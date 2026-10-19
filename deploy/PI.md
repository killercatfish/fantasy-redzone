# Running the board as a service on a Raspberry Pi

The board needs only the Python 3 standard library, so any Pi with Raspberry Pi OS or Ubuntu works
(tested target: a Pi 4). It runs as its own system user, `redzone`, from `/opt/fantasy-redzone`. The
unit can't write anywhere and is capped at 256 MB and half a core, so it sits safely beside other
services. Your ESPN cookies live in `config.json`, owned by `redzone`, mode 600.

A Pi is too slow to run a neural voice itself. Point `kira_voice_url` at a voice server on another
machine, or leave it empty to use the TV browser's voice.

## Install

From the machine that has your filled-in `config.json` (it's copied without ever being world-readable):

```sh
ssh <pi> 'umask 077; cat > ~/redzone-config.json' < config.json
```

On the Pi:

```sh
sudo useradd --system --home-dir /opt/fantasy-redzone --shell /usr/sbin/nologin redzone
sudo git clone https://github.com/killercatfish/fantasy-redzone.git /opt/fantasy-redzone
sudo install -o redzone -g redzone -m 600 ~/redzone-config.json /opt/fantasy-redzone/config.json
rm ~/redzone-config.json
sudo cp /opt/fantasy-redzone/deploy/fantasy-redzone.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now fantasy-redzone
systemctl status fantasy-redzone --no-pager
curl -s localhost:8787/api/board | head -c 300
```

If the Pi runs a firewall (ufw), open the port to your home network, or the TV can't reach the board:

```sh
sudo ufw allow from 192.168.1.0/24 to any port 8787 proto tcp comment 'fantasy-redzone (LAN)'
```

The board is then at `http://<pi-ip>:8787`.

## Update

```sh
ssh <pi> 'sudo git -C /opt/fantasy-redzone pull && sudo systemctl restart fantasy-redzone'
```

## Remove

```sh
ssh <pi> 'sudo systemctl disable --now fantasy-redzone && sudo rm /etc/systemd/system/fantasy-redzone.service \
  && sudo rm -rf /opt/fantasy-redzone && sudo userdel redzone'
```
