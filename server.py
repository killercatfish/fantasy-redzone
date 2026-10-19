#!/usr/bin/env python3
"""Fantasy RedZone — live ESPN fantasy football board for a TV.

Polls ESPN's fantasy API, works out who is scoring, and serves a TV-sized
board at http://<this-machine>:8787. Standard library only — no installs.

    python3 server.py            # real league (needs config.json)
    python3 server.py --demo     # fake league with simulated scoring
"""
import argparse, json, math, os, random, sys, threading, time, urllib.parse, urllib.request, urllib.error
from http.server import ThreadingHTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

HERE = Path(__file__).resolve().parent
WEB = HERE / "web"
API = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}"
       "/segments/0/leagues/{league}")

# ESPN lineup slot ids -> label. 20 = bench, 21 = IR (not counted).
SLOTS = {0: "QB", 2: "RB", 4: "WR", 6: "TE", 16: "D/ST", 17: "K", 23: "FLEX",
         20: "BN", 21: "IR", 3: "RB/WR", 5: "WR/TE", 7: "OP"}
BENCH = {20, 21}
# Both sides of a matchup list their players in this order.
SLOT_ORDER = ["QB", "RB", "WR", "TE", "RB/WR", "WR/TE", "FLEX", "OP", "D/ST", "K", "BN", "IR"]
# ESPN proTeamId -> NFL abbreviation (the same ids as ESPN's public NFL scoreboard).
NFL = {1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN", 8: "DET", 9: "GB", 10: "TEN",
       11: "IND", 12: "KC", 13: "LV", 14: "LAR", 15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ",
       21: "PHI", 22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH", 29: "CAR", 30: "JAX",
       33: "BAL", 34: "HOU"}
SCOREBOARD = "https://site.api.espn.com/apis/site/v2/sports/football/nfl/scoreboard"
TD_JUMP = 5.5          # a jump this big between polls is almost always a touchdown
BIG_JUMP = 3.5         # long play / big chunk


# ---------------------------------------------------------------- ESPN fetch
def espn_get(cfg, view_params, extra_headers=None):
    url = API.format(season=cfg["season"], league=cfg["league_id"]) + "?" + view_params
    headers = {"User-Agent": "Mozilla/5.0 fantasy-redzone", "Accept": "application/json"}
    if cfg.get("espn_s2") and cfg.get("swid"):
        headers["Cookie"] = f"espn_s2={cfg['espn_s2']}; SWID={cfg['swid']}"
    headers.update(extra_headers or {})
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=20) as r:
        raw = r.read().decode()
    if cfg.get("dump_dir"):  # --dump: keep raw responses for checking fetch_live() against
        d = Path(cfg["dump_dir"]); d.mkdir(parents=True, exist_ok=True)
        name = "_".join(v.split("=")[1] for v in view_params.split("&") if v.startswith("view="))
        (d / f"{name or 'raw'}.json").write_text(raw)
    return json.loads(raw)


def team_name(t):
    if t.get("name"):
        return t["name"].strip()
    return f"{t.get('location', '')} {t.get('nickname', '')}".strip() or f"Team {t.get('id')}"


def player_stat(ppe, period, source):
    """appliedTotal for this week from player.stats[]; source 0 = actual, 1 = projected."""
    for s in ppe.get("player", {}).get("stats", []) or []:
        if s.get("statSourceId") == source and s.get("scoringPeriodId") == period:
            return float(s.get("appliedTotal") or 0)
    return None


def player_proj(ppe, period):
    return player_stat(ppe, period, 1)


