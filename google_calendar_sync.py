#!/usr/bin/env python3
"""Sync selected InfoMentor study items to Google Calendar.

Consumes the same item-construction logic as the Study Brief (study_brief.py) rather than
talking to InfoMentor directly (CLAUDE.md principle 2: keep extraction/normalization
separate from external integrations). One Google Calendar per child is found or created.
Sync is idempotent: a local state file maps each item's stable identity (change_watch.py's
`stable_id`) to the Calendar event it created, so re-running updates events in place
instead of duplicating them, and genuinely-removed items get their event deleted.

Per ROADMAP.md Phase 3, only tests/exams/quizzes/homework/school events are synced by
default -- not weekly goals, not every planning note, not regular timetable lessons. The
regular timetable can be explicitly opted into with `--sync-timetable`, which syncs exact
daily lesson instances (not a recurring pattern, so substitute teachers/one-off changes
are reflected accurately) into a separate per-child "Timetable" calendar, so it can be
shown/hidden independently of tests/homework/events.

OAuth: each deployment brings its own Google Cloud OAuth client (`--client-secret`, a
local file, never committed -- see CLAUDE.md "Distributable design"). The one-time
browser consent's refresh token is cached in the OS keychain, never in a plaintext file.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

import keyring
from google.auth.exceptions import RefreshError
from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from change_watch import child_key, stable_id
from keyring_linux_bootstrap import bootstrap_linux_keyring
from local_config import default_client_secret_path
from log_privacy import log_first_name
from sync_status import EXIT_AUTH_REQUIRED  # distinct "needs re-linking" exit code
from study_brief import KIND_LABEL, assessment_items, display_name, finalize_item, merge_duplicates, normalize_space, parse_date

bootstrap_linux_keyring()

SCOPES = ["https://www.googleapis.com/auth/calendar"]
CREDENTIAL_SERVICE_NAME = "infomentor-extractor-google-calendar"
DEFAULT_SYNC_KINDS = {"test", "exam", "quiz", "homework", "school_event"}
DEFAULT_DEVICE_CLIENT_SECRET_PATH = Path("client_secret_device.json")



class CalendarAuthRequired(Exception):
    """No usable cached token and interactive browser consent isn't possible here."""


def relink_hint(account_label: str) -> str:
    return (
        f"Google Calendar authorization for account '{account_label}' is missing or expired, and "
        "this is a non-interactive/headless run (no browser). Re-link it via device flow:\n"
        f"  python google_calendar_sync.py --link-device --account {account_label}\n"
        "(uses client_secret_device.json -- a 'TVs and Limited Input devices' OAuth client; "
        "see README 'Re-linking the push account on a headless host')."
    )


