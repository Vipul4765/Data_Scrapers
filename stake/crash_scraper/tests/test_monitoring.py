from pathlib import Path

from stake_crash_scraper.monitoring import RuntimeMonitor


def test_runtime_monitor_writes_atomic_status(tmp_path: Path):
    monitor = RuntimeMonitor(tmp_path)
    monitor.set_state("RUNNING", "ok", online_count=123)

    payload = (tmp_path / "status.json").read_text(encoding="utf-8")

    assert '"state": "RUNNING"' in payload
    assert '"online_count": 123' in payload
    assert not (tmp_path / "status.json.tmp").exists()
