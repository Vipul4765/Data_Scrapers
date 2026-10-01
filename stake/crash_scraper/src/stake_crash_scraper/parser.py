from __future__ import annotations

import hashlib
import json
import math
from datetime import datetime, timezone
from typing import Any, Iterable

from .types import NormalizedEvent

ROUND_ID_KEYS = {
    "roundid", "round_id", "gameid", "game_id", "rounduuid", "round_uuid"
}
CRASH_KEYS = {
    "crashpoint", "crash_point", "crashmultiplier", "crash_multiplier",
    "resultmultiplier", "result_multiplier", "finalmultiplier", "final_multiplier",
}
PLAYER_COUNT_KEYS = {
    "playercount", "player_count", "playerscount", "players_count",
    "bettorcount", "bettor_count", "bettorscount", "bettors_count",
}
BET_COUNT_KEYS = {
    "betcount", "bet_count", "betscount", "bets_count", "totalbets", "total_bets",
}
ONLINE_COUNT_KEYS = {
    "onlinecount", "online_count", "playersonline", "players_online", "playingcount", "playing_count",
}
BET_ID_KEYS = {"betid", "bet_id", "wagerid", "wager_id"}
PLAYER_KEYS = {"userid", "user_id", "playerid", "player_id", "username", "player", "user"}
AMOUNT_KEYS = {"betamount", "bet_amount", "wageramount", "wager_amount", "amount", "stake"}
CURRENCY_KEYS = {"currency", "currencycode", "currency_code", "asset", "assetcode", "asset_code"}
CASHOUT_KEYS = {
    "cashoutmultiplier", "cashout_multiplier", "cashoutat", "cashout_at",
    "payoutmultiplier", "payout_multiplier", "cashedoutat", "cashed_out_at",
}
PAYOUT_KEYS = {"payout", "payoutamount", "payout_amount", "winamount", "win_amount", "returnamount", "return_amount"}
PROFIT_KEYS = {"profit", "profitamount", "profit_amount"}
STATUS_KEYS = {"status", "betstatus", "bet_status", "state"}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def parse_json_frame(frame: Any) -> Any | None:
    if isinstance(frame, bytes):
        try:
            frame = frame.decode("utf-8")
        except UnicodeDecodeError:
            return None
    if not isinstance(frame, str):
        return None
    frame = frame.strip()
    if not frame or frame[0] not in "[{":
        return None
    try:
        return json.loads(frame)
    except json.JSONDecodeError:
        return None


def walk_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from walk_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from walk_dicts(child)


def safe_shape(value: Any, *, depth: int = 0, max_depth: int = 4) -> Any:
    if depth >= max_depth:
        return type(value).__name__
    if isinstance(value, dict):
        return {
            str(key): safe_shape(child, depth=depth + 1, max_depth=max_depth)
            for key, child in list(value.items())[:50]
        }
    if isinstance(value, list):
        return [safe_shape(value[0], depth=depth + 1, max_depth=max_depth)] if value else []
    return type(value).__name__


def _compact_key(value: Any) -> str:
    return str(value).lower().replace("-", "").replace("_", "").replace(" ", "")


def _lookup(obj: dict[str, Any], aliases: set[str]) -> Any:
    compact_aliases = {_compact_key(alias) for alias in aliases}
    for key, value in obj.items():
        if _compact_key(key) in compact_aliases:
            return value
    return None


def _to_float(value: Any) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _to_int(value: Any) -> int | None:
    number = _to_float(value)
    if number is None or number < 0:
        return None
    return int(number)


def _extract_identity(value: Any) -> str | None:
    if value in (None, ""):
        return None
    if isinstance(value, dict):
        for key in ("id", "userId", "user_id", "playerId", "player_id", "name", "username"):
            if value.get(key) not in (None, ""):
                return str(value[key])
        return None
    return str(value)


def hash_player(value: Any, salt: bytes) -> str | None:
    identity = _extract_identity(value)
    if not identity:
        return None
    digest = hashlib.sha256(salt + identity.encode("utf-8", "ignore")).hexdigest()
    return digest[:24]


def normalize_payload(payload: Any, *, player_salt: bytes) -> list[NormalizedEvent]:
    events: list[NormalizedEvent] = []

    for obj in walk_dicts(payload):
        round_id_value = _lookup(obj, ROUND_ID_KEYS)
        round_id = str(round_id_value) if round_id_value not in (None, "") else None

        crashpoint = _to_float(_lookup(obj, CRASH_KEYS))
        if crashpoint is not None and crashpoint >= 1.0:
            if round_id is None and obj.get("id") not in (None, ""):
                round_id = str(obj["id"])
            events.append(
                NormalizedEvent(
                    event_type="round_end",
                    observed_at_utc=utc_now(),
                    round_id=round_id,
                    crashpoint=crashpoint,
                    reported_player_count=_to_int(_lookup(obj, PLAYER_COUNT_KEYS)),
                    reported_bet_count=_to_int(_lookup(obj, BET_COUNT_KEYS)),
                    online_count=_to_int(_lookup(obj, ONLINE_COUNT_KEYS)),
                )
            )

        amount = _to_float(_lookup(obj, AMOUNT_KEYS))
        player_value = _lookup(obj, PLAYER_KEYS)
        bet_id_value = _lookup(obj, BET_ID_KEYS)
        if bet_id_value in (None, "") and amount is not None and obj.get("id") not in (None, ""):
            bet_id_value = obj["id"]

        bet_id = str(bet_id_value) if bet_id_value not in (None, "") else None
        player_hash = hash_player(player_value, player_salt)
        cashout = _to_float(_lookup(obj, CASHOUT_KEYS))
        payout = _to_float(_lookup(obj, PAYOUT_KEYS))
        profit = _to_float(_lookup(obj, PROFIT_KEYS))

        looks_like_bet = amount is not None and (player_hash is not None or bet_id is not None)
        looks_like_cashout = cashout is not None and (player_hash is not None or bet_id is not None)
        if looks_like_bet or looks_like_cashout:
            currency_value = _lookup(obj, CURRENCY_KEYS)
            status_value = _lookup(obj, STATUS_KEYS)
            events.append(
                NormalizedEvent(
                    event_type="bet_update",
                    observed_at_utc=utc_now(),
                    round_id=round_id,
                    bet_id=bet_id,
                    player_hash=player_hash,
                    amount=amount,
                    currency=str(currency_value) if currency_value not in (None, "") else None,
                    cashout_multiplier=cashout,
                    payout=payout,
                    profit=profit,
                    status=str(status_value) if status_value not in (None, "") else None,
                )
            )

        counts = {
            "reported_player_count": _to_int(_lookup(obj, PLAYER_COUNT_KEYS)),
            "reported_bet_count": _to_int(_lookup(obj, BET_COUNT_KEYS)),
            "online_count": _to_int(_lookup(obj, ONLINE_COUNT_KEYS)),
        }
        if any(value is not None for value in counts.values()):
            events.append(
                NormalizedEvent(
                    event_type="round_stats",
                    observed_at_utc=utc_now(),
                    round_id=round_id,
                    **counts,
                )
            )

    return dedupe_events(events)


def dedupe_events(events: list[NormalizedEvent]) -> list[NormalizedEvent]:
    seen: set[str] = set()
    unique: list[NormalizedEvent] = []
    for event in events:
        comparable = event.as_dict()
        comparable.pop("observed_at_utc", None)
        fingerprint = json.dumps(comparable, sort_keys=True, default=str)
        if fingerprint in seen:
            continue
        seen.add(fingerprint)
        unique.append(event)
    return unique
