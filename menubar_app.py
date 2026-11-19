#!/usr/bin/env python3
"""macOS menu bar app: shows the last InfoMentor sync status and offers a manual trigger.

Reads .infomentor-state/sync_status.json (written by run_pipeline.py) to display status,
and launches run_pipeline.py as a subprocess for the "Sync Now" button. This file never
talks to InfoMentor, Google, or the keychain directly -- all of that stays in
run_pipeline.py, so this is pure UI plus a state-file read / subprocess launch.

macOS only (uses rumps). Run it directly:
    python menubar_app.py
or install it to start automatically at login:
    ./scheduling/macos/install_menubar.sh
"""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import rumps

import sync_status
from local_config import load_config

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".infomentor-state"
OUT_DIR = ROOT / "out"
LOG_FILE = ROOT / "logs" / "launchd.out.log"

DEFAULT_SYNC_ARGS = ["--headless", "--sync-calendar", "--sync-timetable"]
POLL_SECONDS = 30


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "bin" / "python"
    return str(venv_python) if venv_python.exists() else sys.executable


def _sync_args() -> list[str]:
    """Mirrors the scheduled launchd job's flags by default; override by setting
    "manual_sync_args" in .infomentor-config.json."""
    configured = load_config().get("manual_sync_args")
    return list(configured) if configured else list(DEFAULT_SYNC_ARGS)


class InfoMentorMenuBarApp(rumps.App):
    def __init__(self) -> None:
        super().__init__("InfoMentor Extractor", title="\U0001f4da")
        self.status_item = rumps.MenuItem("Last sync: checking…")
        self.sync_item = rumps.MenuItem("Sync Now", callback=self.sync_now)
        self.menu = [
            self.status_item,
            None,
            self.sync_item,
            None,
            rumps.MenuItem("Open Study Brief", callback=self.open_study_brief),
            rumps.MenuItem("Open Changes", callback=self.open_changes),
            rumps.MenuItem("View Log", callback=self.open_log),
        ]
        self._process: subprocess.Popen | None = None
        self.refresh(None)
        rumps.Timer(self.refresh, POLL_SECONDS).start()

    def refresh(self, _sender) -> None:
        if self._process is not None:
            if self._process.poll() is None:
                self.title = "\U0001f4da syncing…"
                self.status_item.title = "Syncing now…"
                return
            self._process = None
            self.sync_item.set_callback(self.sync_now)

        status = sync_status.read_status(STATE_DIR)
        if not status:
            self.title = "\U0001f4da"
            self.status_item.title = "Last sync: never"
            return

        state = status.get("status")
        relative = sync_status.relative_time(status.get("finished_at") or status.get("started_at"))
        self.status_item.title = sync_status.describe_status(status)
        if state == "success":
            self.title = f"\U0001f4da {relative}"
        elif state == "failed":
            self.title = f"\U0001f4da ⚠ {relative}"
        else:
            self.title = "\U0001f4da syncing…"

    def sync_now(self, sender) -> None:
        if self._process is not None and self._process.poll() is None:
            return
        sender.set_callback(None)
        self.title = "\U0001f4da syncing…"
        self.status_item.title = "Syncing now…"
        cmd = [_python_executable(), str(ROOT / "run_pipeline.py"), *_sync_args()]
        self._process = subprocess.Popen(
            cmd, cwd=ROOT, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        )

    def open_study_brief(self, _sender) -> None:
        self._open(OUT_DIR / "study-brief.md")

    def open_changes(self, _sender) -> None:
        self._open(OUT_DIR / "changes.md")

    def open_log(self, _sender) -> None:
        self._open(LOG_FILE)

    @staticmethod
    def _open(path: Path) -> None:
        if path.exists():
            subprocess.run(["open", str(path)])
        else:
            rumps.notification("InfoMentor Extractor", "File not found", str(path))


def main() -> int:
    InfoMentorMenuBarApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
