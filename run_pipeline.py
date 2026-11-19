#!/usr/bin/env python3
"""One-command InfoMentor study-control pipeline.

Runs:
  1. InfoMentor extraction
  2. Study Brief generation
  3. Stateful change detection

The first run creates the change-detection baseline. Later runs emit only meaningful
changes while also regenerating the full current Study Brief.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import date
from pathlib import Path

from local_config import default_client_secret_path
from sync_status import EXIT_AUTH_REQUIRED, EXIT_MANUAL_LOGIN_REQUIRED, mark_finished, mark_running


def run(cmd: list[str]) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True)


def run_step(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.run(cmd).returncode


def run_calendar(cmd: list[str]) -> int:
    print("+", " ".join(cmd))
    return subprocess.run(cmd).returncode


def main() -> int:
    p = argparse.ArgumentParser(description="Run the full InfoMentor study-control pipeline")
    p.add_argument("--start", type=date.fromisoformat, help="Extraction start date YYYY-MM-DD")
    p.add_argument("--end", type=date.fromisoformat, help="Extraction end date YYYY-MM-DD")
    p.add_argument("--headless", action="store_true", help="Use an existing authenticated browser session")
    p.add_argument("--profile", default=".infomentor-browser-profile")
    p.add_argument("--output-dir", default="out")
    p.add_argument("--state-dir", default=".infomentor-state")
    p.add_argument("--days", type=int, default=28, help="Study Brief horizon")
    p.add_argument(
        "--username",
        help="InfoMentor username for automated (non-BankID) login. Defaults to whatever manage_credentials.py last stored, if any.",
    )
    p.add_argument("--sync-calendar", action="store_true", help="Also sync tests/homework/events to Google Calendar")
    p.add_argument(
        "--google-client-secret", type=Path,
        help="Path to your Google Cloud OAuth client_secret.json. Defaults to ./client_secret.json if that file exists.",
    )
    p.add_argument("--google-account", default="default", help="Label for the Google account's OS keychain entry")
    p.add_argument(
        "--sync-timetable", action="store_true",
        help="Also sync the regular timetable into a separate per-child 'Timetable' calendar (requires --sync-calendar)",
    )
    args = p.parse_args()

    if args.sync_timetable and not args.sync_calendar:
        p.error("--sync-timetable requires --sync-calendar")

    google_client_secret = default_client_secret_path(args.google_client_secret)
    if args.sync_calendar and not google_client_secret:
        p.error("--sync-calendar requires --google-client-secret (or a client_secret.json in the current directory)")

    root = Path(__file__).resolve().parent
    out = Path(args.output_dir)
    out.mkdir(parents=True, exist_ok=True)

    current = out / "infomentor-current.json"
    brief_json = out / "study-brief.json"
    brief_md = out / "study-brief.md"
    changes_json = out / "changes.json"
    changes_md = out / "changes.md"

    extract_cmd = [
        sys.executable, str(root / "infomentor_extract.py"),
        "--profile", args.profile,
        "--output", str(current),
    ]
    if args.start:
        extract_cmd += ["--start", args.start.isoformat()]
    if args.end:
        extract_cmd += ["--end", args.end.isoformat()]
    if args.headless:
        extract_cmd.append("--headless")
    if args.username:
        extract_cmd += ["--username", args.username]
    state_dir = Path(args.state_dir)
    mark_running(state_dir)
    try:
        extract_rc = run_step(extract_cmd)
        if extract_rc == EXIT_MANUAL_LOGIN_REQUIRED:
            # No fresh extraction, so downstream outputs are left as they were rather
            # than regenerated from stale data.
            error = "InfoMentor needs a manual (BankID) login; see infomentor_extract.py's error output"
            mark_finished(state_dir, success=False, error=error, needs_manual_login=True)
            print(f"\n{error}", file=sys.stderr)
            return 1
        if extract_rc != 0:
            raise subprocess.CalledProcessError(extract_rc, extract_cmd)

        brief_cmd = [
            sys.executable, str(root / "study_brief.py"), str(current),
            "--days", str(args.days),
            "--json-output", str(brief_json),
            "--markdown-output", str(brief_md),
        ]
        run(brief_cmd)

        change_cmd = [
            sys.executable, str(root / "change_watch.py"), str(current),
            "--state-dir", args.state_dir,
            "--json-output", str(changes_json),
            "--markdown-output", str(changes_md),
        ]
        run(change_cmd)

        if args.sync_calendar:
            calendar_cmd = [
                sys.executable, str(root / "google_calendar_sync.py"), str(current),
                "--client-secret", str(google_client_secret),
                "--account", args.google_account,
                "--state-file", str(Path(args.state_dir) / "calendar_sync.json"),
            ]
            if args.sync_timetable:
                calendar_cmd.append("--sync-timetable")
            if args.headless:
                calendar_cmd.append("--non-interactive")
            # Calendar push is an optional external integration (CLAUDE.md layering):
            # its failure must not hide that extraction/brief/changes succeeded, and it
            # runs last so those outputs are already written. Still reported as a
            # failed run (nonzero exit) so it's noticed.
            calendar_rc = run_calendar(calendar_cmd)
            if calendar_rc != 0:
                needs_relink = calendar_rc == EXIT_AUTH_REQUIRED
                error = (
                    "Google Calendar push needs re-linking: run `python google_calendar_sync.py --link-device --account "
                    f"{args.google_account}`" if needs_relink else f"Google Calendar push failed (exit {calendar_rc})"
                )
                mark_finished(state_dir, success=False, error=error, needs_relink=needs_relink)
                print(f"\n{error}\nOther pipeline outputs were written successfully.", file=sys.stderr)
                return 1
    except subprocess.CalledProcessError as exc:
        mark_finished(state_dir, success=False, error=str(exc))
        raise

    mark_finished(state_dir, success=True)

    print("\nPipeline complete")
    print(f"  Current extraction: {current}")
    print(f"  Study Brief:       {brief_md}")
    print(f"  Study Brief JSON:  {brief_json}")
    print(f"  Changes:           {changes_md}")
    print(f"  Changes JSON:      {changes_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
