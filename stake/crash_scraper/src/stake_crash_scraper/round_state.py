from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from .types import NormalizedEvent


@dataclass(slots=True)
class BetState:
    bet_id: str | None = None
    player_hash: str | None = None
    amount: float | None = None
    currency: str | None = None
    cashout_multiplier: float | None = None
    payout: float | None = None
    profit: float | None = None
    status: str | None = None

    def update(self, event: NormalizedEvent) -> None:
        for name in (
            "bet_id", "player_hash", "amount", "currency", "cashout_multiplier",
            "payout", "profit", "status",
        ):
            value = getattr(event, name)
            if value is not None:
                setattr(self, name, value)


@dataclass(slots=True)
class RoundState:
    local_round_uid: str = field(default_factory=lambda: uuid.uuid4().hex)
    round_id: str | None = None
    started_at_utc: str | None = None
    started_monotonic: float | None = None
    reported_player_count: int | None = None
    reported_bet_count: int | None = None
    online_count: int | None = None
    bets: dict[str, BetState] = field(default_factory=dict)

    def _ensure_started(self, event: NormalizedEvent) -> None:
        if self.started_at_utc is None:
            self.started_at_utc = event.observed_at_utc
            self.started_monotonic = time.monotonic()
        if event.round_id and not self.round_id:
            self.round_id = event.round_id

    def _bet_key(self, event: NormalizedEvent) -> str:
        if event.bet_id:
            return f"id:{event.bet_id}"
        return "anon:" + "|".join(
            str(value or "")
            for value in (event.player_hash, event.currency, event.amount)
        )

    def ingest(self, event: NormalizedEvent) -> tuple[dict | None, dict | None]:
        self._ensure_started(event)

        for name in ("reported_player_count", "reported_bet_count", "online_count"):
            value = getattr(event, name)
            if value is not None:
                setattr(self, name, value)

        bet_row = None
        if event.event_type == "bet_update":
            key = self._bet_key(event)
            state = self.bets.setdefault(key, BetState())
            state.update(event)
            bet_row = {
                **event.as_dict(),
                "local_round_uid": self.local_round_uid,
                "round_id": event.round_id or self.round_id,
                "bet_key": key,
            }

        round_row = None
        if event.event_type == "round_end" and event.crashpoint is not None:
            duration_ms = None
            if self.started_monotonic is not None:
                duration_ms = int((time.monotonic() - self.started_monotonic) * 1000)

            players = {bet.player_hash for bet in self.bets.values() if bet.player_hash}
            observed_cashouts = sum(
                1 for bet in self.bets.values() if bet.cashout_multiplier is not None
            )
            wager_total = sum(
                float(bet.amount) for bet in self.bets.values() if bet.amount is not None
            )
            payout_total = sum(
                float(bet.payout) for bet in self.bets.values() if bet.payout is not None
            )

            round_row = {
                "local_round_uid": self.local_round_uid,
                "round_id": event.round_id or self.round_id,
                "started_at_utc": self.started_at_utc,
                "ended_at_utc": event.observed_at_utc,
                "crashpoint": event.crashpoint,
                "reported_player_count": event.reported_player_count or self.reported_player_count,
                "reported_bet_count": event.reported_bet_count or self.reported_bet_count,
                "online_count": event.online_count or self.online_count,
                "observed_unique_bets": len(self.bets),
                "observed_unique_players": len(players),
                "observed_cashouts": observed_cashouts,
                "observed_wager_total": round(wager_total, 12),
                "observed_payout_total": round(payout_total, 12),
                "round_duration_ms": duration_ms,
            }
            self.reset()

        return bet_row, round_row

    def update_online_count(self, count: int) -> None:
        self.online_count = count

    def reset(self) -> None:
        self.local_round_uid = uuid.uuid4().hex
        self.round_id = None
        self.started_at_utc = None
        self.started_monotonic = None
        self.reported_player_count = None
        self.reported_bet_count = None
        self.bets.clear()