def nfl_scoreboard():
    """This week's NFL games from ESPN's public scoreboard.

    Returns (left, games): left maps proTeamId -> share of that team's game still to play
    (1 = not started, 0 = over or bye); games is one dict per game for the board's game boxes.
    """
    req = urllib.request.Request(SCOREBOARD, headers={"User-Agent": "Mozilla/5.0 fantasy-redzone"})
    with urllib.request.urlopen(req, timeout=10) as r:
        sb = json.loads(r.read().decode())
    left = {int(t["id"]): 0.0 for t in sb.get("week", {}).get("teamsOnBye", [])}
    games = []
    for ev in sb.get("events", []):
        c = ev["competitions"][0]
        st = c["status"]
        state = st["type"]["state"]
        period = st.get("period") or 0
        if state == "pre":
            frac, label = 1.0, ""                     # the page shows the kickoff time from "start"
        elif state == "post":
            frac, label = 0.0, st["type"].get("shortDetail", "Final").upper()
        else:  # in progress: quarters still to come plus what's left on the clock; overtime counts as done
            m, _, sec = (st.get("displayClock") or "0:00").partition(":")
            clock = int(m or 0) + int(sec or 0) / 60 if period <= 4 else 0
            frac = min(max(((4 - period) * 15 + clock) / 60, 0.0), 1.0)
            half = "half" in st["type"].get("shortDetail", "").lower()
            label = "HALF" if half else f"{'OT' if period > 4 else 'Q' + str(period)} {st.get('displayClock', '')}"
        sit = c.get("situation") or {}
        teams = {}
        for t in c["competitors"]:
            left[int(t["id"])] = frac
            teams[t["homeAway"]] = {"id": int(t["id"]), "abbr": t["team"]["abbreviation"],
                                    "score": int(t.get("score") or 0)}
        games.append({"id": ev["id"], "start": ev.get("date"), "state": state, "label": label,
                      "away": teams.get("away"), "home": teams.get("home"),
                      "poss": int(sit["possession"]) if sit.get("possession") else None,
                      "redzone": bool(sit.get("isRedZone"))})
    order = {"in": 0, "pre": 1, "post": 2}
    games.sort(key=lambda g: (order.get(g["state"], 3), g["start"] or ""))
    return left, games


def live_proj(pts, proj, left):
    """Points so far plus the pregame projection scaled by the share of the game left."""
    return None if proj is None else pts + proj * left


def slot_key(p):
    return SLOT_ORDER.index(p["slot"]) if p["slot"] in SLOT_ORDER else len(SLOT_ORDER)


def align(a, b):
    """Give both sides the same starter slots, row for row: an empty lineup slot becomes an "Empty" row."""
    for side, other in ((a, b), (b, a)):
        for slot in {p["slot"] for p in other["players"] if not p["bench"]}:
            short = (sum(p["slot"] == slot for p in other["players"])
                     - sum(p["slot"] == slot for p in side["players"]))
            side["players"] += [{"id": f"empty-{side['id']}-{slot}-{i}", "name": "Empty", "nfl": "", "slot": slot,
                                 "bench": False, "empty": True, "pts": 0.0, "proj": None} for i in range(short)]
    for side in (a, b):
        side["players"].sort(key=slot_key)


def game_states(games, left):
    """proTeamId -> "pre", "in", "post" or "bye", for coloring each player's row."""
    states = {t: "bye" for t in left or {}}
    states.update({t["id"]: g["state"] for g in games for t in (g["away"], g["home"])})
    return states


def ball_states(games):
    """proTeamId -> ("off" or "def", red zone?) for each team in a live game whose possession is known."""
    out = {}
    for g in games:
        if g["state"] == "in" and g["poss"]:
            for t in (g["away"], g["home"]):
                out[t["id"]] = ("off" if t["id"] == g["poss"] else "def", g["redzone"])
    return out


def on_field(ball, pro, dst):
    """"on" while a player's unit is on the field, "rz" when that's an offense in the red zone, else None.

    ESPN doesn't say who is in for each snap, so this goes by possession: offense players (and kickers)
    count as on when their team has the ball, a D/ST when the other team has it.
    """
    side, rz = ball.get(pro, (None, False))
    if side != ("def" if dst else "off"):
        return None
    return "rz" if rz and not dst else "on"


