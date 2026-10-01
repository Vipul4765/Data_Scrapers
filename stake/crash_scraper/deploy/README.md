# Ubuntu server deployment

The server deployment deliberately separates **live browser collection** from **official completed-round history**.

This means a browser challenge can pause live player/bet observations without necessarily stopping the independent round-history archive.

## Services

```text
stake-crash-xvfb.service
    virtual display for Chromium

stake-crash.service
    live browser/WebSocket collector

stake-crash-vnc.service
    localhost-only VNC for manual browser access

stake-crash-history-sync.service
    optional independent official Crash History API sync
```

## Live collector behavior

- application/browser crash -> systemd restarts after 20 seconds;
- HTTP 429 -> live collector stops cleanly instead of hammering;
- repeated HTTP 403 -> live collector stops cleanly;
- CAPTCHA/security challenge -> process stays alive and browser-event collection pauses;
- challenge cleared manually -> live collection resumes automatically;
- all known gaps are appended to `/var/lib/stake-crash/runtime/gaps.jsonl`;
- live health is written atomically to `/var/lib/stake-crash/runtime/status.json`.

## Independent history service

Stake documents a Crash History API for approved affiliates. When a valid approved API token is configured, the separate history service writes:

```text
/var/lib/stake-crash/data/official_rounds.csv
```

It is not dependent on Chromium/Xvfb/VNC.

This is the preferred backup for completed-round outcomes during browser downtime.

It does **not** recover player/bet-level live observations that were unavailable during the gap.

## Install packages

```bash
sudo apt update
sudo apt install -y python3-venv xvfb x11vnc
```

## Install project

```bash
sudo mkdir -p /opt/stake-crash /var/lib/stake-crash/{data,profile,runtime}
sudo chown -R "$USER":"$USER" /opt/stake-crash /var/lib/stake-crash

# copy/checkout crash_scraper contents into /opt/stake-crash
cd /opt/stake-crash
python3 -m venv .venv
./.venv/bin/pip install -e .
./.venv/bin/playwright install chromium
```

## Environment

```bash
cp .env.example .env
chmod 600 .env
```

Optional alert endpoint:

```text
STAKE_CRASH_ALERT_WEBHOOK=
```

For the official history service, only if you have approved API access:

```text
STAKE_CRASH_API_TOKEN=
STAKE_CRASH_HISTORY_API_URL=https://api.stake.com/crash/history
```

Never commit the real token.

## Install systemd units

```bash
sudo cp deploy/systemd/stake-crash-xvfb.service /etc/systemd/system/
sudo cp deploy/systemd/stake-crash.service /etc/systemd/system/
sudo cp deploy/systemd/stake-crash-vnc.service /etc/systemd/system/
sudo cp deploy/systemd/stake-crash-history-sync.service /etc/systemd/system/
sudo systemctl daemon-reload
```

Start the live collector:

```bash
sudo systemctl enable --now stake-crash-xvfb stake-crash stake-crash-vnc
```

If approved API access is configured, also start:

```bash
sudo systemctl enable --now stake-crash-history-sync
```

## Health

Live browser collector:

```bash
cat /var/lib/stake-crash/runtime/status.json
systemctl status stake-crash
journalctl -u stake-crash -f
```

Official history sync:

```bash
cat /var/lib/stake-crash/runtime/history/status.json
systemctl status stake-crash-history-sync
journalctl -u stake-crash-history-sync -f
```

Expected live collector states include:

- `STARTING`
- `RUNNING`
- `CHALLENGE`
- `RATE_LIMITED`
- `BLOCKED`
- `STOPPED`

History service states include:

- `STARTING`
- `RUNNING`
- `DEGRADED`
- `DENIED`

## Manual challenge from another computer

The VNC service listens on localhost only. Do not expose VNC publicly.

From your computer:

```bash
ssh -L 5900:127.0.0.1:5900 user@your-server
```

Then connect a VNC client to:

```text
127.0.0.1:5900
```

Solve the security challenge in the existing browser window. The live collector resumes automatically afterward.

## Data-quality rule

For analysis/ML:

- browser round with live observations -> live features may be usable;
- official-history-only round -> crash result is available, but `live_features_complete=false`;
- any interval in `gaps.jsonl` -> never silently fill missing player/bet values with zero.
