from datetime import datetime, timedelta, timezone
from pathlib import Path

from sync_status import describe_status, mark_finished, mark_running, read_status, relative_time


def _iso(delta: timedelta) -> str:
    return (datetime.now(timezone.utc) - delta).isoformat()


def test_read_status_returns_none_when_missing(tmp_path):
    assert read_status(tmp_path / "state") is None


def test_read_status_returns_none_on_corrupt_json(tmp_path):
    state_dir = tmp_path / "state"
    state_dir.mkdir()
    (state_dir / "sync_status.json").write_text("not json", encoding="utf-8")
    assert read_status(state_dir) is None


def test_mark_running_then_finished_success(tmp_path):
    state_dir = tmp_path / "state"
    mark_running(state_dir)
    running = read_status(state_dir)
    assert running["status"] == "running"
    assert running["started_at"]
    assert running["pid"] is not None

    mark_finished(state_dir, success=True)
    finished = read_status(state_dir)
    assert finished["status"] == "success"
    assert finished["error"] is None
    assert finished["pid"] is None
    # started_at is preserved from the running record.
    assert finished["started_at"] == running["started_at"]
    assert finished["finished_at"]


def test_mark_finished_failure_records_error(tmp_path):
    state_dir = tmp_path / "state"
    mark_running(state_dir)
    mark_finished(state_dir, success=False, error="boom")
    status = read_status(state_dir)
    assert status["status"] == "failed"
    assert status["error"] == "boom"


def test_mark_finished_without_prior_running_still_writes(tmp_path):
    state_dir = tmp_path / "state"
    mark_finished(state_dir, success=True)
    status = read_status(state_dir)
    assert status["status"] == "success"
    assert status["started_at"] is None


def test_relative_time_none_is_never():
    assert relative_time(None) == "never"


def test_relative_time_buckets():
    assert relative_time(_iso(timedelta(seconds=5))) == "just now"
    assert relative_time(_iso(timedelta(minutes=5))) == "5m ago"
    assert relative_time(_iso(timedelta(hours=3))) == "3h ago"
    assert relative_time(_iso(timedelta(days=2))) == "2d ago"


def test_describe_status_never_run():
    assert describe_status(None) == "Last sync: never"


def test_describe_status_success():
    status = {"status": "success", "finished_at": _iso(timedelta(minutes=10))}
    assert describe_status(status) == "Last sync: 10m ago (ok)"


def test_describe_status_failed_includes_error():
    status = {"status": "failed", "finished_at": _iso(timedelta(hours=1)), "error": "boom"}
    assert describe_status(status) == "Last sync failed 1h ago: boom"


def test_describe_status_failed_without_error_message():
    status = {"status": "failed", "finished_at": _iso(timedelta(hours=1)), "error": None}
    assert describe_status(status) == "Last sync failed 1h ago: unknown error"


def test_describe_status_running():
    status = {"status": "running", "started_at": _iso(timedelta(seconds=1))}
    assert describe_status(status) == "Syncing now…"
