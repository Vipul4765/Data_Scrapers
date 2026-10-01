from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(slots=True)
class NormalizedEvent:
    event_type: str
    observed_at_utc: str
    round_id: str | None = None
    bet_id: str | None = None
    player_hash: str | None = None
    amount: float | None = None
    currency: str | None = None
    cashout_multiplier: float | None = None
    payout: float | None = None
    profit: float | None = None
    status: str | None = None
    crashpoint: float | None = None
    reported_player_count: int | None = None
    reported_bet_count: int | None = None
    online_count: int | None = None

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
