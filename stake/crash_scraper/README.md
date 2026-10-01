# Stake Crash Scraper

A small, read-only real-time data collector for the Stake Crash page.

It observes WebSocket messages that the normal browser session already receives and writes normalized datasets for offline research. It does **not** place bets, cash out, click betting controls, bypass CAPTCHA, spoof fingerprints, rotate proxies, or hide automation.

Stake currently describes Crash as real-time multiplayer with a live leaderboard showing active bets and successful cash-outs. The exact internal WebSocket schema is not a public contract, so the parser is deliberately isolated and covered by tests.

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
│       ├── cli.py
│       ├── config.py
│       ├── collector.py
│       ├── parser.py
│       ├── round_state.py
│       ├── storage.py
│       └── types.py
└── tests/
    ├── test_parser.py
    └── test_round_state.py
```

## What is collected

### `data/rounds.csv`
One row per observed completed round:

- `local_round_uid`
- `round_id` when exposed
- `started_at_utc`
- `ended_at_utc`
- `crashpoint`
- `reported_player_count`
- `reported_bet_count`
- `online_count`
- `observed_unique_bets`
- `observed_unique_players`
- `observed_cashouts`
- `observed_wager_total`
- `observed_payout_total`
- `round_duration_ms`

### `data/bet_updates.csv`
Normalized visible bet/cash-out updates:

- time
- round linkage
- bet ID when exposed
- hashed player identifier
- amount
- currency
- cash-out multiplier
- payout
- profit/status when exposed

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

A normal Chromium window opens. Log in manually if the page requires it and leave the Crash page open.

For schema diagnostics:

```powershell
stake-crash --debug-shapes
```

`--debug-shapes` prints only JSON keys and value types, not payload values or credentials.

## Safety / rate handling

- persistent browser profile instead of repeated logins;
- no automated betting actions;
- no page-refresh loop;
- stops on HTTP 429;
- stops after repeated HTTP 403 responses;
- online-player text is sampled only every 10 seconds;
- bounded in-memory WebSocket queue;
- graceful shutdown with flushed CSV/JSONL files.

## Test

```powershell
pytest -q
```

The parser tests use synthetic payloads because Stake's internal live schema can change without notice.