def can_open_browser() -> bool:
    """False on a headless host: no attached terminal (systemd/launchd/Task Scheduler)
    or, on Linux, no graphical display -- run_local_server() would fail with
    webbrowser.Error there instead of prompting anyone."""
    if not sys.stdin or not sys.stdin.isatty():
        return False
    if sys.platform.startswith("linux") and not (os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")):
        return False
    return True


def load_google_credentials(client_secret_path: Path, account_label: str, *, interactive: bool | None = None) -> Credentials:
    """Load a cached refresh token from the OS keychain, or run the one-time browser
    consent flow and cache the result. Never reads or writes a plaintext token file.

    When `interactive` is False (or auto-detected as impossible, see can_open_browser()),
    raises CalendarAuthRequired instead of trying to open a browser."""
    stored = keyring.get_password(CREDENTIAL_SERVICE_NAME, account_label)
    creds: Credentials | None = None
    if stored:
        creds = Credentials.from_authorized_user_info(json.loads(stored), SCOPES)

    if creds and creds.valid:
        return creds
    if creds and creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            keyring.set_password(CREDENTIAL_SERVICE_NAME, account_label, creds.to_json())
            return creds
        except RefreshError:
            # The refresh token itself was revoked/expired (e.g. Google's 7-day limit
            # for an OAuth client still in "Testing" publishing status, or the user
            # revoked access) -- fall back to the interactive consent flow below rather
            # than crashing, same "fall back to manual rather than fail" pattern as
            # InfoMentor's own login (CLAUDE.md principle 3).
            print("Cached Google refresh token was rejected (expired/revoked).")

    if interactive is None:
        interactive = can_open_browser()
    if not interactive:
        raise CalendarAuthRequired(relink_hint(account_label))

    flow = InstalledAppFlow.from_client_secrets_file(str(client_secret_path), SCOPES)
    creds = flow.run_local_server(port=0)
    keyring.set_password(CREDENTIAL_SERVICE_NAME, account_label, creds.to_json())
    return creds


def link_account_via_device_flow(
    device_client_secret_path: Path,
    account_label: str,
    *,
    sleep_fn=time.sleep,
    out=print,
) -> Credentials:
    """(Re)authorize the push account with OAuth's Device Authorization Grant -- works
    on a headless host; consent happens in any other device's browser. Reuses
    google_device_flow.py (same as the generic read sync). Stores the result in the
    same keychain entry load_google_credentials() reads."""
    from google_device_flow import request_device_code, wait_for_authorization

    raw = json.loads(Path(device_client_secret_path).read_text(encoding="utf-8"))
    section = raw.get("installed") or raw.get("web") or raw
    client_id, client_secret = section["client_id"], section["client_secret"]

    device = request_device_code(client_id, SCOPES)
    out(f"To authorize Google Calendar push for account '{account_label}':")
    out(f"  1. Visit: {device.verification_url}")
    out(f"  2. Enter code: {device.user_code}")
    out(f"Waiting up to {device.expires_in // 60} minutes for approval...")
    sys.stdout.flush()  # so a backgrounded run's log shows the code immediately
    token = wait_for_authorization(client_id, client_secret, device, sleep_fn=sleep_fn)
    creds = Credentials(
        token=token.get("access_token"),
        refresh_token=token.get("refresh_token"),
        token_uri="https://oauth2.googleapis.com/token",
        client_id=client_id,
        client_secret=client_secret,
        scopes=SCOPES,
    )
    keyring.set_password(CREDENTIAL_SERVICE_NAME, account_label, creds.to_json())
    out(f"Linked. Token stored in the OS keychain ({CREDENTIAL_SERVICE_NAME} / {account_label}).")
    return creds


def find_or_create_calendar(service: Any, *, summary: str, state: dict[str, Any]) -> str:
    known_id = state.setdefault("calendars", {}).get(summary)
    if known_id:
        try:
            service.calendars().get(calendarId=known_id).execute()
            return known_id
        except Exception:
            pass  # stale mapping; fall through to search/create

    page_token = None
    while True:
        resp = service.calendarList().list(pageToken=page_token).execute()
        for entry in resp.get("items", []):
            if entry.get("summary") == summary:
                state["calendars"][summary] = entry["id"]
                return entry["id"]
        page_token = resp.get("nextPageToken")
        if not page_token:
            break

    created = service.calendars().insert(body={"summary": summary}).execute()
    state["calendars"][summary] = created["id"]
    return created["id"]


def event_title(item: dict[str, Any]) -> str:
    label = KIND_LABEL.get(item.get("kind"), str(item.get("kind") or "").upper())
    return f"{label}: {normalize_space(item.get('title'))}"


def build_event_description(item: dict[str, Any]) -> str:
    lines: list[str] = []
    subjects = [normalize_space(s) for s in item.get("subjects") or [] if normalize_space(s)]
    what = item.get("what_to_study") or []
    prep = item.get("preparation_instructions") or []
    resources = item.get("resources") or []

    if subjects:
        lines.append("Subject: " + ", ".join(subjects))
        lines.append("")
    if what:
        lines.append("What to study:")
        lines.extend(f"- {normalize_space(x)}" for x in what)
        lines.append("")
    if prep:
        lines.append("Preparation:")
        lines.extend(f"- {normalize_space(x)}" for x in prep)
        lines.append("")
    if resources:
        lines.append("Resources:")
        for r in resources:
            title = normalize_space(r.get("title")) or normalize_space(r.get("provider")) or "Resource"
            url = r.get("url")
            lines.append(f"- {title}: {url}" if url else f"- {title}")
        lines.append("")

    source_url = (item.get("source") or {}).get("url")
    if source_url:
        lines.append(f"InfoMentor: {source_url}")
    if item.get("derived_from_planning"):
        lines.append("(Date discovered from teacher planning text, not a calendar event.)")
    elif item.get("derived_from_description"):
        lines.append("(Date discovered inside another InfoMentor item's description.)")

    return "\n".join(lines).strip()


def content_fingerprint(item: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "title": item.get("title"),
            "date": item.get("date"),
            "kind": item.get("kind"),
            "subjects": item.get("subjects"),
            "what_to_study": item.get("what_to_study"),
            "preparation_instructions": item.get("preparation_instructions"),
            "resources": [[r.get("title"), r.get("url")] for r in item.get("resources") or []],
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def event_body(item: dict[str, Any]) -> dict[str, Any]:
    d = parse_date(item.get("date"))
    return {
        "summary": event_title(item),
        "description": build_event_description(item),
        "start": {"date": d.isoformat()},
        "end": {"date": (d + timedelta(days=1)).isoformat()},
    }


def sync_child(
    service: Any,
    child: dict[str, Any],
    *,
    reference: date,
    kinds: set[str],
    sync_days: int,
    state: dict[str, Any],
    dry_run: bool,
) -> list[str]:
    log: list[str] = []
    name = display_name(child.get("pupil") or {})
    calendar_summary = f"School – {name}" if name else "School"

    all_items = merge_duplicates(assessment_items(child, reference=reference))
    syncable: dict[str, dict[str, Any]] = {}
    for item in all_items:
        d = parse_date(item.get("date"))
        if not d or item.get("kind") not in kinds:
            continue
        item = finalize_item(item, child)
        syncable[stable_id(item)] = item

    mapping: dict[str, dict[str, Any]] = state.setdefault("items", {}).setdefault(child_key(child), {})
    calendar_id_box: list[str | None] = [None]

    def get_calendar_id() -> str:
        if calendar_id_box[0] is None:
            calendar_id_box[0] = find_or_create_calendar(service, summary=calendar_summary, state=state)
        return calendar_id_box[0]

    horizon_start = reference - timedelta(days=1)
    horizon_end = reference + timedelta(days=sync_days)
    for sid, item in syncable.items():
        d = parse_date(item.get("date"))
        if not (horizon_start <= d <= horizon_end):
            continue  # outside the sync horizon; leave any existing event untouched

        fingerprint = content_fingerprint(item)
        existing = mapping.get(sid)
        if existing and existing.get("fingerprint") == fingerprint:
            continue  # unchanged since last sync

        if dry_run:
            log.append(f"[dry-run] would {'update' if existing else 'create'}: {item.get('title')}")
            continue

        body = event_body(item)
        if existing:
            service.events().update(calendarId=existing["calendar_id"], eventId=existing["event_id"], body=body).execute()
            mapping[sid] = {**existing, "fingerprint": fingerprint}
            log.append(f"Updated: {item.get('title')}")
        else:
            calendar_id = get_calendar_id()
            created = service.events().insert(calendarId=calendar_id, body=body).execute()
            mapping[sid] = {"event_id": created["id"], "calendar_id": calendar_id, "fingerprint": fingerprint}
            log.append(f"Created: {item.get('title')}")

    # Cleanup: an item previously synced that is no longer present at all (any date, any
    # window) is a genuine disappearance, not a sync-horizon artifact -- remove its event.
    for sid in [s for s in mapping if s not in syncable]:
        entry = mapping.pop(sid)
        if dry_run:
            log.append(f"[dry-run] would delete event for removed item ({sid})")
            continue
        try:
            service.events().delete(calendarId=entry["calendar_id"], eventId=entry["event_id"]).execute()
            log.append(f"Deleted event for removed item ({sid})")
        except Exception as exc:
            log.append(f"Warning: failed to delete event {entry.get('event_id')}: {exc}")

    return log


def parse_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(str(value))
    except ValueError:
        return None


def lesson_stable_key(lesson: dict[str, Any]) -> str:
    """Timetable lessons carry no id from InfoMentor at all; a lesson's own start time
    uniquely identifies it (a pupil can't be in two lessons at once). Deliberately
    excludes subject/room/teacher so a substitution or room change updates the existing
    event in place instead of looking like the old lesson disappeared and a new one
    appeared."""
    return "lesson:" + hashlib.sha1(str(lesson.get("start") or "").encode("utf-8")).hexdigest()[:20]


def lesson_event_title(lesson: dict[str, Any]) -> str:
    return normalize_space(lesson.get("subject")) or normalize_space(lesson.get("subject_raw")) or "Lesson"


def lesson_event_description(lesson: dict[str, Any]) -> str:
    lines: list[str] = []
    subject_raw = normalize_space(lesson.get("subject_raw"))
    if subject_raw and subject_raw != lesson_event_title(lesson):
        lines.append(f"Timetable code: {subject_raw}")
    room = normalize_space(lesson.get("room"))
    if room:
        lines.append(f"Room: {room}")
    teachers = normalize_space(lesson.get("teachers"))
    if teachers:
        lines.append(f"Teacher(s): {teachers}")
    notes = normalize_space(lesson.get("notes"))
    if notes:
        lines.append(notes)
    return "\n".join(lines).strip()


def lesson_fingerprint(lesson: dict[str, Any]) -> str:
    payload = json.dumps(
        {
            "start": lesson.get("start"),
            "end": lesson.get("end"),
            "subject": lesson.get("subject"),
            "room": lesson.get("room"),
            "teachers": lesson.get("teachers"),
            "notes": lesson.get("notes"),
        },
        sort_keys=True,
        ensure_ascii=False,
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def lesson_event_body(lesson: dict[str, Any], *, timezone: str) -> dict[str, Any] | None:
    start, end = lesson.get("start"), lesson.get("end")
    if not start or not end:
        return None
    body: dict[str, Any] = {
        "summary": lesson_event_title(lesson),
        "description": lesson_event_description(lesson),
    }
    if lesson.get("all_day"):
        start_date, end_date = parse_datetime(start), parse_datetime(end)
        if not start_date:
            return None
        end_date = end_date or start_date
        body["start"] = {"date": start_date.date().isoformat()}
        body["end"] = {"date": (end_date.date() + timedelta(days=1)).isoformat()}
    else:
        body["start"] = {"dateTime": start, "timeZone": timezone}
        body["end"] = {"dateTime": end, "timeZone": timezone}
    return body


def sync_timetable(
    service: Any,
    child: dict[str, Any],
    *,
    reference: date,
    state: dict[str, Any],
    dry_run: bool,
    timezone: str,
) -> list[str]:
    """Sync exact daily lesson instances (not a recurring pattern) so substitute
    teachers/one-off schedule changes are always reflected accurately -- see
    CLAUDE.md/ARCHITECTURE.md: timetable sync is background context, explicitly opt-in
    (`--sync-timetable`), and lives in its own calendar so it can be shown/hidden
    independently of tests/homework/events."""
    log: list[str] = []
    name = display_name(child.get("pupil") or {})
    calendar_summary = f"School – {name} – Timetable" if name else "School – Timetable"

    lessons = (child.get("normalized") or {}).get("schedule") or []
    syncable: dict[str, dict[str, Any]] = {}
    for lesson in lessons:
        d = parse_datetime(lesson.get("start"))
        if not d:
            continue
        syncable[lesson_stable_key(lesson)] = lesson

    mapping: dict[str, dict[str, Any]] = state.setdefault("timetable", {}).setdefault(child_key(child), {})
    calendar_id_box: list[str | None] = [None]

    def get_calendar_id() -> str:
        if calendar_id_box[0] is None:
            calendar_id_box[0] = find_or_create_calendar(service, summary=calendar_summary, state=state)
        return calendar_id_box[0]

    for key, lesson in syncable.items():
        fingerprint = lesson_fingerprint(lesson)
        existing = mapping.get(key)
        if existing and existing.get("fingerprint") == fingerprint:
            continue  # unchanged since last sync

        title = lesson_event_title(lesson)
        if dry_run:
            log.append(f"[dry-run] would {'update' if existing else 'create'}: {lesson.get('start')} {title}")
            continue

        body = lesson_event_body(lesson, timezone=timezone)
        if not body:
            continue
        if existing:
            service.events().update(calendarId=existing["calendar_id"], eventId=existing["event_id"], body=body).execute()
            mapping[key] = {**existing, "fingerprint": fingerprint, "start": lesson.get("start")}
            log.append(f"Updated: {lesson.get('start')} {title}")
        else:
            calendar_id = get_calendar_id()
            created = service.events().insert(calendarId=calendar_id, body=body).execute()
            mapping[key] = {"event_id": created["id"], "calendar_id": calendar_id, "fingerprint": fingerprint, "start": lesson.get("start")}
            log.append(f"Created: {lesson.get('start')} {title}")

    # Cleanup: only remove events for lessons that are still upcoming but no longer
    # present (a genuine schedule change). A lesson that simply aged into the past as the
    # rolling extraction window moved forward is not a removal -- leave that history alone.
    now = datetime.combine(reference, datetime.min.time())
    for key in [k for k in mapping if k not in syncable]:
        entry = mapping[key]
        entry_start = parse_datetime(entry.get("start"))
        if entry_start and entry_start < now:
            continue  # historical lesson that aged out of the rolling window; leave it
        entry = mapping.pop(key)
        if dry_run:
            log.append(f"[dry-run] would delete event for changed/removed lesson ({key})")
            continue
        try:
            service.events().delete(calendarId=entry["calendar_id"], eventId=entry["event_id"]).execute()
            log.append(f"Deleted event for changed/removed lesson ({key})")
        except Exception as exc:
            log.append(f"Warning: failed to delete event {entry.get('event_id')}: {exc}")

    return log


def main() -> int:
    parser = argparse.ArgumentParser(description="Sync selected InfoMentor study items to Google Calendar")
    parser.add_argument("input", nargs="?", help="Normalized InfoMentor JSON (e.g. out/infomentor-current.json)")
    parser.add_argument(
        "--client-secret", type=Path,
        help="Path to your Google Cloud OAuth Desktop-app client_secret.json. Defaults to ./client_secret.json if that file exists.",
    )
    parser.add_argument("--link-device", action="store_true", help="(Re)link the Google account via device flow (headless-friendly) and exit")
    parser.add_argument("--device-client-secret", type=Path, default=DEFAULT_DEVICE_CLIENT_SECRET_PATH,
                        help="'TVs and Limited Input devices' OAuth client JSON for --link-device (default: client_secret_device.json)")
    parser.add_argument("--non-interactive", action="store_true",
                        help="Never open a browser for consent; fail fast with a re-link hint (exit code 3) instead")
    parser.add_argument("--account", default="default", help="Label for the Google account, used as the OS keychain key")
    parser.add_argument("--kinds", default=",".join(sorted(DEFAULT_SYNC_KINDS)), help="Comma-separated study-item kinds to sync")
    parser.add_argument("--days", type=int, default=60, help="Sync items due within this many days (default: 60)")
    parser.add_argument("--as-of", type=date.fromisoformat, help="Reference date YYYY-MM-DD (default: today)")
    parser.add_argument("--state-file", default=".infomentor-state/calendar_sync.json")
    parser.add_argument("--dry-run", action="store_true", help="Print planned changes without calling the Calendar API")
    parser.add_argument(
        "--sync-timetable", action="store_true",
        help="Also sync the regular timetable (exact daily lesson instances) into a separate 'Timetable' calendar per child",
    )
    parser.add_argument("--timezone", default="Europe/Stockholm", help="IANA timezone for timetable lesson times (default: Europe/Stockholm)")
    args = parser.parse_args()

    if args.link_device:
        if not args.device_client_secret.exists():
            parser.error(f"--link-device needs {args.device_client_secret} (a 'TVs and Limited Input devices' OAuth client)")
        link_account_via_device_flow(args.device_client_secret, args.account)
        return 0
    if not args.input:
        parser.error("input is required unless --link-device is set")

    client_secret = default_client_secret_path(args.client_secret)
    if not args.dry_run and not client_secret:
        parser.error("--client-secret is required unless --dry-run is set (or place a client_secret.json in the current directory)")

    payload = json.loads(Path(args.input).read_text(encoding="utf-8"))
    kinds = {k.strip() for k in args.kinds.split(",") if k.strip()}
    reference = args.as_of or date.today()

    state_path = Path(args.state_file)
    state = json.loads(state_path.read_text(encoding="utf-8")) if state_path.exists() else {}

    service = None
    if not args.dry_run:
        try:
            creds = load_google_credentials(
                client_secret, args.account, interactive=False if args.non_interactive else None
            )
        except CalendarAuthRequired as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return EXIT_AUTH_REQUIRED
        service = build("calendar", "v3", credentials=creds)

    for child in payload.get("children", []):
        name = display_name(child.get("pupil") or {})
        print(f"-- {log_first_name(name)} --")
        for line in sync_child(service, child, reference=reference, kinds=kinds, sync_days=args.days, state=state, dry_run=args.dry_run):
            print(" ", line)
        if args.sync_timetable:
            for line in sync_timetable(service, child, reference=reference, state=state, dry_run=args.dry_run, timezone=args.timezone):
                print(" ", line)

    if not args.dry_run:
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nState written to {state_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
