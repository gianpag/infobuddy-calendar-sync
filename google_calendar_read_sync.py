#!/usr/bin/env python3
"""Pulls events from every selected calendar across every linked Google account (see
calendar_accounts.py) and writes a normalized JSON file matching InkBuddy's
WIDGET_SPEC.md `calendar` widget payload shape: `{events: [{title, start, end, all_day,
location?, source_id}]}`. This is a distinct, generic capability from
google_calendar_sync.py (which *pushes* InfoMentor study items *to* a per-child Google
Calendar) -- this one *reads* from arbitrary personal/work Google accounts, for a
non-InfoMentor e-paper/dashboard consumer. Deliberately has no InfoMentor dependency at
all (CLAUDE.md principle 2: don't couple extraction to external integrations, and this
isn't extraction in the first place).

Accounts are linked via calendar_link_ui.py's web UI (OAuth Device Authorization Grant
-- see google_device_flow.py for why: this needs to run on a headless host with no local
browser). Each linked account's refresh token lives exclusively in the OS keychain
(CREDENTIAL_SERVICE_NAME below), never a plaintext file -- same storage rule as
google_calendar_sync.py's OAuth client, just a different keychain service name/scope
since this is a separate (read-only) OAuth grant for a separate purpose.

If no accounts are linked yet, sync_all() is a no-op (doesn't touch the output file) --
there's nothing to report yet, and overwriting a possibly-still-valid existing file with
an empty one would be worse than just leaving it alone until something is actually
configured.
"""
from __future__ import annotations

import argparse
import json
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import keyring
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build

import calendar_accounts
from keyring_linux_bootstrap import bootstrap_linux_keyring

bootstrap_linux_keyring()

# Read-only calendar access + enough to resolve the linked account's own email address
# (needed for calendar_accounts.py bookkeeping and for a readable account list in the
# web UI) -- deliberately narrower than google_calendar_sync.py's full read/write
# `calendar` scope, since this integration never creates/modifies anything.
SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/userinfo.email",
]
CREDENTIAL_SERVICE_NAME = "infomentor-extractor-generic-calendar-read"
USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"

DEFAULT_OUTPUT_PATH = Path("out/generic-calendar.json")
DEFAULT_SYNC_DAYS = 60


class AccountNotLinkedError(Exception):
    """Raised when load_credentials_for_account() is asked for an email with no stored
    token -- distinct from a stale/revoked token (RefreshError), which is recoverable by
    re-linking rather than a programming/config error."""


def store_token(email: str, creds: Credentials) -> None:
    keyring.set_password(CREDENTIAL_SERVICE_NAME, email, creds.to_json())


def remove_token(email: str) -> None:
    try:
        keyring.delete_password(CREDENTIAL_SERVICE_NAME, email)
    except keyring.errors.PasswordDeleteError:
        pass  # already gone -- fine, disconnect is idempotent


def load_credentials_for_account(email: str) -> Credentials:
    """Loads a linked account's cached token from the keychain, refreshing it if
    expired. Raises AccountNotLinkedError if nothing is stored for this email, or
    RefreshError (propagated, not swallowed) if the refresh token itself was
    revoked/expired -- the caller (sync_all()) treats that as "this account needs to be
    re-linked" and skips it rather than crashing the whole sync."""
    stored = keyring.get_password(CREDENTIAL_SERVICE_NAME, email)
    if not stored:
        raise AccountNotLinkedError(email)

    creds = Credentials.from_authorized_user_info(json.loads(stored), SCOPES)
    if creds.valid:
        return creds
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())  # raises RefreshError if the refresh token is dead
        store_token(email, creds)
        return creds
    return creds


def fetch_account_email(access_token: str) -> str:
    """Resolves the email address for a just-authorized token -- called once right
    after a successful device-flow consent, before anything is stored under a real key
    in the keychain (see calendar_link_ui.py)."""
    req = urllib.request.Request(USERINFO_URL, headers={"Authorization": f"Bearer {access_token}"})
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = json.loads(resp.read().decode("utf-8"))
    return body["email"]


