#!/usr/bin/env python3
"""A small local web UI for linking Google accounts to the generic calendar sync
(google_calendar_read_sync.py) -- lets you connect any number of Google accounts and
pick which of each account's calendars feed into out/generic-calendar.json, without
ever needing a browser on this machine itself (see google_device_flow.py for why: OAuth
Device Authorization Grant, not a redirect-based web flow).

Deliberately a separate process/port from InkBuddy's own admin UI (a different repo,
different security boundary, different data) -- this is purely about linking Google
accounts for this project's own generic calendar sync.

Login: once a username/password is set (manage_link_ui_credentials.py, stored in the
keyring under `infobuddy-link-ui`), every route needs a login: a /login page with an
HttpOnly SameSite=Strict session cookie and a CSRF token on every POST (web_auth.py), or
HTTP Basic auth for scripts. Failed logins are rate-limited per IP. With no credentials
configured the UI stays open and says so at startup. Plain stdlib http.server, no new
web framework dependency.
"""
from __future__ import annotations

import argparse
import html
import json
import secrets
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlsplit

from google.oauth2.credentials import Credentials

import calendar_accounts
import link_ui_auth
import web_auth
import google_calendar_read_sync as read_sync
from google_device_flow import DeviceCodeResponse, DeviceFlowError, TOKEN_URL, poll_for_token, request_device_code

DEFAULT_CLIENT_SECRET_PATH = Path("client_secret_device.json")
DEFAULT_SCOPES = read_sync.SCOPES

_PAGE_STYLE = """
<style>
  :root { color-scheme: light; }
  * { box-sizing: border-box; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    background: #f4f5f7; color: #1c1e21; margin: 0; padding: 2.5rem 1rem;
  }
  main { max-width: 640px; margin: 0 auto; }
  h1 { font-size: 1.5rem; margin-bottom: 0.25rem; }
  .subtitle { color: #666; margin-top: 0; margin-bottom: 2rem; }
  .card {
    background: #fff; border-radius: 12px; padding: 1.5rem 1.75rem;
    box-shadow: 0 1px 3px rgba(0,0,0,0.08); margin-bottom: 1.25rem;
  }
  .card h2 { margin-top: 0; font-size: 1.05rem; }
  .account-row {
    display: flex; justify-content: space-between; align-items: center;
    padding: 0.6rem 0; border-bottom: 1px solid #eee;
  }
  .account-row:last-child { border-bottom: none; }
  .account-email { font-weight: 600; }
  .muted { color: #888; font-size: 0.9rem; }
  a.button, button {
    display: inline-block; background: #1a73e8; color: #fff; border: none;
    border-radius: 8px; padding: 0.55rem 1.1rem; font-size: 0.95rem; font-weight: 600;
    text-decoration: none; cursor: pointer;
  }
  a.button.secondary, button.secondary { background: #e8eaed; color: #1c1e21; }
  a.button.danger, button.danger { background: #fff; color: #d93025; border: 1px solid #d93025; }
  .code-display {
    font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 2.2rem;
    font-weight: 700; letter-spacing: 0.15rem; text-align: center;
    background: #f0f4ff; border-radius: 10px; padding: 1rem; margin: 1.25rem 0;
  }
  .verify-url { text-align: center; font-size: 1.05rem; margin-bottom: 1.5rem; }
  .status-pill {
    display: inline-block; padding: 0.2rem 0.6rem; border-radius: 999px;
    font-size: 0.8rem; font-weight: 600;
  }
  .status-pending { background: #fff4e5; color: #996a13; }
  .status-error { background: #fdecea; color: #d93025; }
  .status-success { background: #e6f4ea; color: #1e7e34; }
  label.calendar-row { display: block; padding: 0.4rem 0; }
  .calendar-id { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.78rem;
                 margin-left: 1.6rem; user-select: all; }
  form.inline { display: inline; margin-left: 0.5rem; }
</style>
"""


def _page(title: str, body: str, *, refresh_seconds: int | None = None) -> bytes:
    refresh_tag = f'<meta http-equiv="refresh" content="{refresh_seconds}">' if refresh_seconds else ""
    html_doc = f"""<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
{refresh_tag}
<title>{html.escape(title)}</title>
{_PAGE_STYLE}
</head>
<body>
<main>
{body}
</main>
</body>
</html>
"""
    return html_doc.encode("utf-8")


