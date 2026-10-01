from __future__ import annotations

import argparse
import asyncio
import logging
import os
import time
from pathlib import Path

from .history_api import StakeCrashHistoryClient
from .monitoring import RuntimeMonitor


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Official Stake Crash round-history synchronization")
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime/history"))
    parser.add_argument("--interval-seconds", type=float, default=60.0)
    parser.add_argument("--startup-lookback-hours", type=float, default=12.0)
    parser.add_argument("--page-size", type=int, default=100)
    parser.add_argument("--max-startup-pages", type=int, default=100)
    parser.add_argument("--max-periodic-pages", type=int, default=5)
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


async def run() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    log = logging.getLogger(__name__)

    token = os.getenv("STAKE_CRASH_API_TOKEN")
    if not token:
        raise SystemExit("STAKE_CRASH_API_TOKEN is required and must belong to approved API access.")

    endpoint = os.getenv("STAKE_CRASH_HISTORY_API_URL", "https://api.stake.com/crash/history")
    args.output_dir.mkdir(parents=True, exist_ok=True)
    monitor = RuntimeMonitor(args.runtime_dir)

    client = StakeCrashHistoryClient(
        token=token,
        output_path=args.output_dir / "official_rounds.csv",
        endpoint=endpoint,
        page_size=args.page_size,
    )

    startup_since_ms = int((time.time() - args.startup_lookback_hours * 3600.0) * 1000)
    monitor.set_state("STARTING", "official Crash history sync")

    try:
        stats = await client.sync_since(startup_since_ms, max_pages=args.max_startup_pages)
        monitor.set_state(
            "RUNNING",
            "official Crash history sync active",
            last_sync_written=stats.written,
            last_sync_fetched=stats.fetched,
        )
        log.info("Startup history sync: fetched=%d written=%d", stats.fetched, stats.written)
    except PermissionError as exc:
        monitor.set_state("DENIED", str(exc))
        raise SystemExit(str(exc)) from exc

    backoff = args.interval_seconds
    while True:
        await asyncio.sleep(backoff)
        since_ms = int((time.time() - max(15.0 * 60.0, args.interval_seconds * 5.0)) * 1000)
        try:
            stats = await client.sync_since(since_ms, max_pages=args.max_periodic_pages)
            monitor.set_state(
                "RUNNING",
                "official Crash history sync active",
                last_sync_written=stats.written,
                last_sync_fetched=stats.fetched,
            )
            log.info("History sync: fetched=%d written=%d", stats.fetched, stats.written)
            backoff = args.interval_seconds
        except PermissionError as exc:
            monitor.set_state("DENIED", str(exc))
            raise SystemExit(str(exc)) from exc
        except Exception as exc:
            log.warning("History sync failed: %s", exc)
            monitor.set_state("DEGRADED", str(exc))
            backoff = min(max(args.interval_seconds, backoff * 2.0), 900.0)


def main() -> None:
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        pass
