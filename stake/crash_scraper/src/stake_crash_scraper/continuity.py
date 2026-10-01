from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def utc_to_ms(value: str) -> int | None:
    try:
        return int(datetime.fromisoformat(value.replace("Z", "+00:00")).timestamp() * 1000)
    except (TypeError, ValueError):
        return None


@dataclass(slots=True)
class Gap:
    reason: str
    started_at_utc: str
    started_at_ms: int


class ContinuityJournal:
    def __init__(self, runtime_dir: Path) -> None:
        self.runtime_dir = runtime_dir
        self.path = runtime_dir / "gaps.jsonl"
        self.current: Gap | None = None
        self.runtime_dir.mkdir(parents=True, exist_ok=True)

    def _append(self, payload: dict[str, Any]) -> None:
        with self.path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(payload, separators=(",", ":")) + "\n")
            f.flush()
            os.fsync(f.fileno())

    def start_gap(self, reason: str, *, started_at_utc: str | None = None) -> Gap:
        if self.current is not None:
            return self.current
        started_at_utc = started_at_utc or utc_now()
        started_at_ms = utc_to_ms(started_at_utc)
        if started_at_ms is None:
            started_at_ms = int(datetime.now(timezone.utc).timestamp() * 1000)
        self.current = Gap(reason=reason, started_at_utc=started_at_utc, started_at_ms=started_at_ms)
        self._append({
            "event": "gap_started",
            "reason": reason,
            "started_at_utc": started_at_utc,
        })
        return self.current

    def end_gap(self, *, backfill_status: str | None = None, backfill_written: int | None = None) -> Gap | None:
        gap = self.current
        if gap is None:
            return None
        ended_at_utc = utc_now()
        ended_at_ms = utc_to_ms(ended_at_utc) or gap.started_at_ms
        self._append({
            "event": "gap_ended",
            "reason": gap.reason,
            "started_at_utc": gap.started_at_utc,
            "ended_at_utc": ended_at_utc,
            "duration_seconds": max(0.0, (ended_at_ms - gap.started_at_ms) / 1000.0),
            "backfill_status": backfill_status,
            "backfill_written": backfill_written,
        })
        self.current = None
        return gap

    def record(self, event: str, **fields: Any) -> None:
        self._append({
            "event": event,
            "observed_at_utc": utc_now(),
            **fields,
        })

    def previous_status_updated_at(self) -> str | None:
        status_path = self.runtime_dir / "status.json"
        if not status_path.exists():
            return None
        try:
            payload = json.loads(status_path.read_text(encoding="utf-8"))
            value = payload.get("updated_at_utc")
            return str(value) if value else None
        except Exception:
            return None
