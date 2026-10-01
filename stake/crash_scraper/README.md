# Stake Crash Scraper

A read-only Stake Crash data-collection project with two independent collection paths:

1. **Live browser collector** — captures WebSocket/browser observations such as visible bets, cash-outs, player counts and completed rounds.
2. **Official round-history sync** — optional independent service that records completed Crash rounds from Stake's documented Crash History API when you have approved affiliate API access.

It does **not** place bets, cash out, click betting controls, bypass CAPTCHA, spoof fingerprints, rotate proxies, or hide automation.

## Folder structure

```text
crash_scraper/
├── README.md
├── pyproject.toml
├── .env.example
├── .gitignore
├── deploy/
│   ├── README.md
│   └── systemd/
│       ├── stake-crash.service
│       ├── stake-crash-history-sync.service
│       ├── stake-crash-xvfb.service
│       └── stake-crash-vnc.service
├── src/
│   └── stake_crash_scraper/
│       ├── __init__.py
│       ├── __main__.py
│       ├── challenge.py
│       ├── cli.py
│       ├── collector.py
│       ├── config.py
│       ├── continuity.py
│       ├── history_api.py
│       ├── history_daemon.py
│       ├── monitoring.py
│       ├── parser.py
│       ├── round_state.py
│       ├── storage.py
│       └── types.py
└── tests/
    ├── test_challenge.py
    ├── test_continuity.py
    ├── test_monitoring.py
    ├── test_parser.py
    └── test_round_state.py
```

## Live-browser output

### `data/rounds.csv`

One row per completed round observed by the browser, including:

- crash multiplier;
- round ID when exposed;
- timestamps and duration;
- reported player/bet counts when exposed;
- online-player count;
- visible unique bets/players;
- visible cash-out count;
- observed wager/payout totals.

### `data/bet_updates.csv`

Visible bet/cash-out updates:

- time and round linkage;
- bet ID when exposed;
- salted player hash;
- amount/currency;
- cash-out multiplier;
- payout/profit/status when exposed.

### `data/events.jsonl`

Normalized browser event stream for later re-processing.

## Continuity / gap tracking

The browser collector writes:

```text
runtime/
├── status.json
└── gaps.jsonl
```

Every known interruption is recorded instead of silently turning missing observations into zeros. Examples include:

- CAPTCHA/security challenge;
- HTTP 403 block;
- HTTP 429 rate limit;
- browser close/crash.

This is important for ML because a missing live feature is **unknown**, not zero.

## Optional official completed-round history

If you have approved Stake API access, configure:

```text
STAKE_CRASH_API_TOKEN=...
```

Then run:

```bash
stake-crash-history-sync
```

It writes:

```text
data/official_rounds.csv
```

This service is independent of Chromium, so completed crash results can continue being archived while the browser collector is paused by a challenge.

Official-history rows are marked:

```text
source=official_api
live_features_complete=false
```

That flag matters: the history API can recover completed-round results, but it cannot reconstruct live player/bet observations that were never delivered to the browser during an interruption.

## Install

```powershell
cd stake\crash_scraper
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
playwright install chromium
```

## Run live collector

```powershell
stake-crash
```

or:

```powershell
python -m stake_crash_scraper
```

A normal Chromium window opens. Log in manually if required and keep Crash open.

## CAPTCHA / security challenges

If a CAPTCHA or browser-security challenge appears, the live collector:

1. detects it;
2. pauses browser-event processing;
3. records the gap start;
4. brings the browser forward;
5. waits for manual completion;
6. records the gap end and resumes automatically.

It does not auto-solve or bypass the challenge.

If the independent official-history service is configured, it can continue recording completed-round results during the browser gap.

## Server deployment

See `deploy/README.md`.

The systemd setup provides:

- restart after genuine browser/process failure;
- no aggressive retry loop after 403/429;
- persistent data/profile/runtime directories;
- atomic health files;
- optional webhook alerts;
- Xvfb virtual display;
- localhost-only VNC through an SSH tunnel;
- optional independent official-history synchronization.

## Test

```powershell
pytest -q
```
