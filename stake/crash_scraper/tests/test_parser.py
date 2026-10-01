from stake_crash_scraper.parser import normalize_payload, parse_json_frame

SALT = b"0123456789abcdef0123456789abcdef"


def test_parse_json_frame_rejects_non_json():
    assert parse_json_frame("42") is None
    assert parse_json_frame("not-json") is None


def test_normalizes_bets_counts_and_round_end():
    payload = {
        "roundId": "r-100",
        "playerCount": 2,
        "betCount": 2,
        "bets": [
            {
                "betId": "b-1",
                "userId": "user-1",
                "betAmount": 10,
                "currency": "USD",
                "cashoutMultiplier": 2.5,
                "payout": 25,
            },
            {
                "betId": "b-2",
                "userId": "user-2",
                "betAmount": 5,
                "currency": "USD",
            },
        ],
        "crashPoint": 3.2,
    }

    events = normalize_payload(payload, player_salt=SALT)
    assert any(event.event_type == "round_end" and event.crashpoint == 3.2 for event in events)
    bets = [event for event in events if event.event_type == "bet_update"]
    assert len(bets) == 2
    assert bets[0].player_hash != "user-1"
    assert len(bets[0].player_hash or "") == 24