def _esc(value: Any) -> str:
    return html.escape(str(value)) if value is not None else ""


def render_home() -> bytes:
    accounts = calendar_accounts.load_accounts()
    if accounts:
        rows = []
        for email, info in sorted(accounts.items()):
            count = len(info.get("calendars", []))
            calendars_label = f"{count} calendar{'s' if count != 1 else ''} selected" if count else "no calendars selected yet"
            rows.append(f"""<div class="account-row">
  <div>
    <div class="account-email">{_esc(email)}</div>
    <div class="muted">{calendars_label}</div>
  </div>
  <div>
    <a class="button secondary" href="/accounts/{quote(email, safe='')}/calendars">Manage</a>
    <form class="inline" method="post" action="/accounts/{quote(email, safe='')}/disconnect"
          onsubmit="return confirm('Disconnect {_esc(email)}?')">
      <button class="danger" type="submit">Disconnect</button>
    </form>
  </div>
</div>""")
        accounts_html = "".join(rows)
    else:
        accounts_html = '<p class="muted">No Google accounts linked yet.</p>'

    body = f"""
<h1>Generic Google Calendar Sync</h1>
<p class="subtitle">Link Google accounts and choose which calendars feed into InkBuddy's calendar widget.</p>

<div class="card">
  <h2>Linked accounts</h2>
  {accounts_html}
</div>

<div class="card">
  <h2>Connect a Google account</h2>
  <p class="muted">You'll get a short code to enter at a Google page -- on your phone, laptop, anywhere with a browser.</p>
  <form method="post" action="/connect/start">
    <button type="submit">Connect a Google account</button>
  </form>
</div>
"""
    return _page("Generic Google Calendar Sync", body)


def render_setup_needed() -> bytes:
    body = f"""
<h1>One-time setup needed</h1>
<div class="card">
  <p>No <code>{_esc(DEFAULT_CLIENT_SECRET_PATH)}</code> found. This project brings its own Google
  Cloud OAuth client per deployment (see README.md's "Generic Google Calendar sync" section):</p>
  <ol>
    <li>In <a href="https://console.cloud.google.com/">Google Cloud Console</a>, create/select a project
      and enable the <strong>Google Calendar API</strong>.</li>
    <li>Configure the OAuth consent screen (External, add yourself as a test user).</li>
    <li>Create an <strong>OAuth client ID</strong> of type <strong>"TVs and Limited Input devices"</strong>
      (not Desktop app / Web application -- those aren't accepted for this flow).</li>
    <li>Download the resulting JSON and save it as <code>{_esc(DEFAULT_CLIENT_SECRET_PATH)}</code>
      in the project root.</li>
  </ol>
  <p><a class="button secondary" href="/">Back</a></p>
</div>
"""
    return _page("Setup needed", body)


def render_connect_status(state: dict[str, Any]) -> bytes:
    status = state["status"]
    if status == "pending":
        device: DeviceCodeResponse = state["device"]
        body = f"""
<h1>Connect a Google account</h1>
<div class="card">
  <p class="verify-url">Go to <a href="{_esc(device.verification_url)}" target="_blank">{_esc(device.verification_url)}</a>
    and enter this code:</p>
  <div class="code-display">{_esc(device.user_code)}</div>
  <p class="muted" style="text-align:center">
    <span class="status-pill status-pending">Waiting for you to finish on Google...</span>
  </p>
</div>
"""
        return _page("Connect a Google account", body, refresh_seconds=3)

    if status == "error":
        body = f"""
<h1>Connection failed</h1>
<div class="card">
  <p><span class="status-pill status-error">Error</span></p>
  <p>{_esc(state.get("message", "Unknown error"))}</p>
  <p><a class="button secondary" href="/">Try again</a></p>
</div>
"""
        return _page("Connection failed", body)

    # status == "linked"
    email = state["email"]
    body = f"""
<h1>Connected</h1>
<div class="card">
  <p><span class="status-pill status-success">Connected</span> as <strong>{_esc(email)}</strong></p>
  <p>Now pick which of this account's calendars should sync.</p>
  <p><a class="button" href="/accounts/{quote(email, safe='')}/calendars">Choose calendars</a></p>
</div>
"""
    return _page("Connected", body)


