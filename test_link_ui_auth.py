"""calendar_link_ui login: session cookie, CSRF on POSTs, logout, rate limit, Basic for
scripts, open when unconfigured. Keyring mocked; synthetic credentials."""
from __future__ import annotations

import base64
import http.client
import re
import threading
from http.server import ThreadingHTTPServer
from urllib.parse import urlencode

import pytest

import calendar_accounts
import link_ui_auth
import web_auth
from calendar_link_ui import make_handler

USER, PW = "admin", "synthetic-Pass-123"


@pytest.fixture(autouse=True)
def fake_keyring(monkeypatch):
    store = {}
    monkeypatch.setattr(link_ui_auth.keyring, "set_password", lambda s, u, p: store.__setitem__((s, u), p))
    monkeypatch.setattr(link_ui_auth.keyring, "get_password", lambda s, u: store.get((s, u)))
    monkeypatch.setattr(calendar_accounts, "load_accounts", lambda: {})
    return store


def start(tmp_path):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(tmp_path / "none.json"))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd


@pytest.fixture
def port(tmp_path):
    link_ui_auth.set_credentials(USER, PW)
    httpd = start(tmp_path)
    yield httpd.server_port
    httpd.shutdown()


def req(port, method, path, body=b"", headers=None):
    c = http.client.HTTPConnection("127.0.0.1", port)
    try:
        c.request(method, path, body=body, headers=headers or {})
        r = c.getresponse()
        return r.status, dict(r.getheaders()), r.read()
    finally:
        c.close()


def login(port, pw=PW, nxt="/"):
    return req(port, "POST", "/login", urlencode({"username": USER, "password": pw, "next": nxt}).encode(),
               {"Content-Type": "application/x-www-form-urlencoded"})


def test_credentials_in_keyring_only(fake_keyring):
    link_ui_auth.set_credentials(USER, PW)
    assert fake_keyring == {("infobuddy-link-ui", "__username__"): USER, ("infobuddy-link-ui", USER): PW}
    assert link_ui_auth.verify(USER, PW) and not link_ui_auth.verify(USER, "x") and not link_ui_auth.verify("x", PW)


def test_requires_login(port):
    st, h, _ = req(port, "GET", "/", headers={"Accept": "text/html"})
    assert st == 303 and h["Location"] == "/login?next=/"
    st, h, _ = req(port, "GET", "/")
    assert st == 401 and "Basic" in h["WWW-Authenticate"]
    assert req(port, "POST", "/connect/start")[0] == 401


def test_session_csrf_and_logout(port):
    st, h, _ = login(port)
    assert st == 303 and h["Location"] == "/"
    cookie = h["Set-Cookie"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie and cookie.startswith("infobuddy_link_session=")
    cookie = cookie.split(";")[0]
    st, _, page = req(port, "GET", "/", headers={"Cookie": cookie})
    assert st == 200 and b"Log out" in page
    tokens = set(re.findall(rb'name="csrf_token" value="([^"]+)"', page))
    assert len(tokens) == 1 and page.count(b"</form>") == page.count(b'name="csrf_token"')
    csrf = tokens.pop().decode()
    form = {"Cookie": cookie, "Content-Type": "application/x-www-form-urlencoded"}
    assert req(port, "POST", "/connect/start", b"", form)[0] == 403
    assert req(port, "POST", "/connect/start", f"csrf_token={csrf}".encode(), form)[0] == 200  # setup-needed page
    st, h, _ = req(port, "POST", "/logout", f"csrf_token={csrf}".encode(), form)
    assert st == 303 and "Max-Age=0" in h["Set-Cookie"]
    assert req(port, "GET", "/", headers={"Cookie": cookie})[0] == 401


def test_basic_auth_and_rate_limit(port):
    good = {"Authorization": "Basic " + base64.b64encode(f"{USER}:{PW}".encode()).decode()}
    assert req(port, "GET", "/", headers=good)[0] == 200
    for _ in range(web_auth.MAX_FAILURES):
        assert login(port, pw="nope")[0] == 401
    assert login(port)[0] == 429
    assert req(port, "GET", "/", headers=good)[0] == 429


def test_open_redirect_blocked(port):
    assert login(port, nxt="//evil.example")[1]["Location"] == "/"


def test_open_when_unconfigured(tmp_path):
    httpd = start(tmp_path)
    try:
        assert req(httpd.server_port, "GET", "/")[0] == 200
        _, _, page = req(httpd.server_port, "GET", "/login")
        assert b"No password is configured" in page
    finally:
        httpd.shutdown()