def fetch_live(cfg):
    base = espn_get(cfg, "view=mTeam&view=mSettings&view=mStatus")
    period = base.get("scoringPeriodId") or base.get("status", {}).get("currentMatchupPeriod")
    mp = base.get("status", {}).get("currentMatchupPeriod", period)
    teams = {t["id"]: {"id": t["id"], "name": team_name(t), "abbrev": t.get("abbrev", ""),
                       "logo": t.get("logo"),
                       "record": "{}-{}".format(*(lambda r: (r.get("wins", 0), r.get("losses", 0)))(
                           t.get("record", {}).get("overall", {})))}
             for t in base.get("teams", [])}
    league_name = base.get("settings", {}).get("name", "Fantasy League")

    # Only pull this week's matchups.
    filt = json.dumps({"schedule": {"filterMatchupPeriodIds": {"value": [mp]}}})
    box = espn_get(cfg, f"view=mMatchupScore&view=mBoxscore&view=mScoreboard&scoringPeriodId={period}",
                   {"x-fantasy-filter": filt})

    try:
        left, games = nfl_scoreboard()
    except Exception as e:  # no game clocks: show pregame projections, still live team totals
        print(f"[scoreboard] {e}", file=sys.stderr)
        left, games = None, []

    states = game_states(games, left)
    ball = ball_states(games)

    matchups = []
    for m in box.get("schedule", []):
        if m.get("matchupPeriodId") != mp:
            continue
        sides = []
        for side in ("home", "away"):
            s = m.get(side)
            if not s:
                continue
            roster = (s.get("rosterForCurrentScoringPeriod")
                      or s.get("rosterForMatchupPeriod") or {}).get("entries", [])
            players = []
            for e in roster:
                ppe = e.get("playerPoolEntry", {})
                p = ppe.get("player", {})
                slot = e.get("lineupSlotId")
                pts = ppe.get("appliedStatTotal")
                if pts is None:
                    pts = player_stat(ppe, period, 0)
                pts = float(pts or 0)
                pro = p.get("proTeamId")
                if left is None:
                    proj = player_proj(ppe, period)
                else:
                    proj = live_proj(pts, player_proj(ppe, period), left.get(pro, 1.0))
                players.append({"id": p.get("id"), "name": p.get("fullName", "?"), "nfl": NFL.get(pro, ""),
                                "game": states.get(pro), "field": on_field(ball, pro, p.get("defaultPositionId") == 16),
                                "elig": [s for s in p.get("eligibleSlots", []) if s not in BENCH],
                                "slot": SLOTS.get(slot, str(slot)), "bench": slot in BENCH,
                                "pts": round(pts, 2), "proj": round(proj, 2) if proj is not None else None})
            t = teams.get(s.get("teamId"), {"id": s.get("teamId"), "name": f"Team {s.get('teamId')}"})
            pts = s.get("totalPointsLive", s.get("totalPoints", 0)) or 0
            proj = s.get("totalProjectedPointsLive")
            if proj is None:  # fall back to points + remaining starter projections
                proj = pts + sum(max((p["proj"] or 0) - p["pts"], 0) for p in players if not p["bench"])
            sides.append({**t, "pts": round(float(pts), 2), "proj": round(float(proj), 2),
                          "players": players})
        if len(sides) == 2:
            align(*sides)
            matchups.append(sides)
    slot_counts = base.get("settings", {}).get("rosterSettings", {}).get("lineupSlotCounts", {})
    return {"league": league_name, "week": mp, "matchups": matchups, "games": games, "slot_counts": slot_counts}