def render_calendar_picker(email: str, calendars: list[dict[str, str]], selected: set[str]) -> bytes:
    # Each calendar's id is shown (muted, monospace) alongside its name -- not just used
    # as the checkbox's hidden form value -- because InkBuddy's own per-device/per-page
    # Google Calendar widget filters this sync's shared output down to specific
    # calendars by id (see InkBuddy's providers/google_calendar.py), and that id is the
    # only place to copy it from once you're past this one-time selection step.
    rows = []
    for cal in calendars:
        checked = "checked" if cal["id"] in selected else ""
        rows.append(f"""<label class="calendar-row">
  <input type="checkbox" name="calendar_id" value="{_esc(cal['id'])}" {checked}> {_esc(cal['summary'])}
  <div class="muted calendar-id">{_esc(cal['id'])}</div>
</label>""")
    body = f"""
<h1>Calendars for {_esc(email)}</h1>
<p class="subtitle">The id under each name is what you'd put in an InkBuddy widget's
config to show just that calendar on a specific device/page.</p>
<div class="card">
  <form method="post" action="/accounts/{quote(email, safe='')}/calendars">
    {''.join(rows) or '<p class="muted">No calendars found on this account.</p>'}
    <p><button type="submit">Save selection</button>
       <a class="button secondary" href="/">Done</a></p>
  </form>
</div>
"""
    return _page(f"Calendars for {email}", body)


SESSION_COOKIE = "infobuddy_link_session"  # distinct from InkBuddy's: cookies aren't port-scoped
LOGIN_TITLE = "Google Calendar accounts"


