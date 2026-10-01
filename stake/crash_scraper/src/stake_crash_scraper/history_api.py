from __future__ import annotations

import asyncio
import csv
import json
import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)


def _utc_from_ms(value: int | float | None) -> str | None:
    if value is None:
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1000.0, tz=timezone.utc).isoformat(timespec="milliseconds")
    except (TypeError, ValueError, OSError):
        return None


def normalize_history_round(raw: dict[str, Any]) -> dict[str, Any] | None:
    round_id = raw.get("id")
    crashpoint = raw.get("crashpoint")
    created_at = raw.get("createdAt")

    if not round_id or crashpoint is None or created_at is None:
        return None

    try:
        crashpoint = float(crashpoint)
        created_at = int(created_at)
    except (TypeError, ValueError):
        return None

    return {
        "round_id": str(round_id),
        "iid": raw.get("iid"),
        "created_at_ms": created_at,
        "created_at_utc": _utc_from_ms(created_at),
        "crashpoint": crashpoint,
        "numbers_json": json.dumps(raw.get("numbers"), separators=(",", ":")) if raw.get("numbers") is not None else None,
        "source": "official_api",
        "live_features_complete": False,
    }


@dataclass(slots=True)
class HistorySyncStats:
    fetched: int = 0
    written: int = 0
    oldest_created_at_ms: int | None = None


class StakeCrashHistoryClient:
    def __init__(
        self,
        *,
        token: str,
        output_path: Path,
        endpoint: str,
        page_size: int = 50,
        page_delay_seconds: float = 1.0,
        timeout_seconds: float = 10.0,
    ) -> None:
        self.token = token
        self.output_path = output_path
        self.endpoint = endpoint
        self.page_size = page_size
        self.page_delay_seconds = page_delay_seconds
        self.timeout_seconds = timeout_seconds
        self._seen_ids: set[str] = set()
        self._load_seen_ids()

    def _load_seen_ids(self) -> None:
        if not self.output_path.exists():
            return
        try:
            with self.output_path.open("r", newline="", encoding="utf-8") as f:
                for row in csv.DictReader(f):
                    if row.get("round_id"):
                        self._seen_ids.add(row["round_id"])
        except Exception as exc:
            log.warning("Could not preload official history IDs: %s", exc)

    def _post(self, limit: int, offset: int) -> list[dict[str, Any]]:
        body = json.dumps({"limit": limit, "offset": offset}).encode("utf-8")
        request = urllib.request.Request(
            self.endpoint,
            data=body,
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "x-access-token": self.token,
                "User-Agent": "stake-crash-collector/0.1",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8"))
        rounds = payload.get("rounds", [])
        return rounds if isinstance(rounds, list) else []

    async def sync_since(self, since_ms: int, *, max_pages: int) -> HistorySyncStats:
        stats = HistorySyncStats()
        self.output_path.parent.mkdir(parents=True, exist_ok=True)

        fields = [
            "round_id",
            "iid",
            "created_at_ms",
            "created_at_utc",
            "crashpoint",
            "numbers_json",
            "source",
            "live_features_complete",
        ]
        exists = self.output_path.exists() and self.output_path.stat().st_size > 0

        with self.output_path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=fields)
            if not exists:
                writer.writeheader()
                f.flush()

            for page in range(max_pages):
                offset = page * self.page_size
                try:
                    rounds = await asyncio.to_thread(self._post, self.page_size, offset)
                except urllib.error.HTTPError as exc:
                    if exc.code in {401, 403}:
                        raise PermissionError(
                            "Official Crash History API denied access. Approved affiliate API access is required."
                        ) from exc
                    if exc.code == 429:
                        raise RuntimeError("Official Crash History API rate limited the sync") from exc
                    raise

                if not rounds:
                    break

                stats.fetched += len(rounds)
                oldest_in_page: int | None = None

                for raw in rounds:
                    if not isinstance(raw, dict):
                        continue
                    row = normalize_history_round(raw)
                    if row is None:
                        continue
                    created_at_ms = int(row["created_at_ms"])
                    oldest_in_page = created_at_ms if oldest_in_page is None else min(oldest_in_page, created_at_ms)

                    if row["round_id"] not in self._seen_ids:
                        writer.writerow(row)
                        f.flush()
                        self._seen_ids.add(row["round_id"])
                        stats.written += 1

                if oldest_in_page is not None:
                    stats.oldest_created_at_ms = (
                        oldest_in_page
                        if stats.oldest_created_at_ms is None
                        else min(stats.oldest_created_at_ms, oldest_in_page)
                    )
                    if oldest_in_page <= since_ms:
                        break

                f.flush()
                os.fsync(f.fileno())

                if len(rounds) < self.page_size:
                    break

                await asyncio.sleep(self.page_delay_seconds)

        return stats