def list_calendars(creds: Credentials) -> list[dict[str, str]]:
    """[{id, summary}, ...] for every calendar this account can see (not just ones
    already selected) -- used by the web UI's calendar picker."""
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    calendars: list[dict[str, str]] = []
    page_token = None
    while True:
        response = service.calendarList().list(pageToken=page_token).execute()
        for item in response.get("items", []):
            calendars.append({"id": item["id"], "summary": item.get("summary", item["id"])})
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return calendars


def _normalize_event(raw: dict[str, Any], *, account_email: str, calendar_id: str) -> dict[str, Any] | None:
    start = raw.get("start", {})
    end = raw.get("end", {})
    all_day = "date" in start
    start_value = start.get("date") if all_day else start.get("dateTime")
    end_value = end.get("date") if all_day else end.get("dateTime")
    if start_value is None or end_value is None:
        return None  # a cancelled/malformed event with no start or end -- skip it

    event: dict[str, Any] = {
        "title": raw.get("summary", "(no title)"),
        "start": start_value,
        "end": end_value,
        "all_day": all_day,
        "source_id": f"google:{account_email}:{calendar_id}:{raw['id']}",
    }
    location = raw.get("location")
    if location:
        event["location"] = location
    return event


def fetch_events(creds: Credentials, calendar_id: str, *, days: int, account_email: str) -> list[dict[str, Any]]:
    service = build("calendar", "v3", credentials=creds, cache_discovery=False)
    now = datetime.now(timezone.utc)
    time_min = now.isoformat()
    time_max = (now + timedelta(days=days)).isoformat()

    events: list[dict[str, Any]] = []
    page_token = None
    while True:
        response = service.events().list(
            calendarId=calendar_id,
            timeMin=time_min,
            timeMax=time_max,
            singleEvents=True,
            orderBy="startTime",
            pageToken=page_token,
        ).execute()
        for raw in response.get("items", []):
            normalized = _normalize_event(raw, account_email=account_email, calendar_id=calendar_id)
            if normalized is not None:
                events.append(normalized)
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return events


def sync_all(output_path: Path = DEFAULT_OUTPUT_PATH, *, days: int = DEFAULT_SYNC_DAYS) -> dict[str, Any]:
    """Returns a small report dict ({"accounts_synced", "accounts_skipped", "event_count"})
    for the CLI/scheduler to log -- doesn't raise for a single broken account (expired
    refresh token, revoked access): that account's events are just omitted and its email
    is listed under "accounts_skipped", same "one bad source doesn't blank everything
    else" philosophy as aggregator.py's provider-failure isolation on the InkBuddy side."""
    emails = calendar_accounts.list_account_emails()
    if not emails:
        return {"accounts_synced": [], "accounts_skipped": [], "event_count": 0, "skipped_write": True}

    all_events: list[dict[str, Any]] = []
    accounts_synced: list[str] = []
    accounts_skipped: list[str] = []

    for email in emails:
        calendar_ids = calendar_accounts.selected_calendars(email)
        if not calendar_ids:
            continue
        try:
            creds = load_credentials_for_account(email)
        except (AccountNotLinkedError, RefreshError):
            accounts_skipped.append(email)
            continue

        try:
            for calendar_id in calendar_ids:
                all_events.extend(fetch_events(creds, calendar_id, days=days, account_email=email))
            accounts_synced.append(email)
        except urllib.error.URLError:
            accounts_skipped.append(email)

    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps({"events": all_events}, ensure_ascii=False, indent=2), encoding="utf-8")

    return {
        "accounts_synced": accounts_synced,
        "accounts_skipped": accounts_skipped,
        "event_count": len(all_events),
        "skipped_write": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--days", type=int, default=DEFAULT_SYNC_DAYS)
    args = parser.parse_args()

    report = sync_all(args.output, days=args.days)
    if report["skipped_write"]:
        print("No Google accounts linked yet -- run calendar_link_ui.py to connect one. Nothing written.")
        return 0

    print(f"Synced {report['event_count']} event(s) from {len(report['accounts_synced'])} account(s) -> {args.output}")
    if report["accounts_skipped"]:
        print(f"Skipped (needs re-linking): {', '.join(report['accounts_skipped'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
