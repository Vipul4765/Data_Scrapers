from __future__ import annotations

import asyncio
import json
import os
import urllib.request
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


@dataclass(slots=True)
class RuntimeMonitor:
    runtime_dir: Path
    state: str = "STARTING"
    detail: str | None = None
    _extra: dict[str, Any] = field(default_factory=dict)

    @property
    def status_path(self) -> Path:
        return self.runtime_dir / "status.json"

    def set_state(self, state: str, detail: str | None = None, **extra: Any) -> None:
        self.state = state
        self.detail = detail
        self._extra.update(extra)
        self.write()

    def update(self, **extra: Any) -> None:
        self._extra.update(extra)
        self.write()

    def write(self) -> None:
        self.runtime_dir.mkdir(parents=True, exist_ok=True)
        payload = {
            "state": self.state,
            "detail": self.detail,
            "pid": os.getpid(),
            "updated_at_utc": utc_now(),
            **self._extra,
        }
        tmp = self.status_path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
        os.replace(tmp, self.status_path)


@dataclass(frozen=True, slots=True)
class WebhookNotifier:
    url: str | None
    timeout_seconds: float = 5.0

    async def send(self, event: str, message: str) -> None:
        if not self.url:
            return
        payload = {
            "event": event,
            "message": message,
            "timestamp_utc": utc_now(),
        }
        await asyncio.to_thread(self._send_sync, payload)

    def _send_sync(self, payload: dict[str, Any]) -> None:
        assert self.url is not None
        body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        request = urllib.request.Request(
            self.url,
            data=body,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "stake-crash-collector/0.1",
            },
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=self.timeout_seconds) as response:
            response.read(1)
