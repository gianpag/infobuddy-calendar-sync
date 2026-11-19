import json
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import RefreshError

import calendar_accounts
import google_calendar_read_sync as read_sync
from google_calendar_read_sync import (
    AccountNotLinkedError,
    _normalize_event,
    fetch_account_email,
    load_credentials_for_account,
    sync_all,
)


def _use_tmp_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_accounts, "ACCOUNTS_PATH", tmp_path / "accounts.json")


# -- _normalize_event -----------------------------------------------------------------

def test_normalize_event_handles_all_day_event():
    raw = {"id": "ev1", "summary": "Sports day", "start": {"date": "2026-10-20"}, "end": {"date": "2026-10-21"}}
    event = _normalize_event(raw, account_email="a@example.com", calendar_id="primary")
    assert event == {
        "title": "Sports day",
        "start": "2026-10-20",
        "end": "2026-10-21",
        "all_day": True,
        "source_id": "google:a@example.com:primary:ev1",
    }


def test_normalize_event_handles_timed_event_with_location():
    raw = {
        "id": "ev2", "summary": "Dentist",
        "start": {"dateTime": "2026-10-20T09:00:00+02:00"},
        "end": {"dateTime": "2026-10-20T09:30:00+02:00"},
        "location": "Main St 1",
    }
    event = _normalize_event(raw, account_email="a@example.com", calendar_id="primary")
    assert event["all_day"] is False
    assert event["start"] == "2026-10-20T09:00:00+02:00"
    assert event["location"] == "Main St 1"


def test_normalize_event_omits_location_when_absent():
    raw = {"id": "ev3", "summary": "x", "start": {"date": "2026-10-20"}, "end": {"date": "2026-10-21"}}
    event = _normalize_event(raw, account_email="a@example.com", calendar_id="primary")
    assert "location" not in event


def test_normalize_event_returns_none_when_start_missing():
    raw = {"id": "ev4", "summary": "x", "start": {}, "end": {"date": "2026-10-21"}}
    assert _normalize_event(raw, account_email="a@example.com", calendar_id="primary") is None


def test_normalize_event_defaults_title_when_missing():
    raw = {"id": "ev5", "start": {"date": "2026-10-20"}, "end": {"date": "2026-10-21"}}
    event = _normalize_event(raw, account_email="a@example.com", calendar_id="primary")
    assert event["title"] == "(no title)"


# -- load_credentials_for_account ------------------------------------------------------

def test_load_credentials_for_account_raises_when_not_linked():
    with patch("google_calendar_read_sync.keyring.get_password", return_value=None):
        with pytest.raises(AccountNotLinkedError):
            load_credentials_for_account("nobody@example.com")


def test_load_credentials_for_account_returns_valid_cached_creds():
    valid_creds = MagicMock(valid=True)
    with (
        patch("google_calendar_read_sync.keyring.get_password", return_value='{"token": "t"}'),
        patch("google_calendar_read_sync.Credentials.from_authorized_user_info", return_value=valid_creds),
    ):
        result = load_credentials_for_account("a@example.com")
    assert result is valid_creds


def test_load_credentials_for_account_refreshes_expired_token():
    expired_creds = MagicMock(valid=False, expired=True, refresh_token="rt")

    def _do_refresh(request):
        expired_creds.valid = True  # simulate refresh() flipping validity

    expired_creds.refresh.side_effect = _do_refresh

    with (
        patch("google_calendar_read_sync.keyring.get_password", return_value='{"token": "stale"}'),
        patch("google_calendar_read_sync.Credentials.from_authorized_user_info", return_value=expired_creds),
        patch("google_calendar_read_sync.keyring.set_password") as mock_set,
    ):
        result = load_credentials_for_account("a@example.com")

    assert result is expired_creds
    mock_set.assert_called_once()


def test_load_credentials_for_account_propagates_refresh_error():
    expired_creds = MagicMock(valid=False, expired=True, refresh_token="rt")
    expired_creds.refresh.side_effect = RefreshError("invalid_grant")

    with (
        patch("google_calendar_read_sync.keyring.get_password", return_value='{"token": "stale"}'),
        patch("google_calendar_read_sync.Credentials.from_authorized_user_info", return_value=expired_creds),
    ):
        with pytest.raises(RefreshError):
            load_credentials_for_account("a@example.com")


# -- fetch_account_email ---------------------------------------------------------------

def test_fetch_account_email_parses_response():
    fake_response = MagicMock()
    fake_response.read.return_value = json.dumps({"email": "a@example.com"}).encode()
    fake_response.__enter__.return_value = fake_response
    fake_response.__exit__.return_value = False

    with patch("google_calendar_read_sync.urllib.request.urlopen", return_value=fake_response):
        assert fetch_account_email("some-token") == "a@example.com"


# -- sync_all ---------------------------------------------------------------------------

def test_sync_all_skips_write_when_no_accounts_linked(monkeypatch, tmp_path):
    _use_tmp_accounts(monkeypatch, tmp_path)
    output_path = tmp_path / "generic-calendar.json"

    report = sync_all(output_path, days=30)

    assert report["skipped_write"] is True
    assert not output_path.exists()


def test_sync_all_skips_account_with_no_selected_calendars(monkeypatch, tmp_path):
    _use_tmp_accounts(monkeypatch, tmp_path)
    calendar_accounts.add_account("a@example.com")  # linked, but no calendars selected
    output_path = tmp_path / "generic-calendar.json"

    report = sync_all(output_path, days=30)

    assert report["accounts_synced"] == []
    assert json.loads(output_path.read_text())["events"] == []


def test_sync_all_writes_merged_events_from_synced_accounts(monkeypatch, tmp_path):
    _use_tmp_accounts(monkeypatch, tmp_path)
    calendar_accounts.add_account("a@example.com")
    calendar_accounts.set_selected_calendars("a@example.com", ["primary"])
    output_path = tmp_path / "generic-calendar.json"

    fake_event = {"title": "x", "start": "2026-10-20", "end": "2026-10-21", "all_day": True, "source_id": "google:a@example.com:primary:1"}

    with (
        patch("google_calendar_read_sync.load_credentials_for_account", return_value=MagicMock()),
        patch("google_calendar_read_sync.fetch_events", return_value=[fake_event]),
    ):
        report = sync_all(output_path, days=30)

    assert report["accounts_synced"] == ["a@example.com"]
    assert report["event_count"] == 1
    assert json.loads(output_path.read_text()) == {"events": [fake_event]}


def test_sync_all_skips_account_that_needs_relinking(monkeypatch, tmp_path):
    _use_tmp_accounts(monkeypatch, tmp_path)
    calendar_accounts.add_account("a@example.com")
    calendar_accounts.set_selected_calendars("a@example.com", ["primary"])
    output_path = tmp_path / "generic-calendar.json"

    with patch("google_calendar_read_sync.load_credentials_for_account", side_effect=AccountNotLinkedError("a@example.com")):
        report = sync_all(output_path, days=30)

    assert report["accounts_skipped"] == ["a@example.com"]
    assert report["event_count"] == 0
    assert output_path.exists()  # still writes -- just an empty/partial merge, not a crash