# ---------------------------------------------------------------- demo league
class Demo:
    NAMES = ["Avalanche Alley", "Comeback Kids", "Gridiron Gurus", "Touchdown Turtles",
             "Fourth & Long", "Big Play Bandits", "Red Zone Raiders", "Bye Week Blues",
             "Hail Mary Hooligans", "Sunday Scaries"]
    POS = [("QB", 19), ("RB", 13), ("RB", 11), ("WR", 14), ("WR", 12), ("TE", 9),
           ("FLEX", 10), ("D/ST", 7), ("K", 8)]
    FIRST = ["Jalen", "Josh", "Tyreek", "CeeDee", "Bijan", "Saquon", "Amon-Ra", "Puka", "Breece",
             "Travis", "Sam", "Drake", "Malik", "Nico", "Garrett", "Jaxon", "Kyren", "De'Von",
             "Brock", "Mark", "Trey", "Isiah", "Rashee", "Chase", "Jordan", "Derrick", "Ladd"]
    LAST = ["Harper", "Moss", "Rivers", "Banks", "Reed", "Holloway", "Price", "Fields", "Wells",
            "Stone", "Brooks", "Carter", "Hayes", "Lowe", "Pierce", "Shaw", "Vance", "Knox"]

    ELIG = {"QB": [0, 7], "RB": [2, 3, 23, 7], "WR": [4, 3, 5, 23, 7], "TE": [6, 5, 23, 7], "D/ST": [16], "K": [17]}
    SLOT_COUNTS = {"0": 1, "2": 2, "4": 2, "6": 1, "23": 1, "16": 1, "17": 1, "20": 3}

    def __init__(self):
        rnd = random.Random(84)
        self.teams = []
        for i, n in enumerate(self.NAMES):
            players = []
            for j, (pos, proj) in enumerate(self.POS):
                name = (f"{n.split()[0]} D/ST" if pos == "D/ST"
                        else f"{rnd.choice(self.FIRST)} {rnd.choice(self.LAST)}")
                players.append({"id": i * 100 + j, "name": name, "slot": pos, "bench": False,
                                "elig": self.ELIG["RB" if pos == "FLEX" else pos],
                                "nfl": rnd.choice(list(NFL.values())), "pts": 0.0, "proj": float(proj + rnd.randint(-3, 4))})
            for j in range(3):
                players.append({"id": i * 100 + 50 + j, "name": f"{rnd.choice(self.FIRST)} {rnd.choice(self.LAST)}",
                                "slot": "BN", "bench": True, "nfl": rnd.choice(list(NFL.values())),
                                "elig": self.ELIG[rnd.choice(["RB", "WR", "TE"])],
                                "pts": 0.0, "proj": float(rnd.randint(4, 11))})
            self.teams.append({"id": i + 1, "name": n, "abbrev": "".join(w[0] for w in n.split())[:4].upper(),
                               "record": f"{rnd.randint(1, 5)}-{rnd.randint(1, 5)}", "players": players})
        ids = list(NFL)
        rnd.shuffle(ids)
        self.games = []
        for g in range(14):   # 4 live, 6 final, 4 still to come; byes for the rest
            state = "in" if g < 4 else "post" if g < 10 else "pre"
            score = (lambda: rnd.choice([0, 3, 7, 10, 13, 14, 17, 20, 24, 27, 31])) if state != "pre" else (lambda: 0)
            self.games.append({"id": str(g), "state": state, "period": 1 + g % 3, "secs": rnd.randint(60, 840),
                               "start": time.strftime("%Y-%m-%dT%H:%MZ", time.gmtime(time.time() + 3600 * (g - 9))),
                               "away": {"id": ids[2 * g], "abbr": NFL[ids[2 * g]], "score": score()},
                               "home": {"id": ids[2 * g + 1], "abbr": NFL[ids[2 * g + 1]], "score": score()}})
        self.tick = 0

    def games_now(self):
        out = []
        for g in self.games:
            label, poss = ("FINAL" if g["state"] == "post" else ""), None
            if g["state"] == "in":
                g["secs"] -= 25
                if g["secs"] <= 0:
                    g["period"], g["secs"] = min(g["period"] + 1, 4), 900
                if random.random() < 0.08:
                    random.choice((g["away"], g["home"]))["score"] += random.choice([3, 7, 7])
                label = f"Q{g['period']} {g['secs'] // 60}:{g['secs'] % 60:02d}"
                poss = (g["away"] if self.tick % 6 < 3 else g["home"])["id"]
            out.append({**{k: g[k] for k in ("id", "state", "start")}, "label": label,
                        "away": dict(g["away"]), "home": dict(g["home"]), "poss": poss,
                        "redzone": poss is not None and self.tick % 5 == 0})
        return out

    def step(self):
        self.tick += 1
        for t in self.teams:
            for p in t["players"]:
                r = random.random()
                if r < 0.002:
                    p["pts"] += 6 + random.choice([0, 0, 1, 1.2, 2.1])   # touchdown
                elif r < 0.006:
                    p["pts"] += round(random.uniform(1.5, 4.0), 1)        # big chunk play
                elif r < 0.45:
                    p["pts"] += round(random.uniform(0.1, 1.2), 1)        # ordinary stat drip
                p["pts"] = round(p["pts"], 2)

    def snapshot(self):
        self.step()
        games = self.games_now()
        states = game_states(games, {t: 0 for t in NFL})
        abbr_state = {NFL[t]: s for t, s in states.items()}
        ball = {NFL[t]: b for t, b in ball_states(games).items()}
        sides = []
        for t in self.teams:
            starters = [p for p in t["players"] if not p["bench"]]
            pts = sum(p["pts"] for p in starters)
            # In demo, roughly the first half of each roster's projection is "used up" over time.
            frac = min(self.tick / 120, 1)
            proj = pts + sum(max(p["proj"] * (1 - frac), 0) for p in starters)
            sides.append({**{k: t[k] for k in ("id", "name", "abbrev", "record")},
                          "pts": round(pts, 2), "proj": round(proj, 2),
                          "players": [{**p, "proj": round(live_proj(p["pts"], p["proj"], 1 - frac), 2),
                                       "game": abbr_state.get(p["nfl"]),
                                       "field": on_field(ball, p["nfl"], p["slot"] == "D/ST")} for p in t["players"]]})
        return {"league": "Demo League", "week": 7, "games": games, "slot_counts": self.SLOT_COUNTS,
                "matchups": [[sides[i], sides[i + 1]] for i in range(0, len(sides), 2)]}


