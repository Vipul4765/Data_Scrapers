from stake_crash_scraper.round_state import RoundState
from stake_crash_scraper.types import NormalizedEvent


def event(kind: str, **kwargs):
    return NormalizedEvent(event_type=kind, observed_at_utc="2026-10-01T00:00:00.000+00:00", **kwargs)


def test_round_state_deduplicates_bet_updates_by_bet_id():
    state = RoundState()

    state.ingest(event("bet_update", round_id="r1", bet_id="b1", player_hash="p1", amount=10, currency="USD"))
    state.ingest(event("bet_update", round_id="r1", bet_id="b1", player_hash="p1", cashout_multiplier=2.0, payout=20))
    state.ingest(event("bet_update", round_id="r1", bet_id="b2", player_hash="p2", amount=5, currency="USD"))

    _, row = state.ingest(event("round_end", round_id="r1", crashpoint=3.0, reported_player_count=2, reported_bet_count=2))

    assert row is not None
    assert row["observed_unique_bets"] == 2
    assert row["observed_unique_players"] == 2
    assert row["observed_cashouts"] == 1
    assert row["observed_wager_total"] == 15
    assert row["observed_payout_total"] == 20
