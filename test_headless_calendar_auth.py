import json
import sys
from unittest.mock import MagicMock, patch

import pytest

import google_calendar_sync as gcs
import run_pipeline
from google_device_flow import DeviceCodeResponse
from sync_status import EXIT_AUTH_REQUIRED, describe_status, mark_finished, read_status


def test_non_interactive_without_token_raises_instead_of_opening_browser(tmp_path):
    with patch.object(gcs.keyring, "get_password", return_value=None), \
         patch.object(gcs.InstalledAppFlow, "from_client_secrets_file") as flow:
        with pytest.raises(gcs.CalendarAuthRequired) as exc:
            gcs.load_google_credentials(tmp_path / "cs.json", "default", interactive=False)
    flow.assert_not_called()
    assert "--link-device --account default" in str(exc.value)


def test_auto_detects_headless_and_never_runs_local_server(tmp_path, monkeypatch):
    monkeypatch.setattr(gcs, "can_open_browser", lambda: False)
    with patch.object(gcs.keyring, "get_password", return_value=None), \
         patch.object(gcs.InstalledAppFlow, "from_client_secrets_file") as flow:
        with pytest.raises(gcs.CalendarAuthRequired):
            gcs.load_google_credentials(tmp_path / "cs.json", "default")
    flow.assert_not_called()


def test_revoked_refresh_token_in_headless_mode_raises(tmp_path):
    creds = MagicMock(valid=False, expired=True, refresh_token="r")
    creds.refresh.side_effect = gcs.RefreshError("revoked")
    with patch.object(gcs.keyring, "get_password", return_value="{}"), \
         patch.object(gcs.Credentials, "from_authorized_user_info", return_value=creds), \
         patch.object(gcs.InstalledAppFlow, "from_client_secrets_file") as flow:
        with pytest.raises(gcs.CalendarAuthRequired):
            gcs.load_google_credentials(tmp_path / "cs.json", "default", interactive=False)
    flow.assert_not_called()


def test_can_open_browser_false_without_tty(monkeypatch):
    monkeypatch.setattr(sys, "stdin", MagicMock(isatty=lambda: False))
    assert gcs.can_open_browser() is False


def test_can_open_browser_false_on_linux_without_display(monkeypatch):
    monkeypatch.setattr(sys, "stdin", MagicMock(isatty=lambda: True))
    monkeypatch.setattr(sys, "platform", "linux")
    monkeypatch.delenv("DISPLAY", raising=False)
    monkeypatch.delenv("WAYLAND_DISPLAY", raising=False)
    assert gcs.can_open_browser() is False


def test_main_returns_auth_required_exit_code(tmp_path, monkeypatch, capsys):
    data = tmp_path / "in.json"
    data.write_text(json.dumps({"children": []}))
    cs = tmp_path / "cs.json"
    cs.write_text("{}")
    monkeypatch.setattr(sys, "argv", ["x", str(data), "--client-secret", str(cs), "--non-interactive",
                                      "--state-file", str(tmp_path / "s.json")])
    with patch.object(gcs.keyring, "get_password", return_value=None):
        assert gcs.main() == EXIT_AUTH_REQUIRED
    assert "--link-device" in capsys.readouterr().err


def test_link_account_via_device_flow_stores_token(tmp_path):
    cs = tmp_path / "dev.json"
    cs.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csec"}}))
    device = DeviceCodeResponse("dc", "ABCD-EFGH", "https://www.google.com/device", 1800, 5)
    lines = []
    with patch("google_device_flow.request_device_code", return_value=device), \
         patch("google_device_flow.wait_for_authorization", return_value={"access_token": "a", "refresh_token": "r"}), \
         patch.object(gcs.keyring, "set_password") as setpw:
        gcs.link_account_via_device_flow(cs, "default", sleep_fn=lambda s: None, out=lines.append)
    assert any("ABCD-EFGH" in l for l in lines) and any("google.com/device" in l for l in lines)
    service, account, stored = setpw.call_args[0]
    assert (service, account) == (gcs.CREDENTIAL_SERVICE_NAME, "default")
    stored = json.loads(stored)
    assert stored["refresh_token"] == "r" and stored["client_id"] == "cid"


def test_status_needs_relink_is_recorded_and_described(tmp_path):
    mark_finished(tmp_path, success=False, error="x", needs_relink=True)
    status = read_status(tmp_path)
    assert status["needs_relink"] is True
    assert "needs re-linking" in describe_status(status)


def _pipeline(tmp_path, monkeypatch, calendar_rc):
    (tmp_path / "client_secret.json").write_text("{}")
    calls = []
    monkeypatch.setattr(run_pipeline, "run", lambda cmd: calls.append(cmd))
    monkeypatch.setattr(run_pipeline, "run_calendar", lambda cmd: calls.append(cmd) or calendar_rc)
    monkeypatch.setattr(run_pipeline, "run_step", lambda cmd: calls.append(cmd) or 0)
    monkeypatch.setattr(sys, "argv", ["x", "--headless", "--sync-calendar",
                                      "--google-client-secret", str(tmp_path / "client_secret.json"),
                                      "--output-dir", str(tmp_path / "out"), "--state-dir", str(tmp_path / "st")])
    rc = run_pipeline.main()
    return rc, calls


def test_pipeline_runs_all_steps_and_flags_relink_when_calendar_auth_missing(tmp_path, monkeypatch):
    rc, calls = _pipeline(tmp_path, monkeypatch, EXIT_AUTH_REQUIRED)
    assert rc == 1
    scripts = [c[1].rsplit("/", 1)[-1] for c in calls]
    assert scripts == ["infomentor_extract.py", "study_brief.py", "change_watch.py", "google_calendar_sync.py"]
    assert "--non-interactive" in calls[-1]
    status = read_status(tmp_path / "st")
    assert status["status"] == "failed" and status["needs_relink"] is True


def test_pipeline_other_calendar_failure_is_failed_without_relink(tmp_path, monkeypatch):
    rc, _ = _pipeline(tmp_path, monkeypatch, 1)
    assert rc == 1
    assert read_status(tmp_path / "st")["needs_relink"] is False


def test_pipeline_success(tmp_path, monkeypatch):
    rc, _ = _pipeline(tmp_path, monkeypatch, 0)
    assert rc == 0
    assert read_status(tmp_path / "st")["status"] == "success"