# ---------------------------------------------------------------- scoring logic
SLOT_IDS = {label: i for i, label in SLOTS.items()}
FLEX_IDS = {3, 5, 7, 23}    # RB/WR, WR/TE, OP, FLEX: filled after the single-position slots


def bench_report(team, slot_counts):
    """Points on the bench, and how many more the best possible lineup would have scored.

    Only finished games count: a starter whose game is still to come or still on keeps their slot,
    and only players whose games are over can be swapped, so the number grows fairly through the
    day instead of flagging every bench player who played before the starters did. Fills the
    single-position slots first with the best eligible player, then the flex slots from whoever
    is left; that's optimal for ordinary QB/RB/WR/TE/FLEX/D/ST/K leagues.
    """
    pool = [p for p in team["players"] if not p.get("empty") and p["slot"] != "IR"]
    done = lambda p: p.get("game") in ("post", "bye", None)    # None: no scoreboard, judge on points so far
    slots = [int(s) for s, n in slot_counts.items() if int(s) not in BENCH for _ in range(n)]
    for p in pool:
        if not p["bench"] and not done(p) and SLOT_IDS.get(p["slot"]) in slots:
            slots.remove(SLOT_IDS[p["slot"]])                 # still playing or to play: keeps the spot
    pool = [p for p in pool if done(p)]
    actual = sum(p["pts"] for p in pool if not p["bench"])
    used, best = set(), 0.0
    slots.sort(key=lambda s: s in FLEX_IDS)
    for slot in slots:
        pick = max((p for p in pool if p["id"] not in used and slot in p.get("elig", ())),
                   key=lambda p: p["pts"], default=None)
        if pick:
            used.add(pick["id"])
            best += pick["pts"]
    team["bench_pts"] = round(sum(p["pts"] for p in pool if p["bench"]), 2)
    team["missed"] = round(max(best - actual, 0.0), 2)
    for p in team["players"]:
        p.pop("elig", None)    # the page doesn't need it

def win_prob(a, b):
    """Rough P(team a wins) from projected finals and remaining uncertainty."""
    rem = max(a["proj"] - a["pts"], 0) + max(b["proj"] - b["pts"], 0)
    sigma = 1.2 * math.sqrt(rem) + 0.75
    z = (a["proj"] - b["proj"]) / sigma
    return 0.5 * (1 + math.erf(z / math.sqrt(2)))


class Board:
    def __init__(self, cfg, demo):
        self.cfg, self.demo = cfg, Demo() if demo else None
        self.state = {"ok": False, "error": "Loading…", "events": []}
        self.prev = {}          # player id -> last points
        self.events = []        # rolling list of scoring events
        self.seq = 0
        self.lock = threading.Lock()

    def poll(self):
        try:
            data = self.demo.snapshot() if self.demo else fetch_live(self.cfg)
        except urllib.error.HTTPError as e:
            msg = {401: "ESPN said 401 — private league: add espn_s2 + swid to config.json",
                   404: "ESPN said 404 — check league_id and season in config.json"}.get(e.code, f"ESPN HTTP {e.code}")
            with self.lock:
                self.state.update(ok=False, error=msg)
            return
        except Exception as e:  # network blips: keep showing last good board
            with self.lock:
                self.state.update(error=f"Can't reach ESPN ({e.__class__.__name__}) — retrying")
            return

        my_id = self.cfg.get("my_team_id")
        new_events = []
        for a, b in data["matchups"]:
            bench_report(a, data["slot_counts"])
            bench_report(b, data["slot_counts"])
            pa = win_prob(a, b)
            a["win_prob"], b["win_prob"] = round(pa, 3), round(1 - pa, 3)
            for side, opp in ((a, b), (b, a)):
                for p in side["players"]:
                    old = self.prev.get(p["id"])
                    self.prev[p["id"]] = p["pts"]
                    if old is None or p["bench"]:
                        continue
                    gain = round(p["pts"] - old, 2)
                    if gain >= BIG_JUMP:
                        self.seq += 1
                        new_events.append({
                            "seq": self.seq, "t": time.time(), "kind": "td" if gain >= TD_JUMP else "big",
                            "player": p["name"], "slot": p["slot"], "gain": gain,
                            "team": side["name"], "team_id": side["id"], "opp_id": opp["id"],
                            "mine": side["id"] == my_id, "against_me": opp["id"] == my_id})
        # Put "my" matchup first.
        if my_id is not None:
            data["matchups"].sort(key=lambda m: 0 if my_id in (m[0]["id"], m[1]["id"]) else 1)

        self.events = (self.events + new_events)[-40:]
        with self.lock:
            self.state = {"ok": True, "error": None, "updated": time.time(), "my_team_id": my_id,
                          "demo": bool(self.demo), **data, "events": self.events}
        for ev in new_events:
            self.notify(ev)

    def notify(self, ev):
        """Optional: ping a Home Assistant webhook so lights/sounds can react."""
        url = self.cfg.get("home_assistant_webhook")
        if not url or not (ev["mine"] or ev["against_me"]):
            return
        body = json.dumps({**ev, "for_me": ev["mine"]}).encode()
        try:
            urllib.request.urlopen(urllib.request.Request(
                url, data=body, headers={"Content-Type": "application/json"}, method="POST"), timeout=5)
        except Exception as e:
            print(f"[home-assistant] webhook failed: {e}", file=sys.stderr)

    def loop(self):
        while True:
            self.poll()
            time.sleep(self.cfg.get("demo_interval", 4) if self.demo else self.cfg.get("poll_seconds", 30))


