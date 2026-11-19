import asyncio

import pytest

import infomentor_extract as im
from log_privacy import log_first_name


@pytest.mark.parametrize("raw,expected", [
    ("Example, Alex", "Alex"),
    ("Example Testsson, Alex Maria", "Alex"),
    ("Alex Example", "Alex"),
    ("  Alex  ", "Alex"),
    ("", "child"),
    (None, "child"),
])
def test_log_first_name(raw, expected):
    assert log_first_name(raw) == expected


def test_extract_logs_first_name_only(monkeypatch, capsys):
    pupil = im.Pupil("Example Testsson, Alex Maria", "/Account/PupilSwitcher/SwitchPupil/1", "1")

    async def noop(*a, **k):
        raise RuntimeError("stop")  # stop right after the log line

    monkeypatch.setattr(im, "switch_pupil", noop)
    with pytest.raises(RuntimeError):
        asyncio.run(im.extract_for_pupil(None, pupil, None, None, False))
    out = capsys.readouterr().out
    assert "Alex" in out
    assert "Example" not in out and "Testsson" not in out and "Maria" not in out


def test_calendar_sync_logs_first_name_only(tmp_path, monkeypatch, capsys):
    import json
    import sys

    import google_calendar_sync as gcs

    data = tmp_path / "in.json"
    data.write_text(json.dumps({"children": [{"pupil": {"name": "Example Testsson, Alex Maria"}, "normalized": {}}]}))
    monkeypatch.setattr(sys, "argv", ["x", str(data), "--dry-run", "--state-file", str(tmp_path / "s.json")])
    monkeypatch.setattr(gcs, "sync_child", lambda *a, **k: [])
    assert gcs.main() == 0
    out = capsys.readouterr().out
    assert "-- Alex --" in out and "Testsson" not in out and "Maria" not in out
