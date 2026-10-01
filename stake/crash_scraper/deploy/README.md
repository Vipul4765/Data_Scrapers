# Ubuntu server deployment

This deployment keeps the browser session persistent and makes the collector recover from ordinary process/browser crashes without creating a restart loop against site blocks.

## Important behavior

- application crash -> systemd restarts after 20 seconds;
- HTTP 429 -> collector stops cleanly and stays stopped;
- repeated HTTP 403 -> collector stops cleanly and stays stopped;
- CAPTCHA/security challenge -> collector stays alive but pauses data processing;
- manual challenge cleared -> collection resumes automatically;
- runtime health is written atomically to `/var/lib/stake-crash/runtime/status.json`;
- optional webhook alerts can notify you when a challenge/block occurs.

A CAPTCHA is deliberately **not** auto-solved. For unattended servers, use remote desktop access when a challenge requires a human.



## Best unattended option

If you are eligible for Stake's official API access, prefer the documented Crash History API over browser scraping for server-side collection. It removes browser/CAPTCHA dependency. Stake currently documents Crash History for approved affiliates.

## Install

Example Ubuntu packages:

```bash
sudo apt update
sudo apt install -y python3-venv xvfb x11vnc
```

Install the project to `/opt/stake-crash`:

```bash
sudo mkdir -p /opt/stake-crash /var/lib/stake-crash/{data,profile,runtime}
sudo chown -R "$USER":"$USER" /opt/stake-crash /var/lib/stake-crash
# copy/checkout crash_scraper contents into /opt/stake-crash
cd /opt/stake-crash
python3 -m venv .venv
./.venv/bin/pip install -e .
./.venv/bin/playwright install chromium
```

Copy units:

```bash
sudo cp deploy/systemd/stake-crash-xvfb.service /etc/systemd/system/
sudo cp deploy/systemd/stake-crash.service /etc/systemd/system/
sudo cp deploy/systemd/stake-crash-vnc.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stake-crash-xvfb stake-crash stake-crash-vnc
```

## Optional alerts

Copy the example environment file and put your own webhook endpoint there:

```bash
cp .env.example .env
chmod 600 .env
```

The collector sends only operational event text, not browser cookies or captured gambling payloads.

## Health check

```bash
cat /var/lib/stake-crash/runtime/status.json
systemctl status stake-crash
journalctl -u stake-crash -f
```

Expected states include:

- `STARTING`
- `RUNNING`
- `CHALLENGE`
- `RATE_LIMITED`
- `BLOCKED`
- `STOPPED`

## Manual challenge from another computer

The included VNC service listens on localhost only. Do **not** expose port 5900 publicly.

From your computer:

```bash
ssh -L 5900:127.0.0.1:5900 user@your-server
```

Then connect your VNC client to `127.0.0.1:5900`. Solve the security challenge manually in the existing browser window. The collector detects that the challenge cleared and resumes automatically.

For stronger remote-desktop authentication, replace the minimal local-only VNC unit with your normal secured remote-access stack.
