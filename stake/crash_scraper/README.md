# Stake Crash Scraper

A small, read-only real-time data collector for the Stake Crash page.

It observes WebSocket messages that the normal browser session already receives and writes normalized datasets for offline research. It does **not** place bets, cash out, click betting controls, bypass CAPTCHA, spoof fingerprints, rotate proxies, or hide automation.

## Folder structure

```text
crash_scraper/
├── README.md
├── pyproject.toml
├── .gitignore
├── src/
│   └── stake_crash_scraper/
│       ├── __init__.py
│       ├── __main__.py
│       ├── challenge.py
│       ├── cli.py
│       ├── config.py
│       ├── collector.py
│       ├── parser.py
│       ├── round_state.py
│       ├── storage.py
│       └── types.py
└── tests/
    ├── test_challenge.py
    ├── test_parser.py
    └── test_round_state.py
```

## What is collected

### `data/rounds.csv`

One row per observed completed round, including:

- crash multiplier
- round ID when exposed
- timestamps and round duration
- reported player/bet counts when exposed
- online-player count
- unique visible bets/players
- visible cash-out count
- observed wager and payout totals

### `data/bet_updates.csv`

Normalized visible bet/cash-out updates:

- time and round linkage
- bet ID when exposed
- salted player hash
- amount/currency
- cash-out multiplier
- payout/profit/status when exposed

### `data/events.jsonl`

Normalized event stream for later re-processing.

Player identifiers are salted and hashed locally. The salt lives in `data/.player_salt`, which is ignored by Git.

## Install

```powershell
cd stake\crash_scraper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
playwright install chromium
```

## Run

```powershell
stake-crash
```

or:

```powershell
python -m stake_crash_scraper
```

A normal Chromium window opens. Log in manually if required and keep Crash open.

### CAPTCHA / security challenge handling

If the site presents a CAPTCHA or browser-security challenge, the collector:

1. detects the challenge;
2. pauses WebSocket data processing;
3. brings the browser window forward;
4. waits while **you solve the challenge manually**;
5. resumes automatically when the normal page is back.

It never attempts to solve, bypass, hide, or defeat the challenge and it does not refresh-loop against the page.

## Schema diagnostics

```powershell
stake-crash --debug-shapes
```

This prints only JSON keys and value types, never raw payload values or credentials.

## Reliability / rate handling

- persistent browser profile instead of repeated logins;
- manual login and manual security-challenge completion;
- automatic pause/resume around challenges;
- no automated betting actions;
- no page-refresh loop;
- stops on HTTP 429;
- counts only document/XHR/fetch 403s for blocking logic;
- stops after repeated relevant HTTP 403 responses;
- online-player text sampled only every 10 seconds;
- bounded WebSocket queue;
- graceful shutdown with flushed CSV/JSONL files.

## Ubuntu server / overnight operation

Production-style deployment files are under:

```text
deploy/
├── README.md
└── systemd/
    ├── stake-crash.service
    ├── stake-crash-xvfb.service
    └── stake-crash-vnc.service
```

The server setup adds:

- systemd restart on genuine process/browser failure;
- no restart loop after HTTP 429 or repeated HTTP 403;
- persistent data and browser-profile directories;
- atomic `runtime/status.json` heartbeat/status;
- optional operational webhook alerts;
- Xvfb virtual display;
- localhost-only VNC access through an SSH tunnel for manual security challenges.

A challenge can leave the collector safely paused overnight. It does not lose or corrupt already-flushed rows, and it resumes automatically after the challenge is manually cleared.

See `deploy/README.md` for installation and remote-access commands.

## Test

```powershell
pytest -q
```

The parser/state tests use synthetic data because the site's internal live schema can change without notice.
