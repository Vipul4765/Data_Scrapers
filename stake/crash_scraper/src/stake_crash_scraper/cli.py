from __future__ import annotations

import argparse
import asyncio
import logging
import os
from pathlib import Path

from .collector import collect
from .config import CollectorSettings, DEFAULT_URL


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Read-only Stake Crash real-time data collector")
    parser.add_argument("--url", default=DEFAULT_URL)
    parser.add_argument("--output-dir", type=Path, default=Path("data"))
    parser.add_argument("--profile-dir", type=Path, default=Path(".browser-profile"))
    parser.add_argument("--runtime-dir", type=Path, default=Path("runtime"))
    parser.add_argument("--headless", action="store_true", help="Headed Chromium is the default")
    parser.add_argument("--debug-shapes", action="store_true", help="Print JSON key/type shapes only, never payload values")
    parser.add_argument("--log-level", choices=("DEBUG", "INFO", "WARNING", "ERROR"), default="INFO")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    settings = CollectorSettings(
        url=args.url,
        output_dir=args.output_dir,
        profile_dir=args.profile_dir,
        runtime_dir=args.runtime_dir,
        headless=args.headless,
        debug_shapes=args.debug_shapes,
        alert_webhook_url=os.getenv("STAKE_CRASH_ALERT_WEBHOOK"),
    )
    try:
        asyncio.run(collect(settings))
    except KeyboardInterrupt:
        print("\nStopped.")
