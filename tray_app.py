#!/usr/bin/env python3
"""Windows system tray app: shows the last InfoMentor sync status and offers a manual
trigger. This is the Windows counterpart to menubar_app.py (macOS/rumps); the two share
all status logic via sync_status.py and differ only in which tray toolkit they use.

Reads .infomentor-state/sync_status.json (written by run_pipeline.py) to display status,
and launches run_pipeline.py as a subprocess for "Sync Now". This file never talks to
InfoMentor, Google, or the keychain directly -- all of that stays in run_pipeline.py.

Uses pystray + Pillow. Run it directly:
    python tray_app.py
or install it to start automatically at login:
    powershell -ExecutionPolicy Bypass -File scheduling\\windows\\install_tray.ps1
"""
from __future__ import annotations

import subprocess
import sys
import threading
import webbrowser
from pathlib import Path

import pystray
from PIL import Image, ImageDraw

import sync_status
from local_config import load_config

ROOT = Path(__file__).resolve().parent
STATE_DIR = ROOT / ".infomentor-state"
OUT_DIR = ROOT / "out"
LOG_FILE = ROOT / "logs" / "pipeline.out.log"

DEFAULT_SYNC_ARGS = ["--headless", "--sync-calendar", "--sync-timetable"]
POLL_SECONDS = 30

_COLORS: dict[str | None, tuple[int, int, int]] = {
    "success": (46, 160, 67),
    "failed": (200, 40, 40),
    "running": (66, 133, 244),
    None: (140, 140, 140),
}

# Avoids a flashing console window each time "Sync Now" launches run_pipeline.py.
_POPEN_KWARGS = {"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}


def _python_executable() -> str:
    venv_python = ROOT / ".venv" / "Scripts" / "python.exe"
    return str(venv_python) if venv_python.exists() else sys.executable


def _sync_args() -> list[str]:
    """Mirrors the scheduled task's flags by default; override by setting
    "manual_sync_args" in .infomentor-config.json."""
    configured = load_config().get("manual_sync_args")
    return list(configured) if configured else list(DEFAULT_SYNC_ARGS)


def _make_icon(color: tuple[int, int, int]) -> Image.Image:
    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    ImageDraw.Draw(image).ellipse((4, 4, size - 4, size - 4), fill=color)
    return image


class TrayApp:
    def __init__(self) -> None:
        self._process: subprocess.Popen | None = None
        self._lock = threading.Lock()
        self._stop_event = threading.Event()
        self.icon = pystray.Icon(
            "infomentor-extractor",
            icon=_make_icon(_COLORS[None]),
            title="InfoMentor Extractor",
            menu=self._build_menu(),
        )

    def _syncing(self) -> bool:
        return self._process is not None and self._process.poll() is None

    def _build_menu(self) -> pystray.Menu:
        status = sync_status.read_status(STATE_DIR)
        syncing = self._syncing()
        return pystray.Menu(
            pystray.MenuItem(sync_status.describe_status(status), None, enabled=False),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem(
                "Syncing…" if syncing else "Sync Now",
                self.sync_now,
                enabled=not syncing,
            ),
            pystray.MenuItem("Open Study Brief", self.open_study_brief),
            pystray.MenuItem("Open Changes", self.open_changes),
            pystray.MenuItem("View Log", self.open_log),
            pystray.Menu.SEPARATOR,
            pystray.MenuItem("Quit", self.quit),
        )

    def refresh(self) -> None:
        status = sync_status.read_status(STATE_DIR)
        state = "running" if self._syncing() else (status.get("status") if status else None)
        self.icon.icon = _make_icon(_COLORS.get(state, _COLORS[None]))
        self.icon.title = sync_status.describe_status(status)
        self.icon.menu = self._build_menu()

    def sync_now(self, _icon=None, _item=None) -> None:
        with self._lock:
            if self._syncing():
                return
            cmd = [_python_executable(), str(ROOT / "run_pipeline.py"), *_sync_args()]
            self._process = subprocess.Popen(
                cmd, cwd=ROOT,
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                **_POPEN_KWARGS,
            )
        self.refresh()

    def open_study_brief(self, _icon=None, _item=None) -> None:
        self._open(OUT_DIR / "study-brief.md")

    def open_changes(self, _icon=None, _item=None) -> None:
        self._open(OUT_DIR / "changes.md")

    def open_log(self, _icon=None, _item=None) -> None:
        self._open(LOG_FILE)

    @staticmethod
    def _open(path: Path) -> None:
        if path.exists():
            webbrowser.open(path.resolve().as_uri())

    def quit(self, _icon=None, _item=None) -> None:
        self._stop_event.set()
        self.icon.stop()

    def _poll_loop(self) -> None:
        while not self._stop_event.wait(POLL_SECONDS):
            self.refresh()

    def run(self) -> None:
        self.refresh()
        threading.Thread(target=self._poll_loop, daemon=True).start()
        self.icon.run()


def main() -> int:
    TrayApp().run()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
