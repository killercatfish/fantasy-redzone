# Fantasy RedZone

A live ESPN fantasy football board for the TV, like NFL RedZone for your fantasy league.

![The board in demo mode](docs/screenshot.png)

- **Your matchup**, big: live scores, projections that update every 30 seconds as games
  play out, a win-probability tug-of-war, and both lineups side by side in the same
  position order. Each player shows their NFL team and logo. Rows are grey before kickoff,
  white during their game, and blue once it's over, and they flash green when a player scores.
- **Bench watch**: each team's bench points, and how many more points the best possible
  lineup would have scored (counting only finished games).
- **Every NFL game** on the right: scores, quarter and clock, kickoff times, who has the
  ball, and a red box when a team is in the red zone. Games with your starters are outlined.
- **The rest of your league** along the bottom, and a **scoring crawl** across the top with
  touchdowns, big plays, and trash talk.
- **Touchdown takeovers**: when one of your players scores, the screen flashes, an air horn
  plays, and the play is announced. When it's against you, a sad horn plays instead.

Plain Python 3, nothing to install. Arrow keys (or the TV remote's ◀ ▶) switch the featured matchup. Press T for a test scoring call (horn plus voice).

## 1. Try it (no ESPN needed)

    python3 server.py --demo

Open http://localhost:8787. The demo fakes a league and NFL slate and scores every few seconds.

## 2. Set it up for your league

You need Python 3 (already on macOS and Linux; on Windows, install it from python.org and type
`python` wherever this says `python3`) and about 5 minutes.

**a. Get the code and make your config**

    git clone https://github.com/killercatfish/fantasy-redzone.git
    cd fantasy-redzone
    cp config.example.json config.json

**b. Find your league ID.** Open your league on fantasy.espn.com. The URL looks like
`https://fantasy.espn.com/football/league?leagueId=12345678`. Put that number in
`config.json` as `"league_id"`, and set `"season"` to this season's year.

**c. Copy your two ESPN cookies** (only for private leagues; a public league can skip this).
ESPN doesn't give private leagues an API key, so the board signs in with the same two
cookies your browser uses:

1. In Chrome, go to fantasy.espn.com and make sure you're logged in.
2. Open DevTools: **⌥⌘I** on a Mac, **Ctrl+Shift+I** on Windows (or right-click → Inspect).
3. Click the **Application** tab (if you don't see it, click **»** at the top of DevTools).
4. In the left sidebar, open **Cookies** → **https://fantasy.espn.com**.
5. Type `espn_s2` in the filter box. Double-click its **Value**, copy it, and paste it into
   `config.json` as `"espn_s2"`. It's long, about 300 characters.
6. Do the same for `SWID`, and paste it as `"swid"`, including the `{ }` braces.

Firefox: the same cookies are under DevTools → **Storage** → **Cookies**. Safari: turn on
Settings → Advanced → "Show features for web developers" first, then use
Develop → Show Web Inspector → **Storage**.

The cookies last until you log out of ESPN in that browser. If the board ever says "ESPN said 401", copy them again.
`config.json` is your ESPN login, so never share it or commit it (it's already in `.gitignore`).

**d. Pick your team and start it**

    python3 server.py --teams     # lists every team with its id
    # put your id in config.json as "my_team_id"
    python3 server.py

Open http://localhost:8787 on the same computer to check it, then put it on the TV.

## 3. Get it on the TV

The server runs on a laptop, a Pi, or any always-on machine. The TV only displays the page,
at `http://<that-machine's-IP>:8787`.

- **Google TV / Android TV**: cast a Chrome tab to the TV, or install a TV browser app (such as TV Bro) and open the URL.
- **Roku TV** (no real browser): AirPlay your screen from a Mac, or use a Raspberry Pi on HDMI in kiosk mode (works on any TV):

      chromium-browser --kiosk --autoplay-policy=no-user-gesture-required http://<server-ip>:8787

  The autoplay flag lets the air horn work without a click.

On a TV browser without that flag, press OK or click once to turn on sound.

To find the computer's IP: on a Mac, System Settings → Wi-Fi → Details; on Linux, `hostname -I`.
macOS may ask once whether to allow incoming connections for Python; say yes. The board runs
as long as `python3 server.py` is running. To keep it running on a Raspberry Pi as a service
that starts on boot, see `deploy/PI.md`.

## Optional: a real voice for the calls

Without this, the browser's built-in voice reads the scoring calls. Set `kira_voice_url` to a
text-to-speech endpoint that takes `POST {"text": "..."}` and returns a wav. The server forwards each
line there, and if the endpoint fails, the browser voice reads the line instead. For example, point it at a
[Kokoro](https://github.com/thewh1teagle/kokoro-onnx) server on another machine.

## Optional: Home Assistant lights

Set `home_assistant_webhook` to a webhook URL, such as
`http://homeassistant.local:8123/api/webhook/fantasy_td`. The server POSTs JSON
for every big play in your matchup. The payload includes `for_me` (true = your
player, false = your opponent's), `kind` (`td` or `big`), `player`, and `gain`.
Trigger an automation on it, for example flashing the lights blue for you and red for them.

## Notes

- ESPN's fantasy API is unofficial and can change without notice. If the board breaks,
  `fetch_live()` in `server.py` is the only place that talks to ESPN.
- Touchdowns are inferred: a starter jumping 5.5 or more points between polls counts as a TD,
  and 3.5 or more counts as a big play. The default `poll_seconds` is 30.
- `python3 server.py --dump dumps` does one poll, saves ESPN's raw responses plus the parsed
  board to `dumps/`, and exits. Start there if the board shows wrong numbers.
- Win probability is a rough estimate based on projections and how many points are left to play.

## License

MIT. Not affiliated with ESPN.
