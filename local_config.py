"""Small local, non-secret configuration file.

Stores just enough to make the common case a short command: which InfoMentor username to
default to for automated login. Never stores a password or token -- those live
exclusively in the OS keychain (see manage_credentials.py, google_calendar_sync.py, and
CLAUDE.md principle 3). This file can still contain a personal username/email, so it's
gitignored like every other local, deployment-specific file.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

CONFIG_PATH = Path(".infomentor-config.json")


def load_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {}
    try:
        return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}


def save_config(updates: dict[str, Any]) -> None:
    config = load_config()
    config.update(updates)
    CONFIG_PATH.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")


def resolve_username(cli_username: str | None) -> str | None:
    """CLI --username wins; otherwise fall back to the saved default, if any."""
    return cli_username or load_config().get("infomentor_username")


def default_client_secret_path(cli_path: Path | None) -> Path | None:
    """CLI --client-secret/--google-client-secret wins; otherwise use ./client_secret.json
    if it actually exists. Never guesses a path that isn't there."""
    if cli_path:
        return cli_path
    default = Path("client_secret.json")
    return default if default.exists() else None
