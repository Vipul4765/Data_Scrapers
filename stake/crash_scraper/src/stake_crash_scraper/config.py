from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DEFAULT_URL = "https://stake.com/casino/games/crash"


@dataclass(frozen=True, slots=True)
class CollectorSettings:
    url: str = DEFAULT_URL
    output_dir: Path = Path("data")
    profile_dir: Path = Path(".browser-profile")
    headless: bool = False
    debug_shapes: bool = False
    queue_size: int = 20_000
    max_consecutive_403: int = 3
    online_count_interval_seconds: float = 10.0
