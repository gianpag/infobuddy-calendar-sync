"""Small local, non-secret config for the generic Google Calendar sync's linked
accounts -- which Google accounts are connected and which of each account's calendars
are selected to sync. Mirrors local_config.py's pattern (a small gitignored JSON file)
but kept as its own file/module rather than folded into local_config.py, since that
module's own docstring scopes it narrowly to the InfoMentor username default -- this is
a materially larger, separate concern.

Never stores a token/credential -- those live exclusively in the OS keychain (see
google_calendar_read_sync.py's CREDENTIAL_SERVICE_NAME). Account emails and calendar
ids/names are still personal enough to be gitignored, same as local_config.py's
.infomentor-config.json.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

ACCOUNTS_PATH = Path(".google-calendar-accounts.json")


def load_accounts() -> dict[str, Any]:
    if not ACCOUNTS_PATH.exists():
        return {}
    try:
        return json.loads(ACCOUNTS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_accounts(accounts: dict[str, Any]) -> None:
    ACCOUNTS_PATH.write_text(json.dumps(accounts, ensure_ascii=False, indent=2), encoding="utf-8")


def add_account(email: str) -> None:
    """Idempotent -- linking an already-linked account again just leaves its existing
    calendar selection alone."""
    accounts = load_accounts()
    accounts.setdefault(email, {"calendars": []})
    save_accounts(accounts)


def remove_account(email: str) -> None:
    accounts = load_accounts()
    accounts.pop(email, None)
    save_accounts(accounts)


def set_selected_calendars(email: str, calendar_ids: list[str]) -> None:
    accounts = load_accounts()
    accounts.setdefault(email, {})["calendars"] = calendar_ids
    save_accounts(accounts)


def list_account_emails() -> list[str]:
    return sorted(load_accounts().keys())


def selected_calendars(email: str) -> list[str]:
    return load_accounts().get(email, {}).get("calendars", [])
