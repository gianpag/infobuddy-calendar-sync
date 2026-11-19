"""Tracks the outcome of the most recent run_pipeline.py run.

Written by run_pipeline.py; read by menubar_app.py (macOS) and tray_app.py (Windows), or
anything else that wants to show sync status. Kept as its own tiny module so the UI layer
never has to import pipeline internals -- only this small state-file contract, plus the
shared display-formatting helpers below (so the two platform UIs don't duplicate the same
"2h ago" / "Last sync: ..." logic). Lives in the same gitignored state directory as the
change-detection baseline (`.infomentor-state/` by default), since it's per-deployment
operational state, not extracted school data.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

STATUS_FILENAME = "sync_status.json"

# Exit code google_calendar_sync.py uses for "Google authorization needed, can't prompt
# here" -- lives here (dependency-free) so run_pipeline.py can check it cheaply.
EXIT_AUTH_REQUIRED = 3
# infomentor_extract.py's exit code for "a human must log in (BankID/SSO)".
EXIT_MANUAL_LOGIN_REQUIRED = 4


def _status_path(state_dir: Path) -> Path:
    return state_dir / STATUS_FILENAME


def read_status(state_dir: Path) -> dict[str, Any] | None:
    path = _status_path(state_dir)
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def mark_running(state_dir: Path) -> None:
    _write(state_dir, {
        "status": "running",
        "pid": os.getpid(),
        "started_at": _now(),
        "finished_at": None,
        "error": None,
    })


def mark_finished(
    state_dir: Path,
    *,
    success: bool,
    error: str | None = None,
    needs_relink: bool = False,
    needs_manual_login: bool = False,
) -> None:
    """`needs_relink` flags that the Google Calendar push account must be re-authorized
    (see google_calendar_sync.py --link-device); UIs/health checks surface it."""
    previous = read_status(state_dir) or {}
    _write(state_dir, {
        "needs_relink": needs_relink,
        "needs_manual_login": needs_manual_login,
        "status": "success" if success else "failed",
        "pid": None,
        "started_at": previous.get("started_at"),
        "finished_at": _now(),
        "error": None if success else error,
    })


def relative_time(iso_timestamp: str | None) -> str:
    if not iso_timestamp:
        return "never"
    then = datetime.fromisoformat(iso_timestamp)
    seconds = int((datetime.now(timezone.utc) - then).total_seconds())
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    return f"{hours // 24}d ago"


def describe_status(status: dict[str, Any] | None) -> str:
    """One-line human summary, e.g. "Last sync: 2h ago (ok)" -- shared by every UI."""
    if not status:
        return "Last sync: never"
    state = status.get("status")
    when = relative_time(status.get("finished_at") or status.get("started_at"))
    if state == "success":
        return f"Last sync: {when} (ok)"
    if state == "failed" and status.get("needs_manual_login"):
        return f"Last sync {when}: InfoMentor needs a manual (BankID) login"
    if state == "failed" and status.get("needs_relink"):
        return f"Last sync {when}: Google Calendar needs re-linking (google_calendar_sync.py --link-device)"
    if state == "failed":
        return f"Last sync failed {when}: {status.get('error') or 'unknown error'}"
    return "Syncing now…"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _write(state_dir: Path, payload: dict[str, Any]) -> None:
    state_dir.mkdir(parents=True, exist_ok=True)
    _status_path(state_dir).write_text(
        json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8"
    )
