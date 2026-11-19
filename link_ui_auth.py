"""Credentials for calendar_link_ui.py's login. They live only in the OS keyring
(CLAUDE.md: keyring, never a plaintext file), under InfoBuddy's own service
LINK_UI_SERVICE_NAME, separate from InkBuddy's `inkbuddy-admin` entry even when the
same username and password are used.

Layout: (LINK_UI_SERVICE_NAME, USERNAME_KEY) holds the username, and
(LINK_UI_SERVICE_NAME, <username>) holds the password. Set them with
manage_link_ui_credentials.py.
"""
from __future__ import annotations

import base64
import binascii
import hmac

import keyring

LINK_UI_SERVICE_NAME = "infobuddy-link-ui"
USERNAME_KEY = "__username__"


def configured_username() -> str | None:
    try:
        return keyring.get_password(LINK_UI_SERVICE_NAME, USERNAME_KEY)
    except Exception:  # noqa: BLE001 -- unreadable keyring == not configured (logged by caller)
        return None


def is_configured() -> bool:
    user = configured_username()
    return bool(user) and keyring.get_password(LINK_UI_SERVICE_NAME, user) is not None


def set_credentials(username: str, password: str) -> None:
    if not username or not password:
        raise ValueError("username and password are required")
    keyring.set_password(LINK_UI_SERVICE_NAME, USERNAME_KEY, username)
    keyring.set_password(LINK_UI_SERVICE_NAME, username, password)


def verify(username: str, password: str) -> bool:
    expected_user = configured_username()
    if not expected_user or not hmac.compare_digest(username, expected_user):
        return False
    expected_pw = keyring.get_password(LINK_UI_SERVICE_NAME, expected_user)
    return expected_pw is not None and hmac.compare_digest(password, expected_pw)


def parse_basic(header: str | None) -> tuple[str, str] | None:
    if not header or not header.startswith("Basic "):
        return None
    try:
        user, sep, pw = base64.b64decode(header[6:].strip(), validate=True).decode("utf-8").partition(":")
    except (binascii.Error, UnicodeDecodeError):
        return None
    return (user, pw) if sep else None
