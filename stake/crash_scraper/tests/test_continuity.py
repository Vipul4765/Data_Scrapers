from pathlib import Path

from stake_crash_scraper.continuity import ContinuityJournal
from stake_crash_scraper.history_api import normalize_history_round


def test_normalize_official_history_round():
    row = normalize_history_round({
        "id": "round-1",
        "iid": 2,
        "createdAt": 1704328927635,
        "crashpoint": 10.586515,
        "numbers": [1, 2, 3],
    })

    assert row is not None
    assert row["round_id"] == "round-1"
    assert row["crashpoint"] == 10.586515
    assert row["source"] == "official_api"
    assert row["live_features_complete"] is False


def test_continuity_journal_records_gap(tmp_path: Path):
    journal = ContinuityJournal(tmp_path)
    gap = journal.start_gap("test")
    assert gap.reason == "test"
    journal.end_gap(backfill_status="ok", backfill_written=3)

    text = (tmp_path / "gaps.jsonl").read_text(encoding="utf-8")
    assert "gap_started" in text
    assert "gap_ended" in text
    assert '"backfill_written":3' in text
