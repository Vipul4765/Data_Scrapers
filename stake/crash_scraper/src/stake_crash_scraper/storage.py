from __future__ import annotations

import csv
import json
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, TextIO

from .types import NormalizedEvent

BET_FIELDS = [
    "observed_at_utc",
    "local_round_uid",
    "round_id",
    "bet_key",
    "bet_id",
    "player_hash",
    "amount",
    "currency",
    "cashout_multiplier",
    "payout",
    "profit",
    "status",
]

ROUND_FIELDS = [
    "local_round_uid",
    "round_id",
    "started_at_utc",
    "ended_at_utc",
    "crashpoint",
    "reported_player_count",
    "reported_bet_count",
    "online_count",
    "observed_unique_bets",
    "observed_unique_players",
    "observed_cashouts",
    "observed_wager_total",
    "observed_payout_total",
    "round_duration_ms",
]


class CsvAppender:
    def __init__(self, path: Path, fields: list[str]) -> None:
        self.path = path
        self.fields = fields
        self.handle: TextIO | None = None
        self.writer: csv.DictWriter[str] | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        existed = self.path.exists() and self.path.stat().st_size > 0
        self.handle = self.path.open("a", newline="", encoding="utf-8")
        self.writer = csv.DictWriter(self.handle, fieldnames=self.fields, extrasaction="ignore")
        if not existed:
            self.writer.writeheader()
            self.sync()

    def append(self, row: dict[str, Any]) -> None:
        if self.writer is None or self.handle is None:
            raise RuntimeError("CsvAppender is not open")
        self.writer.writerow({field: row.get(field) for field in self.fields})
        self.handle.flush()

    def sync(self) -> None:
        if self.handle is None:
            return
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self) -> None:
        if self.handle is not None:
            self.handle.flush()
            self.handle.close()
            self.handle = None
            self.writer = None


class JsonlAppender:
    def __init__(self, path: Path) -> None:
        self.path = path
        self.handle: TextIO | None = None

    def open(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a", encoding="utf-8")

    def append(self, row: dict[str, Any]) -> None:
        if self.handle is None:
            raise RuntimeError("JsonlAppender is not open")
        self.handle.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
        self.handle.flush()

    def sync(self) -> None:
        if self.handle is None:
            return
        self.handle.flush()
        os.fsync(self.handle.fileno())

    def close(self) -> None:
        if self.handle is not None:
            self.handle.flush()
            self.handle.close()
            self.handle = None


def load_or_create_player_salt(output_dir: Path) -> bytes:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / ".player_salt"
    if path.exists():
        raw = path.read_bytes()
        if len(raw) >= 16:
            return raw
    raw = os.urandom(32)
    path.write_bytes(raw)
    return raw


@dataclass(slots=True)
class DatasetWriter:
    output_dir: Path
    rounds: CsvAppender = field(init=False)
    bets: CsvAppender = field(init=False)
    events: JsonlAppender = field(init=False)

    def __post_init__(self) -> None:
        self.rounds = CsvAppender(self.output_dir / "rounds.csv", ROUND_FIELDS)
        self.bets = CsvAppender(self.output_dir / "bet_updates.csv", BET_FIELDS)
        self.events = JsonlAppender(self.output_dir / "events.jsonl")

    def open(self) -> None:
        self.rounds.open()
        self.bets.open()
        self.events.open()

    def write_event(self, event: NormalizedEvent) -> None:
        self.events.append(event.as_dict())

    def write_bet(self, row: dict[str, Any]) -> None:
        self.bets.append(row)

    def write_round(self, row: dict[str, Any]) -> None:
        self.rounds.append(row)
        self.rounds.sync()
        self.events.sync()

    def close(self) -> None:
        self.events.close()
        self.bets.close()
        self.rounds.close()
