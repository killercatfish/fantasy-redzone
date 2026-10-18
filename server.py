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
                proj = player_proj(ppe, period)
                players.append({"id": p.get("id"), "name": p.get("fullName", "?"),
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
            matchups.append(sides)
    return {"league": league_name, "week": mp, "matchups": matchups}


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

    def __init__(self):
        rnd = random.Random(84)
        self.teams = []
        for i, n in enumerate(self.NAMES):
            players = []
            for j, (pos, proj) in enumerate(self.POS):
                name = (f"{n.split()[0]} D/ST" if pos == "D/ST"
                        else f"{rnd.choice(self.FIRST)} {rnd.choice(self.LAST)}")
                players.append({"id": i * 100 + j, "name": name, "slot": pos, "bench": False,
                                "pts": 0.0, "proj": float(proj + rnd.randint(-3, 4))})
            for j in range(3):
                players.append({"id": i * 100 + 50 + j, "name": f"{rnd.choice(self.FIRST)} {rnd.choice(self.LAST)}",
                                "slot": "BN", "bench": True, "pts": 0.0, "proj": float(rnd.randint(4, 11))})
            self.teams.append({"id": i + 1, "name": n, "abbrev": "".join(w[0] for w in n.split())[:4].upper(),
                               "record": f"{rnd.randint(1, 5)}-{rnd.randint(1, 5)}", "players": players})
        self.tick = 0

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
        sides = []
        for t in self.teams:
            starters = [p for p in t["players"] if not p["bench"]]
            pts = sum(p["pts"] for p in starters)
            # In demo, roughly the first half of each roster's projection is "used up" over time.
            frac = min(self.tick / 120, 1)
            proj = pts + sum(max(p["proj"] * (1 - frac), 0) for p in starters)
            sides.append({**{k: t[k] for k in ("id", "name", "abbrev", "record")},
                          "pts": round(pts, 2), "proj": round(proj, 2),
                          "players": [dict(p) for p in t["players"]]})
        return {"league": "Demo League", "week": 7,
                "matchups": [[sides[i], sides[i + 1]] for i in range(0, len(sides), 2)]}


# ---------------------------------------------------------------- scoring logic
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