def make_handler(client_secret_path: Path) -> type[BaseHTTPRequestHandler]:
    sessions = web_auth.SessionStore()
    limiter = web_auth.LoginRateLimiter()
    configured_cache = {"at": -1e9, "value": False}

    def auth_configured() -> bool:
        # The keyring read is slow with cryptfile, so cache it for 30 s.
        if time.monotonic() - configured_cache["at"] > 30:
            configured_cache.update(at=time.monotonic(), value=link_ui_auth.is_configured())
        return configured_cache["value"]

    # In-memory only -- a single admin linking an account at a time is the expected
    # use case, and a mid-link process restart just means starting that one link over.
    pending: dict[str, dict[str, Any]] = {}

    def _load_client() -> tuple[str, str] | None:
        if not client_secret_path.exists():
            return None
        raw = json.loads(client_secret_path.read_text(encoding="utf-8"))
        section = raw.get("installed") or raw.get("web") or raw
        return section["client_id"], section["client_secret"]

    def _poll_pending(session_id: str) -> None:
        state = pending.get(session_id)
        if state is None or state["status"] != "pending":
            return
        now = time.monotonic()
        if now < state["next_poll_at"]:
            return  # not time yet -- avoid polling Google faster than its own interval

        client_id, client_secret = state["client_id"], state["client_secret"]
        device: DeviceCodeResponse = state["device"]
        try:
            result = poll_for_token(client_id, client_secret, device.device_code)
        except DeviceFlowError as exc:
            state["status"] = "error"
            state["message"] = str(exc)
            return

        if result is None:
            state["next_poll_at"] = now + device.interval
            return
        if result.get("_slow_down"):
            device.interval += 5
            state["next_poll_at"] = now + device.interval
            return

        # Success -- result is a full token response.
        creds = Credentials(
            token=result["access_token"],
            refresh_token=result.get("refresh_token"),
            token_uri=TOKEN_URL,
            client_id=client_id,
            client_secret=client_secret,
            scopes=DEFAULT_SCOPES,
        )
        try:
            email = read_sync.fetch_account_email(result["access_token"])
        except Exception as exc:  # noqa: BLE001 -- surfaced to the user, not swallowed
            state["status"] = "error"
            state["message"] = f"Linked, but couldn't resolve the account email: {exc}"
            return

        read_sync.store_token(email, creds)
        calendar_accounts.add_account(email)
        state["status"] = "linked"
        state["email"] = email

    class Handler(BaseHTTPRequestHandler):
        session: dict[str, Any] | None = None
        auth_mode: str | None = None

        # ---- auth ----
        def _ip(self) -> str:
            return self.client_address[0] if self.client_address else "?"

        def _authorize(self) -> bool:
            """True to proceed; otherwise a redirect/401/429 has been sent."""
            self.session = None
            if not auth_configured():
                self.auth_mode = "open"
                return True
            sess = sessions.get(web_auth.parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE))
            if sess:
                self.session, self.auth_mode = sess, "session"
                return True
            creds = link_ui_auth.parse_basic(self.headers.get("Authorization"))
            if creds:
                if limiter.retry_after(self._ip()):
                    self._send(429, b"Too many failed logins; try again later.", "text/plain; charset=utf-8")
                    return False
                if link_ui_auth.verify(*creds):
                    limiter.success(self._ip())
                    self.auth_mode = "basic"
                    return True
                limiter.failure(self._ip())
            if self.command == "GET" and "text/html" in self.headers.get("Accept", ""):
                self._redirect(web_auth.login_redirect(self.path))
                return False
            headers = {} if self.headers.get("Sec-Fetch-Site") else {"WWW-Authenticate": 'Basic realm="InfoBuddy calendar accounts"'}
            self._send(401, b"Authentication required", "text/plain; charset=utf-8", headers)
            return False

        def _csrf_ok(self, fields: dict[str, list[str]]) -> bool:
            if self.auth_mode != "session":
                return True
            token = self.headers.get("X-CSRF-Token") or (fields.get("csrf_token") or [None])[0]
            if self.session and web_auth.csrf_ok(self.session, token):
                return True
            self._send(403, b"CSRF token missing or invalid; reload the page and try again.", "text/plain; charset=utf-8")
            return False

        def _handle_login(self, body: bytes) -> None:
            fields = {k: v[0] for k, v in parse_qs(body.decode("utf-8", "replace")).items()}
            nxt = web_auth.safe_next(fields.get("next"), "/")
            wait = limiter.retry_after(self._ip())
            if wait:
                self._send(429, web_auth.login_page(LOGIN_TITLE, next_path=nxt, error=f"Too many failed attempts. Try again in {max(1, wait // 60)} min."))
                return
            if not auth_configured() or not link_ui_auth.verify(fields.get("username", ""), fields.get("password", "")):
                limiter.failure(self._ip())
                self._send(401, web_auth.login_page(LOGIN_TITLE, next_path=nxt, error="Wrong username or password.", configured=auth_configured()))
                return
            limiter.success(self._ip())
            sess = sessions.create(fields["username"])
            self._redirect(nxt, {"Set-Cookie": web_auth.set_cookie(SESSION_COOKIE, sess["id"], max_age=web_auth.MAX_SECONDS)})

        def _handle_logout(self, body: bytes) -> None:
            sess = sessions.get(web_auth.parse_cookies(self.headers.get("Cookie")).get(SESSION_COOKIE))
            if sess:
                token = self.headers.get("X-CSRF-Token") or (parse_qs(body.decode("utf-8", "replace")).get("csrf_token") or [None])[0]
                if not web_auth.csrf_ok(sess, token):
                    self._send(403, b"CSRF token missing or invalid", "text/plain; charset=utf-8")
                    return
                sessions.delete(sess["id"])
            self._redirect("/login", {"Set-Cookie": web_auth.set_cookie(SESSION_COOKIE, "", max_age=0)})

        def do_GET(self) -> None:
            path = urlsplit(self.path).path
            if path == "/login":
                query = parse_qs(urlsplit(self.path).query)
                nxt = web_auth.safe_next((query.get("next") or [""])[0], "/")
                self._send(200, web_auth.login_page(LOGIN_TITLE, next_path=nxt, configured=auth_configured()))
                return
            if not self._authorize():
                return
            if path == "/":
                self._respond(render_home())
            elif path.startswith("/connect/status/"):
                session_id = path.removeprefix("/connect/status/")
                self._serve_status(session_id)
            elif path.startswith("/accounts/") and path.endswith("/calendars"):
                email = unquote(path.removeprefix("/accounts/").removesuffix("/calendars"))
                self._serve_calendar_picker(email)
            else:
                self.send_response(404)
                self.end_headers()

        def do_POST(self) -> None:
            path = urlsplit(self.path).path
            content_length = int(self.headers.get("Content-Length", 0))
            if content_length > 1_000_000:
                self._send(413, b"request too large", "text/plain; charset=utf-8")
                return
            body = self.rfile.read(content_length) if content_length else b""
            if path == "/login":
                self._handle_login(body)
                return
            if path == "/logout":
                self._handle_logout(body)
                return
            if not self._authorize():
                return
            if not self._csrf_ok(parse_qs(body.decode("utf-8", "replace"))):
                return

            if path == "/connect/start":
                self._handle_connect_start()
            elif path.startswith("/accounts/") and path.endswith("/calendars"):
                email = unquote(path.removeprefix("/accounts/").removesuffix("/calendars"))
                self._handle_save_calendars(email, body)
            elif path.startswith("/accounts/") and path.endswith("/disconnect"):
                email = unquote(path.removeprefix("/accounts/").removesuffix("/disconnect"))
                self._handle_disconnect(email)
            else:
                self.send_response(404)
                self.end_headers()

        def _handle_connect_start(self) -> None:
            client = _load_client()
            if client is None:
                self._respond(render_setup_needed())
                return
            client_id, client_secret = client
            try:
                device = request_device_code(client_id, DEFAULT_SCOPES)
            except DeviceFlowError as exc:
                self._respond(render_connect_status({"status": "error", "message": str(exc)}))
                return

            session_id = secrets.token_urlsafe(16)
            pending[session_id] = {
                "status": "pending",
                "device": device,
                "client_id": client_id,
                "client_secret": client_secret,
                "next_poll_at": time.monotonic(),
            }
            self._redirect(f"/connect/status/{session_id}")

        def _serve_status(self, session_id: str) -> None:
            state = pending.get(session_id)
            if state is None:
                self.send_response(404)
                self.end_headers()
                return
            _poll_pending(session_id)
            self._respond(render_connect_status(state))

        def _serve_calendar_picker(self, email: str) -> None:
            try:
                creds = read_sync.load_credentials_for_account(email)
                calendars = read_sync.list_calendars(creds)
            except Exception as exc:  # noqa: BLE001 -- shown to the user, not swallowed
                self._respond(_page("Error", f"<h1>Couldn't load calendars</h1><p>{_esc(exc)}</p><p><a class='button secondary' href='/'>Back</a></p>"))
                return
            selected = set(calendar_accounts.selected_calendars(email))
            self._respond(render_calendar_picker(email, calendars, selected))

        def _handle_save_calendars(self, email: str, body: bytes) -> None:
            fields = parse_qs(body.decode("utf-8"))
            calendar_ids = fields.get("calendar_id", [])
            calendar_accounts.set_selected_calendars(email, calendar_ids)
            self._redirect("/")

        def _handle_disconnect(self, email: str) -> None:
            read_sync.remove_token(email)
            calendar_accounts.remove_account(email)
            self._redirect("/")

        def _respond(self, body: bytes) -> None:
            if self.session:
                # Every form on every page carries the session's CSRF token, plus a logout.
                hidden = f'<input type="hidden" name="csrf_token" value="{self.session["csrf"]}">'.encode()
                body = body.replace(b"</form>", hidden + b"</form>")
                logout = (f'<form method="post" action="/logout" style="text-align:right;margin:0 0 1rem">'
                          f'<span class="muted">Signed in as {_esc(self.session["user"])}</span> '
                          f'<input type="hidden" name="csrf_token" value="{self.session["csrf"]}">'
                          f'<button class="secondary" type="submit">Log out</button></form>').encode()
                body = body.replace(b"<main>", b"<main>\n" + logout, 1)
            self._send(200, body)

        def _send(self, status: int, body: bytes, content_type: str = "text/html; charset=utf-8",
                  headers: dict[str, str] | None = None) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()
            self.wfile.write(body)

        def _redirect(self, location: str, headers: dict[str, str] | None = None) -> None:
            self.send_response(303)
            self.send_header("Location", location)
            for k, v in (headers or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def log_message(self, format: str, *args: Any) -> None:
            pass

    return Handler


def main() -> int:
    parser = argparse.ArgumentParser(description="Web UI for linking Google accounts for generic calendar sync")
    parser.add_argument("--port", type=int, default=8200, help="Local HTTP port (default: 8200)")
    parser.add_argument("--client-secret", type=Path, default=DEFAULT_CLIENT_SECRET_PATH)
    args = parser.parse_args()

    server = ThreadingHTTPServer(("0.0.0.0", args.port), make_handler(args.client_secret))
    print(f"Serving Google Calendar account linking UI on http://0.0.0.0:{args.port}/")
    if link_ui_auth.is_configured():
        print("Login required (credentials in keyring service 'infobuddy-link-ui').")
    else:
        print("WARNING: no login configured -- anyone on the LAN can use this UI. Run manage_link_ui_credentials.py.")
    server.serve_forever()


if __name__ == "__main__":
    raise SystemExit(main())
