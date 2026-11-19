"""Login sessions, CSRF tokens and failed-login rate limiting for calendar_link_ui.py.
Stdlib only, in-memory, so a restart logs everyone out (acceptable for a single-admin
LAN tool).

- Sessions: a random 256-bit id kept in an HttpOnly, SameSite=Strict cookie. The
  `Secure` flag is added when INFOBUDDY_COOKIE_SECURE=1, which matters only behind
  HTTPS; the default deployment is plain-http LAN. Idle timeout is IDLE_SECONDS,
  absolute lifetime MAX_SECONDS.
- CSRF: each session carries its own token. A state-changing request made with a
  session must echo it back in the X-CSRF-Token header or a `csrf_token` form field.
  Basic-auth requests (scripts) are exempt: the server never sends a Basic challenge
  to browsers, so a browser never caches those credentials or attaches them
  cross-site.
- Rate limit: MAX_FAILURES failed logins per client IP within WINDOW_SECONDS lock that
  IP out for LOCKOUT_SECONDS. This applies to the login form and to Basic auth.

Duplicated from InkBuddy's feed_service/web_auth.py, the same way
keyring_linux_bootstrap.py is: separate repos, no cross-imports.
"""
from __future__ import annotations

import hmac
import html
import os
import secrets
import threading
import time
from typing import Any
from urllib.parse import quote

IDLE_SECONDS = 12 * 3600
MAX_SECONDS = 7 * 24 * 3600
MAX_FAILURES = 5
WINDOW_SECONDS = 15 * 60
LOCKOUT_SECONDS = 15 * 60


def cookie_secure() -> bool:
    return os.environ.get("INFOBUDDY_COOKIE_SECURE", "") == "1"


class SessionStore:
    def __init__(self, clock=time.monotonic) -> None:
        self._lock = threading.Lock()
        self._sessions: dict[str, dict[str, Any]] = {}
        self._clock = clock

    def create(self, user: str) -> dict[str, Any]:
        now = self._clock()
        sess = {"id": secrets.token_urlsafe(32), "user": user, "csrf": secrets.token_urlsafe(32),
                "created": now, "seen": now}
        with self._lock:
            # Drop expired sessions opportunistically.
            for sid in [s for s, v in self._sessions.items() if self._expired(v, now)]:
                del self._sessions[sid]
            self._sessions[sess["id"]] = sess
        return sess

    def _expired(self, sess: dict[str, Any], now: float) -> bool:
        return now - sess["seen"] > IDLE_SECONDS or now - sess["created"] > MAX_SECONDS

    def get(self, sid: str | None) -> dict[str, Any] | None:
        if not sid:
            return None
        now = self._clock()
        with self._lock:
            sess = self._sessions.get(sid)
            if sess is None:
                return None
            if self._expired(sess, now):
                del self._sessions[sid]
                return None
            sess["seen"] = now
            return sess

    def delete(self, sid: str | None) -> None:
        with self._lock:
            self._sessions.pop(sid or "", None)


class LoginRateLimiter:
    def __init__(self, clock=time.monotonic) -> None:
        self._lock = threading.Lock()
        self._failures: dict[str, list[float]] = {}
        self._locked_until: dict[str, float] = {}
        self._clock = clock

    def retry_after(self, key: str) -> int:
        """Seconds until `key` may try again (0 = allowed now)."""
        with self._lock:
            until = self._locked_until.get(key, 0)
            left = until - self._clock()
            if left <= 0:
                self._locked_until.pop(key, None)
                return 0
            return int(left) + 1

    def failure(self, key: str) -> None:
        now = self._clock()
        with self._lock:
            recent = [t for t in self._failures.get(key, []) if now - t < WINDOW_SECONDS] + [now]
            if len(recent) >= MAX_FAILURES:
                self._locked_until[key] = now + LOCKOUT_SECONDS
                recent = []
            self._failures[key] = recent

    def success(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


def parse_cookies(header: str | None) -> dict[str, str]:
    out: dict[str, str] = {}
    for part in (header or "").split(";"):
        k, sep, v = part.strip().partition("=")
        if sep and k:
            out[k] = v
    return out


def set_cookie(name: str, value: str, *, max_age: int, path: str = "/") -> str:
    attrs = [f"{name}={value}", f"Path={path}", f"Max-Age={max_age}", "HttpOnly", "SameSite=Strict"]
    if cookie_secure():
        attrs.append("Secure")
    return "; ".join(attrs)


def csrf_ok(sess: dict[str, Any], supplied: str | None) -> bool:
    return bool(supplied) and hmac.compare_digest(str(supplied), sess["csrf"])


def safe_next(value: str | None, default: str) -> str:
    """Only same-origin absolute paths, so the login page can't be used as an open redirect."""
    v = value or ""
    if not v.startswith("/") or v.startswith("//") or "\\" in v or "\n" in v or "\r" in v:
        return default
    return v


def login_page(title: str, *, next_path: str, error: str = "", configured: bool = True, action: str = "/login",
               setup_hint: str = "manage_link_ui_credentials.py") -> bytes:
    err = f'<p class="err" role="alert">{html.escape(error)}</p>' if error else ""
    if not configured:
        form = f"<p>No password is configured yet, so this UI is open. Set one with <code>{html.escape(setup_hint)}</code>.</p>"
    else:
        form = f"""<form method="post" action="{html.escape(action)}">
  <input type="hidden" name="next" value="{html.escape(next_path)}">
  <label>Username <input name="username" autocomplete="username" required autofocus></label>
  <label>Password <input name="password" type="password" autocomplete="current-password" required></label>
  <button type="submit">Sign in</button>
</form>"""
    doc = f"""<!DOCTYPE html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)} · Sign in</title>
<style>
 body {{ font-family: system-ui, -apple-system, "Segoe UI", sans-serif; background: #f4f5f7; margin: 0;
        display: grid; place-items: center; min-height: 100vh; color: #1c1e21; }}
 main {{ background: #fff; padding: 2rem 2.2rem; border-radius: 12px; box-shadow: 0 2px 10px rgba(0,0,0,.08); width: min(360px, 92vw); }}
 h1 {{ font-size: 1.3rem; margin: 0 0 1.2rem; }}
 label {{ display: block; margin-bottom: .9rem; font-size: .9rem; color: #444; }}
 input {{ display: block; width: 100%; box-sizing: border-box; margin-top: .3rem; padding: .55rem .6rem; font-size: 1rem;
         border: 1px solid #ccd; border-radius: 8px; }}
 button {{ width: 100%; padding: .65rem; font-size: 1rem; font-weight: 600; color: #fff; background: #4f46e5;
          border: 0; border-radius: 8px; cursor: pointer; }}
 .err {{ color: #b00020; background: #fdecea; padding: .5rem .7rem; border-radius: 8px; }}
</style></head>
<body><main><h1>{html.escape(title)}</h1>{err}{form}</main></body></html>"""
    return doc.encode("utf-8")


def login_redirect(next_path: str, login_path: str = "/login") -> str:
    return f"{login_path}?next={quote(next_path, safe='/')}"
