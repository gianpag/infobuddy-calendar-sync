"""Helpers for keeping children's identities out of log output (stdout/stderr/logs/).

Logs only ever name a child by first name -- never surname or full name. Data files
(JSON/Markdown outputs) are unaffected; this is purely for what gets printed.
"""
from __future__ import annotations


def log_first_name(name: str | None) -> str:
    """"Surname(s), Given Names" (InfoMentor's format) or "Given Surname" -> first given
    name only. Empty/unknown -> "child"."""
    raw = " ".join(str(name or "").split())
    if "," in raw:
        raw = raw.split(",", 1)[1].strip()
    return raw.split(" ", 1)[0] if raw else "child"
