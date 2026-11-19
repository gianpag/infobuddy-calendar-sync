import http.client
import json
import threading
from http.server import ThreadingHTTPServer
from unittest.mock import patch

import pytest

import calendar_accounts
import calendar_link_ui
from calendar_link_ui import (
    make_handler,
    render_calendar_picker,
    render_connect_status,
    render_home,
    render_setup_needed,
)
from google_device_flow import DeviceCodeResponse, DeviceFlowError


# -- pure render functions --------------------------------------------------------------

def test_render_home_shows_placeholder_when_no_accounts(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_accounts, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    page = render_home().decode("utf-8")
    assert "No Google accounts linked yet" in page


def test_render_home_lists_linked_accounts_and_escapes_untrusted_email(monkeypatch, tmp_path):
    monkeypatch.setattr(calendar_accounts, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    calendar_accounts.add_account("<script>alert(1)</script>@example.com")
    calendar_accounts.set_selected_calendars("<script>alert(1)</script>@example.com", ["primary", "secondary"])

    page = render_home().decode("utf-8")
    assert "<script>alert(1)</script>@example.com" not in page
    assert "&lt;script&gt;" in page
    assert "2 calendars selected" in page


def test_render_setup_needed_mentions_device_client_type():
    page = render_setup_needed().decode("utf-8")
    assert "TVs and Limited Input devices" in page


def test_render_connect_status_pending_shows_code_and_autorefreshes():
    device = DeviceCodeResponse("devcode", "ABCD-EFGH", "https://www.google.com/device", expires_in=1800, interval=5)
    page = render_connect_status({"status": "pending", "device": device}).decode("utf-8")
    assert "ABCD-EFGH" in page
    assert "www.google.com/device" in page
    assert 'http-equiv="refresh"' in page


def test_render_connect_status_error_shows_message():
    page = render_connect_status({"status": "error", "message": "something broke"}).decode("utf-8")
    assert "something broke" in page


def test_render_connect_status_linked_links_to_calendar_picker():
    page = render_connect_status({"status": "linked", "email": "a@example.com"}).decode("utf-8")
    assert "a@example.com" in page
    assert "/accounts/a%40example.com/calendars" in page


def test_render_calendar_picker_checks_selected_calendars():
    calendars = [{"id": "primary", "summary": "Primary"}, {"id": "work", "summary": "Work"}]
    page = render_calendar_picker("a@example.com", calendars, {"work"}).decode("utf-8")
    primary_line = next(line for line in page.splitlines() if 'value="primary"' in line)
    work_line = next(line for line in page.splitlines() if 'value="work"' in line)
    assert "checked" not in primary_line
    assert "checked" in work_line


def test_render_calendar_picker_shows_calendar_id_visibly():
    # Not just the checkbox's hidden form value -- InkBuddy's per-device calendar
    # filtering needs the admin to be able to read/copy this id (see the docstring on
    # render_calendar_picker()).
    calendars = [{"id": "alex-timetable@group.calendar.google.com", "summary": "Alex Timetable"}]
    page = render_calendar_picker("a@example.com", calendars, set()).decode("utf-8")
    assert "alex-timetable@group.calendar.google.com" in page
    # appears twice: once as the checkbox value, once in the visible calendar-id div
    assert page.count("alex-timetable@group.calendar.google.com") == 2


# -- integration: real HTTP server, mocked Google calls ---------------------------------

@pytest.fixture
def running_server(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_accounts, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    client_secret_path = tmp_path / "client_secret_device.json"
    client_secret_path.write_text(json.dumps({"installed": {"client_id": "cid", "client_secret": "csecret"}}))

    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(client_secret_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"127.0.0.1:{server.server_port}"
    finally:
        server.shutdown()
        thread.join(timeout=5)


def _get(host_port: str, path: str) -> tuple[int, bytes, str | None]:
    conn = http.client.HTTPConnection(host_port)
    try:
        conn.request("GET", path)
        resp = conn.getresponse()
        return resp.status, resp.read(), resp.getheader("Location")
    finally:
        conn.close()


def _post(host_port: str, path: str, body: bytes = b"") -> tuple[int, bytes, str | None]:
    conn = http.client.HTTPConnection(host_port)
    try:
        conn.request("POST", path, body=body, headers={"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        return resp.status, resp.read(), resp.getheader("Location")
    finally:
        conn.close()


def test_home_page_served(running_server):
    status, body, _ = _get(running_server, "/")
    assert status == 200
    assert b"Generic Google Calendar Sync" in body


def test_setup_needed_shown_when_client_secret_missing(tmp_path, monkeypatch):
    monkeypatch.setattr(calendar_accounts, "ACCOUNTS_PATH", tmp_path / "accounts.json")
    server = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path / "missing.json"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        status, body, _ = _post(f"127.0.0.1:{server.server_port}", "/connect/start")
        assert status == 200
        assert b"One-time setup needed" in body
    finally:
        server.shutdown()
        thread.join(timeout=5)


def test_connect_start_redirects_to_status_page(running_server):
    device = DeviceCodeResponse("devcode", "ABCD-EFGH", "https://www.google.com/device", expires_in=1800, interval=5)
    with patch("calendar_link_ui.request_device_code", return_value=device):
        status, _, location = _post(running_server, "/connect/start")
    assert status == 303
    assert location.startswith("/connect/status/")


def test_connect_status_shows_pending_then_linked(running_server):
    device = DeviceCodeResponse("devcode", "ABCD-EFGH", "https://www.google.com/device", expires_in=1800, interval=0)
    with patch("calendar_link_ui.request_device_code", return_value=device):
        _, _, location = _post(running_server, "/connect/start")

    # First poll: Google says authorization is still pending.
    with patch("calendar_link_ui.poll_for_token", return_value=None):
        status, body, _ = _get(running_server, location)
    assert status == 200
    assert b"Waiting for you to finish" in body

    # Second poll: Google says it's authorized; resolving the account email succeeds.
    with (
        patch("calendar_link_ui.poll_for_token", return_value={"access_token": "at", "refresh_token": "rt"}),
        patch("google_calendar_read_sync.fetch_account_email", return_value="a@example.com"),
        patch("google_calendar_read_sync.store_token") as mock_store,
    ):
        status, body, _ = _get(running_server, location)

    assert status == 200
    assert b"a@example.com" in body
    mock_store.assert_called_once()
    assert calendar_accounts.list_account_emails() == ["a@example.com"]


def test_connect_status_shows_error_on_denied(running_server):
    device = DeviceCodeResponse("devcode", "ABCD-EFGH", "https://www.google.com/device", expires_in=1800, interval=0)
    with patch("calendar_link_ui.request_device_code", return_value=device):
        _, _, location = _post(running_server, "/connect/start")

    with patch("calendar_link_ui.poll_for_token", side_effect=DeviceFlowError("access_denied")):
        status, body, _ = _get(running_server, location)

    assert status == 200
    assert b"access_denied" in body


def test_unknown_status_session_returns_404(running_server):
    status, _, _ = _get(running_server, "/connect/status/does-not-exist")
    assert status == 404


def test_save_calendars_updates_selection_and_redirects(running_server):
    calendar_accounts.add_account("a@example.com")
    status, _, location = _post(running_server, "/accounts/a%40example.com/calendars", body=b"calendar_id=primary&calendar_id=work")
    assert status == 303
    assert location == "/"
    assert calendar_accounts.selected_calendars("a@example.com") == ["primary", "work"]


def test_disconnect_removes_account_and_token(running_server):
    calendar_accounts.add_account("a@example.com")
    with patch("google_calendar_read_sync.remove_token") as mock_remove:
        status, _, location = _post(running_server, "/accounts/a%40example.com/disconnect")
    assert status == 303
    assert location == "/"
    mock_remove.assert_called_once_with("a@example.com")
    assert calendar_accounts.list_account_emails() == []