def make_handler(board):
    class H(SimpleHTTPRequestHandler):
        def __init__(self, *a, **k):
            super().__init__(*a, directory=str(WEB), **k)

        def do_GET(self):
            if self.path.startswith("/api/say"):
                return self.say()
            if self.path.startswith("/api/board"):
                with board.lock:
                    body = json.dumps(board.state).encode()
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return
            return super().do_GET()

        def say(self):
            """Proxy one spoken line to the voice server (kira_voice_url); 503 tells the page to use the browser voice."""
            url = board.cfg.get("kira_voice_url")
            text = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query).get("text", [""])[0][:300]
            if not url or not text:
                self.send_error(503)
                return
            trace = os.environ.get("CLAUDE_CODE_SESSION_ID")
            req = urllib.request.Request(url, data=json.dumps({"text": text}).encode(), method="POST", headers={
                "Content-Type": "application/json", "X-HKOS-Requester": "fantasy-redzone.voice",
                "X-HKOS-Trace": f"claude:{trace}" if trace else "fantasy-redzone:server"})
            try:
                with urllib.request.urlopen(req, timeout=8) as r:
                    wav = r.read()
            except Exception as e:
                print(f"[kira-voice] {e}", file=sys.stderr)
                self.send_error(503)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(wav)))
            self.end_headers()
            self.wfile.write(wav)

        def log_message(self, *a):
            pass
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true", help="simulated league, no ESPN needed")
    ap.add_argument("--teams", action="store_true", help="list team ids in your league and exit")
    ap.add_argument("--port", type=int, default=8787)
    ap.add_argument("--config", default=str(HERE / "config.json"))
    ap.add_argument("--dump", metavar="DIR", help="save raw ESPN responses to DIR (one poll, then exit)")
    args = ap.parse_args()

    cfg = {"season": 2026, "poll_seconds": 30}
    cfg_path = Path(args.config)
    if cfg_path.exists():
        cfg.update(json.loads(cfg_path.read_text()))
    elif not args.demo:
        sys.exit(f"No {cfg_path.name} found. Copy config.example.json to config.json and fill it in, "
                 "or run with --demo.")
    if args.teams:
        base = espn_get(cfg, "view=mTeam")
        for t in base.get("teams", []):
            print(f"  {t['id']:>3}  {team_name(t)}")
        print('Put your number in config.json as "my_team_id".')
        return
    if args.dump:
        cfg["dump_dir"] = args.dump
        data = fetch_live(cfg)
        (Path(args.dump) / "parsed.json").write_text(json.dumps(data, indent=2))
        for a, b in data["matchups"]:
            print(f"  {a['name']} {a['pts']} ({a['proj']})  vs  {b['name']} {b['pts']} ({b['proj']})")
        print(f"Raw + parsed responses saved in {args.dump}/")
        return
    if args.demo and cfg.get("my_team_id") is None:
        cfg["my_team_id"] = 1

    board = Board(cfg, args.demo)
    threading.Thread(target=board.loop, daemon=True).start()
    srv = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(board))
    print(f"Fantasy RedZone on http://localhost:{args.port}  ({'DEMO' if args.demo else 'league ' + str(cfg.get('league_id'))})")
    srv.serve_forever()


if __name__ == "__main__":
    main()
