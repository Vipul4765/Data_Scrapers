from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "https://stake.com/casino/games/crash"
DEFAULT_HISTORY_API_URL = "https://api.stake.com/crash/history"


@dataclass(frozen=True, slots=True)
class CollectorSettings:
    url: str = DEFAULT_URL
    output_dir: Path = Path("data")
    profile_dir: Path = Path(".browser-profile")
    runtime_dir: Path = Path("runtime")
    headless: bool = False
    debug_shapes: bool = False
    queue_size: int = 20_000
    max_consecutive_403: int = 3
    online_count_interval_seconds: float = 10.0
    challenge_check_interval_seconds: float = 2.0
    heartbeat_interval_seconds: float = 30.0
    alert_webhook_url: str | None = None
    history_api_token: str | None = None
    history_api_url: str = DEFAULT_HISTORY_API_URL
    history_sync_interval_seconds: float = 60.0
    history_sync_lookback_hours: float = 12.0
    history_sync_page_size: int = 100
    history_sync_max_pages: int = 100
