#!/usr/bin/env python3
"""Extract and normalize family school data from InfoMentor Hub.

BankID is never automated. The browser profile is persistent, so the user can authenticate
interactively when the InfoMentor session has expired. API calls are then made from the page
context and reuse the browser's authenticated cookies.

The normalized layer is intentionally study-centric:
- schedule: regular lessons
- important_dates: tests/exams/quizzes/homework/events
- study_items: actionable things to prepare/do, with study guidance and resources
- resources: deduplicated links/files/references discovered anywhere in the extracted data
- planning: current learning units
- notifications: normalized InfoMentor notifications

Raw API responses are always preserved for traceability and future improvements.
"""

from __future__ import annotations

import argparse
import asyncio
import html as html_lib
import json
import re
import sys
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Iterable
from urllib.parse import urljoin, urlparse

from playwright.async_api import Page, async_playwright

from log_privacy import log_first_name
from sync_status import EXIT_MANUAL_LOGIN_REQUIRED

BASE_URL = "https://hub.infomentor.se"
AUTH_CHECK = "/authentication/authentication/isauthenticated/"
LOGIN_URL = f"{BASE_URL}/authentication/authentication/login?apitype=im1&forceOAuth=true"

# OS keychain service name for stored username/password credentials (see manage_credentials.py
# and CLAUDE.md principle 3). Never store the password anywhere else.
CREDENTIAL_SERVICE_NAME = "infomentor-extractor"
AUTOMATED_LOGIN_TIMEOUT_SECONDS = 45.0


@dataclass
class Pupil:
    name: str
    switch_url: str
    switch_id: str


class _HTMLTextExtractor(HTMLParser):
    """Small standard-library HTML-to-text/link extractor."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._anchor_href: str | None = None
        self._anchor_text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"p", "br", "li", "div", "tr", "h1", "h2", "h3", "h4"}:
            self.parts.append("\n")
        if tag == "a":
            attr = dict(attrs)
            self._anchor_href = attr.get("href")
            self._anchor_text = []

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._anchor_href:
            label = " ".join("".join(self._anchor_text).split())
            self.links.append((label, self._anchor_href))
            self._anchor_href = None
            self._anchor_text = []
        if tag in {"p", "li", "div", "tr"}:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        self.parts.append(data)
        if self._anchor_href is not None:
            self._anchor_text.append(data)

    @property
    def text(self) -> str:
        text = html_lib.unescape("".join(self.parts))
        text = re.sub(r"[ \t\r\f\v]+", " ", text)
        text = re.sub(r"\n\s*\n+", "\n", text)
        return text.strip()


def html_to_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if "<" not in text and ">" not in text:
        return html_lib.unescape(text).strip()
    parser = _HTMLTextExtractor()
    try:
        parser.feed(text)
        return parser.text
    except Exception:
        return re.sub(r"<[^>]+>", " ", html_lib.unescape(text)).strip()


def clean_text(value: Any) -> str:
    text = html_to_text(value)
    return re.sub(r"[ \t]+", " ", text).strip()


def split_nonempty_lines(value: Any) -> list[str]:
    text = html_to_text(value)
    return [re.sub(r"\s+", " ", line).strip(" -•\t") for line in text.splitlines() if line.strip()]


def absolute_url(url: str) -> str:
    return urljoin(BASE_URL + "/", url.strip())


def provider_for_url(url: str) -> str:
    host = urlparse(url).netloc.lower()
    path = urlparse(url).path.lower()
    if "glosboken" in host:
        return "Glosboken"
    if "classroom.google" in host or "classroom" in host:
        return "Google Classroom"
    if "magma" in host:
        return "Magma"
    if "infomentor" in host or path.startswith("/resources/"):
        return "InfoMentor"
    if "google" in host:
        return "Google"
    if "youtube" in host or "youtu.be" in host:
        return "YouTube"
    return host.removeprefix("www.") or "Web"


def resource_type_for_url(url: str, declared_type: str | None = None, file_type: str | None = None) -> str:
    if declared_type:
        dt = declared_type.lower()
        if "file" in dt or "media" in dt or "document" in dt:
            return "file"
        if "link" in dt:
            return "link"
    suffix = (file_type or Path(urlparse(url).path).suffix.lstrip(".")).lower()
    if suffix in {"pdf", "doc", "docx", "ppt", "pptx", "xls", "xlsx", "txt"}:
        return "file"
    return "link"


def extract_urls_from_text(value: Any) -> list[dict[str, str]]:
    if not value:
        return []
    source = str(value)
    parser = _HTMLTextExtractor()
    anchors: list[tuple[str, str]] = []
    if "<" in source and ">" in source:
        try:
            parser.feed(source)
            anchors = parser.links
        except Exception:
            anchors = []

    found: list[dict[str, str]] = []
    seen: set[str] = set()

    def add(url: str, title: str = "") -> None:
        raw = html_lib.unescape(url).strip().rstrip(".,;:)\"]")
        if not raw:
            return
        if raw.startswith("www."):
            raw = "https://" + raw
        if raw.startswith("/"):
            normalized = absolute_url(raw)
        elif re.match(r"^https?://", raw, re.I):
            normalized = raw
        else:
            return
        if normalized in seen:
            return
        seen.add(normalized)
        found.append(
            {
                "type": resource_type_for_url(normalized),
                "provider": provider_for_url(normalized),
                "title": clean_text(title) or normalized,
                "url": normalized,
            }
        )

    for label, href in anchors:
        add(href, label)

    # Also capture naked URLs that teachers paste directly into rich text. Use a short
    # preceding line as the resource label when possible (common in teacher notes).
    plain_text = html_to_text(source)
    lines = [line.strip() for line in plain_text.splitlines() if line.strip()]
    previous = ""
    for line in lines:
        matches = list(re.finditer(r"(?i)\b(?:https?://|www\.)[^\s<>\"']+", line))
        if matches:
            for match in matches:
                prefix = line[:match.start()].strip(" :-–—")
                label = prefix if prefix and len(prefix) <= 120 else (previous if len(previous) <= 120 else "")
                add(match.group(0), label)
        else:
            previous = line

    return found


def iter_dicts(value: Any) -> Iterable[dict[str, Any]]:
    if isinstance(value, dict):
        yield value
        for child in value.values():
            yield from iter_dicts(child)
    elif isinstance(value, list):
        for child in value:
            yield from iter_dicts(child)


def extract_resources(value: Any, *, source: dict[str, Any]) -> list[dict[str, Any]]:
    """Find resources in rich text and structured InfoMentor resource/link/file objects."""
    resources: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()

    def add(item: dict[str, Any]) -> None:
        url = item.get("url")
        title = clean_text(item.get("title") or item.get("name") or "")
        if not url:
            return
        url = absolute_url(str(url)) if str(url).startswith("/") else str(url)
        key = (url, title)
        if key in seen:
            return
        seen.add(key)
        item = dict(item)
        item["url"] = url
        item.setdefault("provider", provider_for_url(url))
        item.setdefault("type", resource_type_for_url(url, item.get("declared_type"), item.get("format")))
        item.setdefault("title", title or url)
        item["source"] = source
        resources.append(item)

    for node in iter_dicts(value):
        # Rich text fields commonly used by InfoMentor.
        for key in ("description", "text", "note", "content", "body"):
            if node.get(key):
                for r in extract_urls_from_text(node[key]):
                    add(r)

        url = node.get("url")
        if isinstance(url, str) and url and (url.startswith("/") or re.match(r"^https?://", url, re.I)):
            # InfoMentor calendar/task navigation URLs are provenance, not study resources.
            # Keep actual downloadable resources and external links.
            resolved = absolute_url(url) if url.startswith("/") else url
            parsed = urlparse(resolved)
            is_internal = parsed.netloc.lower().endswith("infomentor.se")
            is_resource_path = parsed.path.lower().startswith("/resources/")
            declared = str(node.get("type") or "").lower()
            looks_resource = any(x in declared for x in ("media", "file", "document", "link"))
            if (not is_internal) or is_resource_path or looks_resource:
                add(
                    {
                        "title": node.get("title") or node.get("name") or node.get("text") or "",
                        "url": url,
                        "declared_type": node.get("type"),
                        "format": node.get("fileType") or node.get("file_type"),
                    }
                )

    return resources


TEST_WORDS = (
    "prov",
    "exam",
    "examination",
    "test",
    "skrivning",
)
QUIZ_WORDS = (
    "läxförhör",
    "forhör",
    "förhör",
    "quiz",
    "exit ticket",
)
HOMEWORK_WORDS = (
    "läxa",
    "hemuppgift",
    "homework",
)
GOAL_WORDS = (
    "veckans mål",
    "veckomål",
    "mål för veckan",
    "veckans matte",
)
EVENT_WORDS = (
    "fotografering",
    "utflykt",
    "studiedag",
    "lov",
    "möte",
    "utvecklingssamtal",
    "idrottsdag",
    "skolavslutning",
    "temadag",
)
STUDY_VERBS = (
    "läsa",
    "repetera",
    "öva",
    "träna",
    "plugga",
    "kunna",
    "känna till",
    "förbereda",
    "arbeta med",
    "svara på",
    "titta igenom",
    "göra klart",
    "slutföra",
)


def text_blob(*values: Any) -> str:
    return " ".join(clean_text(v).lower() for v in values if v is not None)


def contains_any(text: str, words: Iterable[str]) -> bool:
    return any(word in text for word in words)


def classify_study_item(title: str, description: str = "", *, type_id: int | None = None) -> tuple[str, str]:
    """Return primary (kind, priority). Title/type signals outrank incidental text mentions."""
    title_blob = text_blob(title)
    full_blob = text_blob(title, description)

    if type_id == 17 or contains_any(title_blob, TEST_WORDS):
        return "test", "high"
    if contains_any(title_blob, QUIZ_WORDS):
        return "quiz", "high"
    if type_id == 11 or contains_any(title_blob, HOMEWORK_WORDS):
        return "homework", "high"
    if type_id == 16 or contains_any(title_blob, GOAL_WORDS):
        return "weekly_goal", "medium"
    if type_id == 15 or contains_any(title_blob, EVENT_WORDS):
        return "school_event", "normal"
    if type_id == 3:
        return "assignment", "medium"

    # Fallback only when the title/type gives us no better category.
    if contains_any(full_blob, TEST_WORDS):
        return "test", "high"
    if contains_any(full_blob, QUIZ_WORDS):
        return "quiz", "high"
    if contains_any(full_blob, HOMEWORK_WORDS):
        return "homework", "high"
    if contains_any(full_blob, EVENT_WORDS):
        return "school_event", "normal"
    return "unknown", "normal"


SWEDISH_MONTHS = {
    "januari": 1, "februari": 2, "mars": 3, "april": 4, "maj": 5, "juni": 6,
    "juli": 7, "augusti": 8, "september": 9, "oktober": 10, "november": 11, "december": 12,
}
SWEDISH_WEEKDAYS = {
    "måndag": 0, "måndagen": 0, "tisdag": 1, "tisdagen": 1, "onsdag": 2, "onsdagen": 2,
    "torsdag": 3, "torsdagen": 3, "fredag": 4, "fredagen": 4, "lördag": 5, "lördagen": 5,
    "söndag": 6, "söndagen": 6,
}
# Includes short forms ("mån", "tis", ...) used together with an explicit week number,
# e.g. "Prov onsdag v. 39". Kept separate from SWEDISH_WEEKDAYS above because the longer
# names there are matched without a week number and abbreviations would be too eager there.
SWEDISH_WEEKDAY_ABBREV = {
    "mån": 0, "måndag": 0, "måndagen": 0,
    "tis": 1, "tisdag": 1, "tisdagen": 1,
    "ons": 2, "onsdag": 2, "onsdagen": 2,
    "tor": 3, "tors": 3, "torsdag": 3, "torsdagen": 3,
    "fre": 4, "fredag": 4, "fredagen": 4,
    "lör": 5, "lördag": 5, "lördagen": 5,
    "sön": 6, "söndag": 6, "söndagen": 6,
}


def classify_planning_mention(text: str) -> str | None:
    """Classify a line of planning guidance text as an assessment mention, if any."""
    low = text.lower()
    if re.search(r"\b(prov|exam|test)\b", low):
        return "test"
    if re.search(r"\b(läxförhör|förhör|quiz|exit ticket)\b", low):
        return "quiz"
    if re.search(r"\b(läxa|hemuppgift)\b", low):
        return "homework"
    return None


def resolve_week_date(text: str, *, reference: date) -> date | None:
    """Resolve Swedish weekday + ISO week phrases, e.g. 'onsdag v. 39'."""
    low = text.lower()
    week_m = re.search(r"\b(?:v(?:ecka)?\.?\s*)(\d{1,2})\b", low)
    if not week_m:
        return None
    week = int(week_m.group(1))
    weekday: int | None = None
    for word, idx in SWEDISH_WEEKDAY_ABBREV.items():
        if re.search(rf"\b{re.escape(word)}\b", low):
            weekday = idx
            break
    if weekday is None:
        return None

    years = [reference.year - 1, reference.year, reference.year + 1]
    candidates: list[date] = []
    for year in years:
        try:
            candidates.append(date.fromisocalendar(year, week, weekday + 1))
        except ValueError:
            continue
    if not candidates:
        return None
    # Prefer a date around the extraction/reference date, with a modest preference for future.
    candidates.sort(key=lambda d: (0 if d >= reference - timedelta(days=14) else 1, abs((d - reference).days)))
    return candidates[0]


def _parse_iso_date(value: Any) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def extract_assessment_mentions(description: Any, context_date: Any) -> list[dict[str, Any]]:
    """Extract teacher-written test/quiz mentions and explicit dates from descriptive text."""
    context = _parse_iso_date(context_date)
    mentions: list[dict[str, Any]] = []
    seen: set[tuple[str, str | None, str]] = set()

    for segment in sentence_like_segments(description):
        low = segment.lower()
        if contains_any(low, TEST_WORDS):
            kind = "test"
        elif contains_any(low, QUIZ_WORDS):
            kind = "quiz"
        else:
            continue

        resolved: date | None = None
        explicit = re.search(r"(?:den\s+)?(\d{1,2})\s+(" + "|".join(SWEDISH_MONTHS) + r")", low)
        if explicit:
            day = int(explicit.group(1))
            month = SWEDISH_MONTHS[explicit.group(2)]
            year = context.year if context else date.today().year
            try:
                resolved = date(year, month, day)
                # If context is late in the year and parsed month is clearly next year.
                if context and resolved < context - timedelta(days=120):
                    resolved = date(year + 1, month, day)
            except ValueError:
                resolved = None
        elif context:
            # Resolve weekday mentions only when they match the item's own date; this avoids
            # guessing which week the teacher intended.
            for word, weekday in SWEDISH_WEEKDAYS.items():
                if re.search(rf"\b{re.escape(word)}\b", low) and context.weekday() == weekday:
                    resolved = context
                    break

        key = (kind, resolved.isoformat() if resolved else None, segment)
        if key in seen:
            continue
        seen.add(key)
        mentions.append({
            "kind": kind,
            "date": resolved.isoformat() if resolved else None,
            "text": segment,
            "derived": True,
        })
    return mentions


def sentence_like_segments(value: Any) -> list[str]:
    lines = split_nonempty_lines(value)
    segments: list[str] = []
    for line in lines:
        # Preserve teacher wording but split excessively long paragraph-like lines.
        pieces = re.split(r"(?<=[.!?])\s+(?=[A-ZÅÄÖ0-9])", line)
        segments.extend(p.strip() for p in pieces if p.strip())
    return segments


def extract_study_guidance(title: str, description: Any) -> dict[str, list[str]]:
    """Derive useful study guidance without replacing the teacher's original wording."""
    what_to_study: list[str] = []
    preparation: list[str] = []
    weekly_goals: list[str] = []
    topics: list[str] = []

    for segment in sentence_like_segments(description):
        low = segment.lower()
        if contains_any(low, GOAL_WORDS):
            weekly_goals.append(segment)
        if contains_any(low, STUDY_VERBS) or "sidor" in low or re.search(r"\bs\.\s*\d", low):
            preparation.append(segment)
        if any(token in low for token in ("du ska", "ni ska", "ska kunna", "du behöver", "inför prov", "inför förhör")):
            what_to_study.append(segment)

    title_low = title.lower()
    if contains_any(title_low, GOAL_WORDS) and title not in weekly_goals:
        weekly_goals.insert(0, title)

    # Pull concise topic lists from bullets/lines such as "Tristan och Isolde".
    for line in split_nonempty_lines(description):
        low = line.lower()
        if len(line) <= 120 and not re.search(r"https?://|www\.", low) and not contains_any(low, STUDY_VERBS):
            if line not in topics and (line.startswith(("•", "-")) or len(line.split()) <= 10):
                topics.append(line.lstrip("•- "))

    def dedupe(items: list[str]) -> list[str]:
        out: list[str] = []
        seen: set[str] = set()
        for item in items:
            item = re.sub(r"\s+", " ", item).strip()
            key = item.lower()
            if item and key not in seen:
                seen.add(key)
                out.append(item)
        return out

    return {
        "what_to_study": dedupe(what_to_study),
        "preparation_instructions": dedupe(preparation),
        "weekly_goals": dedupe(weekly_goals),
        "topics": dedupe(topics),
    }


async def fetch_json(
    page: Page,
    path: str,
    *,
    method: str = "POST",
    json_body: Any | None = None,
    form_body: dict[str, Any] | None = None,
) -> Any:
    return await page.evaluate(
        """async ({path, method, jsonBody, formBody}) => {
            const headers = {"Accept": "application/json, text/plain, */*"};
            const opts = { method, credentials: "include", headers };
            if (jsonBody !== null) {
                headers["Content-Type"] = "application/json; charset=UTF-8";
                opts.body = JSON.stringify(jsonBody);
            } else if (formBody !== null) {
                headers["Content-Type"] = "application/x-www-form-urlencoded; charset=UTF-8";
                opts.body = new URLSearchParams(formBody).toString();
            }
            const response = await fetch(path, opts);
            const text = await response.text();
            if (!response.ok) {
                throw new Error(`${method} ${path}: HTTP ${response.status}: ${text.slice(0, 500)}`);
            }
            try { return JSON.parse(text); }
            catch (_) { return text; }
        }""",
        {"path": path, "method": method, "jsonBody": json_body, "formBody": form_body},
    )


async def is_authenticated(page: Page) -> bool:
    if not page.url.startswith(BASE_URL):
        return False
    try:
        result = await fetch_json(page, AUTH_CHECK)
        return result is True or str(result).lower() == "true"
    except Exception:
        return False


async def _start_login_flow(page: Page) -> None:
    """Start InfoMentor's interactive login flow without fighting its redirects.

    InfoMentor's Hub login endpoint immediately hands navigation off to the legacy
    Mentor/SSO flow. Playwright can therefore report the initial ``goto`` as
    "interrupted by another navigation" even though the login flow is working
    exactly as intended. Starting the navigation with ``wait_until=commit`` and
    tolerating that specific redirect race lets the browser continue to BankID.
    """
    try:
        await page.goto(LOGIN_URL, wait_until="commit")
    except Exception as exc:
        message = str(exc).lower()
        redirect_race = (
            "interrupted by another navigation" in message
            or "navigation interrupted" in message
            or "net::err_aborted" in message
        )
        if not redirect_race:
            raise


class ManualLoginRequired(Exception):
    """InfoMentor needs a human (BankID/SSO, or no usable credential login) and this run
    can't wait for one. BankID is never automated (CLAUDE.md principle 3)."""


MANUAL_LOGIN_HINT = (
    "Complete the login manually once in a visible browser so the persistent profile gets "
    "a fresh session, e.g. on the host with a display (or `ssh -X`): "
    "`.venv/bin/python infomentor_extract.py --profile .infomentor-browser-profile "
    "--output out/infomentor-current.json` (without --headless), then rerun the pipeline."
)

LOGIN_FORM_SELECTOR = "#login_ascx_txtNotandanafn"


def _is_redirect_race(exc: Exception) -> bool:
    message = str(exc).lower()
    return (
        "interrupted by another navigation" in message
        or "navigation interrupted" in message
        or "net::err_aborted" in message
    )


async def _goto_settled(page: Page, url: str) -> None:
    """goto() that tolerates InfoMentor's redirect chain: if the navigation is
    interrupted by a redirect, wait for whatever page it settled on instead of failing."""
    try:
        await page.goto(url, wait_until="domcontentloaded")
    except Exception as exc:
        if not _is_redirect_race(exc):
            raise
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass  # state detection below decides what to do with wherever we ended up


async def _detect_login_state(page: Page) -> str:
    """'authenticated' | 'login_form' | 'bankid' | 'unknown' for the current page."""
    if await is_authenticated(page):
        return "authenticated"
    try:
        if await page.query_selector(LOGIN_FORM_SELECTOR):
            return "login_form"
    except Exception:
        pass
    url = (page.url or "").lower()
    if "bankid" in url or "/idp" in url or "saml" in url:
        return "bankid"
    try:
        content = (await page.content()).lower()
    except Exception:
        content = ""
    if "bankid" in content:
        return "bankid"
    return "unknown"


async def _login_with_credentials(page: Page, username: str, password: str, *, attempts: int = 2) -> None:
    """Submit InfoMentor's own username/password login form.

    This is a distinct login path from BankID/SSO (see CLAUDE.md principle 3): it fills
    and submits the same visible username/password fields a person would use on
    InfoMentor's login page, never touching BankID or any third-party IdP. Navigating to
    the login page can be interrupted by InfoMentor's own redirect (e.g. to the legacy
    Mentor site), so the page is allowed to settle and its state detected before acting;
    one retry. Raises ManualLoginRequired if BankID/SSO is required or no form appears.
    """
    state = "unknown"
    for _ in range(attempts):
        await _goto_settled(page, LOGIN_URL)
        state = await _detect_login_state(page)
        if state == "authenticated":
            return
        if state == "bankid":
            raise ManualLoginRequired("InfoMentor requires BankID/SSO login, which is never automated. " + MANUAL_LOGIN_HINT)
        if state == "login_form":
            break
    else:
        raise ManualLoginRequired(
            f"InfoMentor login form not found (ended at {(page.url or '').split('?')[0]}). " + MANUAL_LOGIN_HINT
        )

    await page.fill(LOGIN_FORM_SELECTOR, username)
    await page.fill("#login_ascx_txtLykilord", password)
    try:
        await page.click("#login_ascx_btnLogin")
    except Exception as exc:
        if not _is_redirect_race(exc):
            raise


async def _wait_for_authentication(page: Page, *, timeout_seconds: float | None) -> bool:
    """Poll until the Hub session is authenticated.

    Do not navigate the page again while a login flow (BankID/SSO or the credential
    form's postback) is still settling; the final callback returns to hub.infomentor.se,
    only then is the authenticated Hub API used to verify the session. With
    ``timeout_seconds=None`` this waits forever (used for the manual/BankID path, where a
    human is expected to be present); a numeric timeout returns False instead of hanging
    an unattended/scheduled run indefinitely.
    """
    loop = asyncio.get_running_loop()
    started = loop.time()
    last_url = None
    while True:
        if timeout_seconds is not None and loop.time() - started > timeout_seconds:
            return False
        await asyncio.sleep(2)
        current_url = page.url
        if current_url != last_url:
            last_url = current_url
            if not current_url.startswith(BASE_URL):
                print(f"Waiting for InfoMentor login... ({current_url.split('?')[0]})")

        if current_url.startswith(BASE_URL) and await is_authenticated(page):
            # The OAuth callback can still be settling; loading Hub once here is
            # safe because authentication has already been verified.
            await page.goto(BASE_URL, wait_until="domcontentloaded")
            return True


async def ensure_authenticated(
    page: Page, credentials: tuple[str, str] | None = None, *, headless: bool = False
) -> None:
    """With `headless=True` nobody can complete a manual/BankID login, so instead of
    waiting forever this raises ManualLoginRequired."""
    await _goto_settled(page, BASE_URL)
    if await is_authenticated(page):
        return

    if credentials:
        username, password = credentials
        # Never log the username/password themselves.
        print("InfoMentor session is not authenticated. Attempting automated login with stored credentials.")
        try:
            await _login_with_credentials(page, username, password)
        except ManualLoginRequired:
            if headless:
                raise
            print("Automated login is not possible here; falling back to manual login.\n")
        else:
            if await _wait_for_authentication(page, timeout_seconds=AUTOMATED_LOGIN_TIMEOUT_SECONDS):
                print("Authenticated successfully via stored credentials.\n")
                return
            if headless:
                raise ManualLoginRequired("Automated login did not complete in time. " + MANUAL_LOGIN_HINT)
            print("Automated login did not complete in time; falling back to manual login.\n")

    if headless:
        raise ManualLoginRequired("InfoMentor session is not authenticated and this is a headless run. " + MANUAL_LOGIN_HINT)

    print("InfoMentor session is not authenticated.")
    print("A browser window is open. Complete the normal login/BankID flow there.")
    print("BankID is not automated or captured by this script.\n")

    await _start_login_flow(page)
    await _wait_for_authentication(page, timeout_seconds=None)
    print("Authenticated successfully.\n")


def discover_pupils(html: str) -> list[Pupil]:
    pattern = re.compile(
        r"onPupilSwitch\('(?P<url>/Account/PupilSwitcher/SwitchPupil/(?P<id>\d+))'\).*?"
        r"<div class=['\"]pupiltrigger pupilText['\"]>\s*(?P<name>[^<\r\n]+)",
        re.IGNORECASE | re.DOTALL,
    )
    pupils: list[Pupil] = []
    seen: set[str] = set()
    for match in pattern.finditer(html):
        sid = match.group("id")
        if sid in seen:
            continue
        seen.add(sid)
        pupils.append(Pupil(match.group("name").strip(), match.group("url"), sid))
    return pupils


async def switch_pupil(page: Page, pupil: Pupil) -> None:
    await page.goto(f"{BASE_URL}{pupil.switch_url}", wait_until="domcontentloaded")
    if not page.url.startswith(BASE_URL):
        raise RuntimeError(f"Unexpected redirect while switching pupil: {page.url}")
    await page.goto(BASE_URL, wait_until="domcontentloaded")
    if not await is_authenticated(page):
        raise RuntimeError("Session expired while switching pupil")


async def extract_timetable(page: Page, start: date, end: date) -> list[dict[str, Any]]:
    """Fetches lessons for [start, end] in <=7-day chunks and concatenates them.

    InfoMentor's own /timetable/timetable/gettimetablelist endpoint has been observed
    (verified against the live API, not a guess -- a pupil's Monday/Tuesday lessons were
    confirmed present on InfoMentor's own web UI and via a single-week request, but
    silently absent from the response to a wider request covering the same dates) to
    drop the first two weekdays of every week after the first when a single request
    spans more than about a week. A 26-day request returned week 1 in full but only
    Wed-Fri of weeks 2-4; the same missing Mon/Tue came back complete when re-requested
    alone in a narrower window. Chunking into <=7-day requests avoids ever depending on
    that undocumented server-side limit, at the cost of one extra HTTP request per week
    of the extraction window (this project's default window is a few weeks -- a handful
    of requests, not a meaningful slowdown).
    """
    tz_offset = await page.evaluate("new Date().getTimezoneOffset()")
    entries: list[dict[str, Any]] = []
    chunk_start = start
    while chunk_start <= end:
        chunk_end = min(chunk_start + timedelta(days=6), end)
        chunk = await fetch_json(
            page,
            "/timetable/timetable/gettimetablelist",
            form_body={"UTCOffset": str(tz_offset), "start": chunk_start.isoformat(), "end": chunk_end.isoformat()},
        )
        entries.extend(chunk)
        chunk_start = chunk_end + timedelta(days=1)
    return entries


async def extract_calendar(page: Page, start: date, end: date) -> list[dict[str, Any]]:
    return await fetch_json(
        page,
        "/calendarv2/calendarv2/getentries",
        json_body={"startDate": start.strftime("%Y/%m/%d"), "endDate": end.strftime("%Y/%m/%d")},
    )


async def extract_task(page: Page, task_id: int | str) -> dict[str, Any]:
    return await fetch_json(page, "/Task/Task/GetTask", json_body={"taskId": str(task_id)})


async def extract_notifications(page: Page) -> dict[str, Any]:
    return await fetch_json(page, "/NotificationApp/NotificationApp/appData")


async def extract_planning(page: Page, include_details: bool = True) -> dict[str, Any]:
    app_data = await fetch_json(page, "/uolv2/uolv2/appData")
    result: dict[str, Any] = {"index": app_data}
    if not include_details:
        return result

    details: dict[str, Any] = {}
    for uol in app_data.get("uols", []):
        uid = uol.get("id")
        if uid is None:
            continue
        try:
            details[str(uid)] = await fetch_json(page, "/UolV2/UolV2/GetUol", json_body={"id": uid})
        except Exception as exc:
            details[str(uid)] = {"_error": str(exc)}
    result["details"] = details
    return result


# Only unambiguous timetable abbreviations named by ROADMAP.md are normalized here.
# "NO" is deliberately excluded (ROADMAP calls it out as context-dependent); do not
# guess ambiguous abbreviations.
SUBJECT_ABBREVIATIONS = {
    "ma": "Matematik",
    "sv": "Svenska",
    "en": "Engelska",
}


def normalize_subject_name(raw: Any) -> str:
    text = clean_text(raw)
    return SUBJECT_ABBREVIATIONS.get(text.strip().lower(), text)


def normalize_lesson(item: dict[str, Any]) -> dict[str, Any]:
    notes = item.get("notes") or {}
    raw_subject = item.get("title")
    return {
        "start": item.get("start"),
        "end": item.get("end"),
        "subject": normalize_subject_name(raw_subject),
        "subject_raw": clean_text(raw_subject),
        "room": notes.get("roomInfo") or item.get("details"),
        "teachers": notes.get("tutors"),
        "notes": clean_text(notes.get("timetableNotes")),
        "all_day": item.get("allDay", False),
        "source": {"type": "timetable"},
    }


def subject_names(item: dict[str, Any]) -> list[str]:
    subjects = item.get("subjects") or []
    names: list[str] = []
    for subject in subjects:
        if isinstance(subject, dict):
            name = subject.get("title") or subject.get("name")
            if name:
                names.append(normalize_subject_name(name))
        elif subject:
            names.append(normalize_subject_name(subject))
    return names


def calendar_source(item: dict[str, Any]) -> dict[str, Any]:
    return {
        "type": "calendar",
        "source_id": item.get("id"),
        "infomentor_type": item.get("calendarEntryTypeId"),
        "url": absolute_url(item["url"]) if item.get("url") else None,
    }


def normalize_calendar_item(item: dict[str, Any], task: dict[str, Any] | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
    task = task if isinstance(task, dict) and "_error" not in task else None
    title = clean_text((task or {}).get("title") or item.get("title"))
    description_raw = (task or {}).get("description") or item.get("description") or item.get("text") or ""
    description = clean_text(description_raw)
    type_id = item.get("calendarEntryTypeId")
    kind, priority = classify_study_item(title, description, type_id=type_id)
    subjects = []
    if task and task.get("subject"):
        subjects = [normalize_subject_name(s.strip()) for s in re.split(r",| och \d+ andra ämnen", str(task.get("subject"))) if s.strip()]
    if not subjects:
        subjects = subject_names(item)

    source = calendar_source(item)
    resources = extract_resources({"calendar": item, "task": task or {}}, source=source)
    guidance = extract_study_guidance(title, description_raw)
    date_value = (task or {}).get("dueDate") or item.get("startDate") or item.get("startDateFull")
    assessment_mentions = extract_assessment_mentions(description_raw, date_value)

    base = {
        "id": str(item.get("id")) if item.get("id") is not None else None,
        "kind": kind,
        "priority": priority,
        "subjects": subjects,
        "title": title,
        "date": date_value,
        "end": item.get("endDate") or item.get("endDateFull"),
        "all_day": item.get("isAllDayEvent"),
        "description": description,
        "status": (task or {}).get("status"),
        "is_overdue": (task or {}).get("isOverdue"),
        "read": (task or {}).get("read"),
        "has_unread_comments": (task or {}).get("hasUnreadComments"),
        "what_to_study": guidance["what_to_study"],
        "preparation_instructions": guidance["preparation_instructions"],
        "weekly_goals": guidance["weekly_goals"],
        "topics": guidance["topics"],
        "assessment_mentions": assessment_mentions,
        "resources": resources,
        "source": source,
    }

    important = {
        "id": base["id"],
        "kind": kind,
        "priority": priority,
        "date": date_value,
        "title": title,
        "subjects": subjects,
        "source": source,
    }
    return base, important


def normalize_planning_task(
    task: dict[str, Any], *, planning_id: str, planning_subjects: list[str]
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Normalize a task embedded inside a planning unit's own 'task' section.

    Planning frequently contains fully-specified future assessments (with their own
    dueDate) before they ever appear as a dedicated calendar entry. These are treated
    like calendar/task items so they get the same classification, guidance and resource
    extraction, but are marked as planning-derived for provenance.
    """
    title = clean_text(task.get("title"))
    description_raw = task.get("description") or ""
    description = clean_text(description_raw)
    kind, priority = classify_study_item(title, description)

    task_subject = clean_text(task.get("subject") or "")
    if task_subject and task_subject != "Ämne saknas":
        subjects = [normalize_subject_name(s.strip()) for s in re.split(r",| och \d+ andra ämnen", task_subject) if s.strip()]
    else:
        subjects = list(planning_subjects)

    task_id = str(task.get("id")) if task.get("id") is not None else None
    source = {"type": "planning_task", "source_id": task_id, "planning_id": planning_id}
    resources = extract_resources(task, source=source)
    guidance = extract_study_guidance(title, description_raw)
    date_value = task.get("dueDate")
    assessment_mentions = extract_assessment_mentions(description_raw, date_value)

    base = {
        "id": task_id,
        "kind": kind,
        "priority": priority,
        "subjects": subjects,
        "title": title,
        "date": date_value,
        "end": None,
        "all_day": None,
        "description": description,
        "status": task.get("status"),
        "is_overdue": task.get("isOverdue"),
        "read": task.get("read"),
        "has_unread_comments": task.get("hasUnreadComments"),
        "what_to_study": guidance["what_to_study"],
        "preparation_instructions": guidance["preparation_instructions"],
        "weekly_goals": guidance["weekly_goals"],
        "topics": guidance["topics"],
        "assessment_mentions": assessment_mentions,
        "resources": resources,
        "source": source,
        "derived_from_planning": True,
    }
    important = {
        "id": base["id"],
        "kind": kind,
        "priority": priority,
        "date": date_value,
        "title": title,
        "subjects": subjects,
        "source": source,
        "derived_from_planning": True,
    }
    return base, important


def normalize_planning(
    planning: dict[str, Any],
    *,
    reference: date | None = None,
    known_task_ids: set[str] | None = None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    """Normalize planning units.

    Returns (planning_units, resources, embedded_study_items, embedded_important_dates).
    The latter two come from fully-specified tasks embedded in planning 'task' sections
    that are not already covered by the calendar/task extraction.
    """
    reference = reference or date.today()
    known_task_ids = known_task_ids or set()
    index = planning.get("index") or {}
    details = planning.get("details") or {}
    index_by_id = {str(x.get("id")): x for x in index.get("uols", []) if x.get("id") is not None}
    subject_lookup = {str(x.get("id")): clean_text(x.get("name") or x.get("title")) for x in index.get("subjects", []) if x.get("id") is not None}
    normalized: list[dict[str, Any]] = []
    all_resources: list[dict[str, Any]] = []
    embedded_items: list[dict[str, Any]] = []
    embedded_important: list[dict[str, Any]] = []
    seen_task_ids: set[str] = set()

    ids = list(dict.fromkeys([*index_by_id.keys(), *details.keys()]))
    for uid in ids:
        idx = index_by_id.get(uid, {})
        detail = details.get(uid, {}) if isinstance(details.get(uid, {}), dict) else {}
        if detail.get("_error"):
            detail = {}
        title = clean_text(detail.get("title") or idx.get("title") or "")
        source = {"type": "planning", "source_id": uid}
        resources = extract_resources({"index": idx, "detail": detail}, source=source)
        all_resources.extend(resources)

        # Preserve teacher feed / weekly plan text as study guidance.
        text_parts: list[str] = []
        for node in iter_dicts(detail):
            for key in ("description", "text", "content"):
                if node.get(key):
                    text_parts.append(str(node[key]))
        guidance_source = "\n".join(text_parts)
        guidance = extract_study_guidance(title, guidance_source)

        raw_subjects = idx.get("subjectNames") or idx.get("subjects") or detail.get("subjects") or []
        subjects = []
        for subject in raw_subjects:
            if isinstance(subject, dict):
                subjects.append(normalize_subject_name(subject.get("name") or subject.get("title") or subject.get("id")))
            else:
                subjects.append(normalize_subject_name(subject_lookup.get(str(subject), clean_text(subject))))
        subjects = [x for x in subjects if x]

        # Planning 'task' sections may contain fully-specified future assessments (their
        # own id/title/dueDate) that never appear in the calendar extraction at all.
        uid_embedded: list[dict[str, Any]] = []
        for section in detail.get("sections") or []:
            if section.get("type") != "task":
                continue
            for task in section.get("tasks") or []:
                task_id = str(task.get("id")) if task.get("id") is not None else None
                if not task_id or task_id in known_task_ids or task_id in seen_task_ids:
                    continue
                seen_task_ids.add(task_id)
                task_base, task_important = normalize_planning_task(
                    task, planning_id=uid, planning_subjects=subjects
                )
                uid_embedded.append(task_base)
                embedded_items.append(task_base)
                embedded_important.append(task_important)
                all_resources.extend(task_base["resources"])

        # Unit-wide guidance mentions (e.g. "Prov onsdag v.39" inside prose), skipping any
        # kind/date already covered by a specific embedded task above.
        covered = {(x["kind"], x["date"]) for x in uid_embedded if x.get("date")}
        mention_lines = list(dict.fromkeys(
            guidance["topics"] + guidance["preparation_instructions"] + guidance["what_to_study"] + guidance["weekly_goals"]
        ))
        assessment_mentions: list[dict[str, Any]] = []
        seen_mentions: set[tuple[str, str]] = set()
        for line in mention_lines:
            mention_kind = classify_planning_mention(line)
            if not mention_kind:
                continue
            resolved = resolve_week_date(line, reference=reference)
            if resolved is None:
                continue
            key = (mention_kind, resolved.isoformat())
            if key in seen_mentions or key in covered:
                continue
            seen_mentions.add(key)
            assessment_mentions.append({"kind": mention_kind, "date": resolved.isoformat(), "text": line, "derived": True})

        normalized.append(
            {
                "id": uid,
                "title": title,
                "start": idx.get("startDate") or idx.get("start") or detail.get("startDate"),
                "end": idx.get("endDate") or idx.get("end") or detail.get("endDate"),
                "subjects": subjects,
                "has_assessments": detail.get("hasAssessments", idx.get("hasAssessments")),
                "what_to_study": guidance["what_to_study"],
                "preparation_instructions": guidance["preparation_instructions"],
                "weekly_goals": guidance["weekly_goals"],
                "topics": guidance["topics"],
                "assessment_mentions": assessment_mentions,
                "resources": resources,
                "source": source,
            }
        )

    return normalized, all_resources, embedded_items, embedded_important


def normalize_notifications(raw: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for item in (raw or {}).get("notifications", []):
        result.append(
            {
                "id": item.get("id"),
                "title": clean_text(item.get("title")),
                "subtitle": clean_text(item.get("subTitle")),
                "subjects": clean_text(item.get("subjectsCourses")),
                "date": item.get("dateSent") or item.get("orderDate"),
                "state": item.get("state"),
                "type": item.get("type") or item.get("entityTypeString") or item.get("appType"),
                "url": absolute_url(item["url"]) if isinstance(item.get("url"), str) and item["url"].startswith("/") else item.get("url"),
                "source": {"type": "notification", "source_id": item.get("id")},
            }
        )
    return result


def dedupe_resources(resources: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    """Dedupe resources, but keep distinct copies attributed to different sources.

    The same URL can legitimately be attached to more than one source (e.g. a calendar
    item and a planning unit both linking the same Glosboken invite); keying only on the
    URL would silently drop one of them and break source-scoped lookups downstream.
    """
    result: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str]] = set()
    for resource in resources:
        url = resource.get("url")
        if not url:
            continue
        src = resource.get("source") or {}
        key = (str(url), str(src.get("type") or ""), str(src.get("source_id") or ""))
        if key in seen:
            continue
        seen.add(key)
        result.append(resource)
    return result


def promote_assessment_mentions(study: dict[str, Any], important_dates: dict[str, list[dict[str, Any]]]) -> None:
    """Promote safely-resolved embedded assessment mentions found inside an item's own text.

    A weekly goal/homework/planning task can contain a separately stated future test date;
    only mentions whose date we can resolve safely are promoted. A mention that just
    resolves to the item's own kind/date (e.g. a test description restating its own due
    date in prose) is not new information and must not be promoted as a second entry.
    """
    own_kind, own_date = study.get("kind"), study.get("date")
    for mention in study.get("assessment_mentions", []):
        if not mention.get("date"):
            continue
        if mention.get("kind") == own_kind and mention.get("date") == own_date:
            continue
        mention_bucket = "tests" if mention["kind"] == "test" else "quizzes"
        important_dates[mention_bucket].append({
            "id": f"{study['id']}:{mention['kind']}:{mention['date']}",
            "kind": mention["kind"],
            "priority": "high",
            "date": mention["date"],
            "title": mention["text"],
            "subjects": study.get("subjects", []),
            "derived_from_description": True,
            "source": study.get("source"),
        })


IMPORTANT_DATE_BUCKETS = {
    "test": "tests",
    "quiz": "quizzes",
    "homework": "homework",
    "assignment": "assignments",
    "weekly_goal": "assignments",
    "school_event": "school_events",
}

# Buckets eligible for cross-source assessment linking. "school_events" and "other" are
# excluded: they are not assessments and same-day/same-subject school events are common
# without being the same event.
ASSESSMENT_LINK_BUCKETS = ("tests", "quizzes", "homework", "assignments")


def _title_word_set(title: Any) -> set[str]:
    text = re.sub(r"[^a-z0-9åäöéèü]+", " ", str(title or "").lower())
    return {word for word in text.split() if word}


def _titles_overlap(a: Any, b: Any) -> bool:
    words_a, words_b = _title_word_set(a), _title_word_set(b)
    if not words_a or not words_b:
        return False
    return len(words_a & words_b) / min(len(words_a), len(words_b)) >= 0.5


def link_assessment_groups(
    study_items: list[dict[str, Any]], important_dates: dict[str, list[dict[str, Any]]]
) -> list[dict[str, Any]]:
    """Link records that likely describe the same real-world assessment, without merging them.

    InfoMentor can represent one assessment as a calendar entry, a task, and/or a planning
    mention, each with its own id (see DATA_MODEL.md "Deduplication philosophy"). Per
    CLAUDE.md principle 8, do not delete or fuzzy-merge apparent duplicates. Instead, assign
    a shared `canonical_id` and return a summary group so consumers can present them
    together while every source record stays intact and traceable.

    Grouping requires *both* an exact (kind, date, subject-set) match *and* clearly
    overlapping titles (>=50% word overlap) -- never a single weak signal alone, since e.g.
    two distinct homework items are routinely due the same day in the same subject.

    Only `important_dates` entries are clustered: a study_item and its own
    important_dates entry share one id and are two views of the same source record, not
    independent evidence of a duplicate. The resulting canonical_id is then copied onto
    the matching study_item by id.
    """
    entries: list[tuple[str, dict[str, Any]]] = []
    for bucket in ASSESSMENT_LINK_BUCKETS:
        entries.extend((f"important_dates.{bucket}", item) for item in important_dates.get(bucket, []))

    clusters: list[dict[str, Any]] = []
    for collection, item in entries:
        if not item.get("date") or item.get("id") is None:
            continue
        subj_key = tuple(sorted({s.strip().lower() for s in item.get("subjects", []) if s and s.strip()}))
        signature = (item.get("kind"), item.get("date"), subj_key)
        placed = False
        for cluster in clusters:
            if cluster["signature"] != signature:
                continue
            if _titles_overlap(item.get("title"), cluster["members"][0][1].get("title")):
                cluster["members"].append((collection, item))
                placed = True
                break
        if not placed:
            clusters.append({"signature": signature, "members": [(collection, item)]})

    groups: list[dict[str, Any]] = []
    for cluster in clusters:
        kind, date_value, subj_key = cluster["signature"]
        members = cluster["members"]
        slug = "-".join(subj_key) or re.sub(r"[^a-z0-9]+", "-", str(members[0][1].get("title") or "").lower()).strip("-") or "item"
        # (kind, date, subjects) alone is not unique across clusters that share a signature
        # but differ by title (e.g. two distinct same-subject/same-day homework items); the
        # smallest member id disambiguates them while staying stable across repeat runs.
        member_ids = sorted({str(item.get("id")) for _, item in members if item.get("id") is not None})
        canonical_id = f"canon:{kind}:{date_value}:{slug}:{member_ids[0]}"
        for _, item in members:
            item["canonical_id"] = canonical_id
        if len(members) < 2:
            continue
        subjects = sorted({s for _, item in members for s in item.get("subjects", [])})
        titles: list[str] = []
        for _, item in members:
            if item.get("title") and item["title"] not in titles:
                titles.append(item["title"])
        groups.append({
            "canonical_id": canonical_id,
            "kind": kind,
            "date": date_value,
            "subjects": subjects,
            "titles": titles,
            "sources": [
                {"collection": collection, "id": item.get("id"), "source": item.get("source")}
                for collection, item in members
            ],
        })

    canonical_by_id = {
        item["id"]: item["canonical_id"]
        for cluster in clusters
        for _, item in cluster["members"]
        if item.get("id") is not None
    }
    for study in study_items:
        canonical_id = canonical_by_id.get(study.get("id"))
        if canonical_id:
            study["canonical_id"] = canonical_id

    return groups


def build_normalized(
    timetable: list[dict[str, Any]],
    calendar: list[dict[str, Any]],
    tasks: dict[str, Any],
    notifications: dict[str, Any],
    planning: dict[str, Any],
    *,
    reference: date | None = None,
) -> dict[str, Any]:
    reference = reference or date.today()
    schedule = [normalize_lesson(x) for x in timetable]
    study_items: list[dict[str, Any]] = []
    important_dates = {"tests": [], "quizzes": [], "homework": [], "assignments": [], "school_events": [], "other": []}
    resource_pool: list[dict[str, Any]] = []

    for entry in calendar:
        task = tasks.get(str(entry.get("id"))) if entry.get("id") is not None else None
        study, important = normalize_calendar_item(entry, task)
        resource_pool.extend(study.get("resources", []))

        # Unknown calendar entries stay available under important_dates.other but are not
        # promoted to study_items unless they have meaningful descriptive content.
        kind = study["kind"]
        if kind != "unknown" or study.get("description") or study.get("resources"):
            study_items.append(study)

        important_dates[IMPORTANT_DATE_BUCKETS.get(kind, "other")].append(important)
        promote_assessment_mentions(study, important_dates)

    # Planning may contain fully-specified future assessments before they ever appear as
    # calendar entries; skip any task id already covered by the calendar/task extraction.
    known_task_ids = {str(entry.get("id")) for entry in calendar if entry.get("id") is not None}
    planning_norm, planning_resources, embedded_items, embedded_important = normalize_planning(
        planning, reference=reference, known_task_ids=known_task_ids
    )
    resource_pool.extend(planning_resources)

    for study in embedded_items:
        resource_pool.extend(study.get("resources", []))
        kind = study["kind"]
        if kind != "unknown" or study.get("description") or study.get("resources"):
            study_items.append(study)
        promote_assessment_mentions(study, important_dates)

    for important in embedded_important:
        important_dates[IMPORTANT_DATE_BUCKETS.get(important["kind"], "other")].append(important)

    # Unit-wide guidance mentions (e.g. "Prov onsdag v.39" inside prose) that aren't tied
    # to a specific embedded task above.
    for plan in planning_norm:
        for mention in plan.get("assessment_mentions", []):
            if not mention.get("date"):
                continue
            mention_bucket = "tests" if mention["kind"] == "test" else ("quizzes" if mention["kind"] == "quiz" else "homework")
            important_dates[mention_bucket].append({
                "id": f"planning:{plan['id']}:{mention['kind']}:{mention['date']}",
                "kind": mention["kind"],
                "priority": "high",
                "date": mention["date"],
                "title": mention["text"],
                "subjects": plan.get("subjects", []),
                "resources": plan.get("resources") or [],
                "derived_from_planning": True,
                "source": plan.get("source"),
            })

    # Also inspect raw tasks because some embedded links/files may not be represented in calendar.
    for task_id, task in tasks.items():
        if isinstance(task, dict) and "_error" not in task:
            resource_pool.extend(extract_resources(task, source={"type": "task", "source_id": task_id}))

    assessment_groups = link_assessment_groups(study_items, important_dates)

    return {
        "schedule": schedule,
        "important_dates": important_dates,
        "study_items": study_items,
        "assessment_groups": assessment_groups,
        "resources": dedupe_resources(resource_pool),
        "planning": planning_norm,
        "notifications": normalize_notifications(notifications),
    }


async def extract_for_pupil(
    page: Page,
    pupil: Pupil,
    start: date,
    end: date,
    include_planning_details: bool,
) -> dict[str, Any]:
    print(f"Extracting: {log_first_name(pupil.name)}")
    await switch_pupil(page, pupil)

    timetable = await extract_timetable(page, start, end)
    calendar = await extract_calendar(page, start, end)

    tasks: dict[str, Any] = {}
    for entry in calendar:
        if entry.get("calendarEntryTypeId") == 3 and entry.get("id") is not None:
            task_id = str(entry["id"])
            try:
                tasks[task_id] = await extract_task(page, task_id)
            except Exception as exc:
                tasks[task_id] = {"_error": str(exc)}

    notifications = await extract_notifications(page)
    planning = await extract_planning(page, include_details=include_planning_details)
    normalized = build_normalized(timetable, calendar, tasks, notifications, planning)

    return {
        "pupil": asdict(pupil),
        "range": {"start": start.isoformat(), "end": end.isoformat()},
        "normalized": normalized,
        "raw": {
            "timetable": timetable,
            "calendar": calendar,
            "tasks": tasks,
            "notifications": notifications,
            "planning": planning,
        },
    }


def parse_date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("Expected date as YYYY-MM-DD") from exc


def default_range() -> tuple[date, date]:
    """Current week through 4 weeks out.

    Only calendar/timetable entries are bounded by this window (planning data is fetched
    regardless of date); a wider window gives more advance notice of already-scheduled
    calendar tests/homework/events, matching the Study Brief/Calendar sync's own horizons.
    """
    today = date.today()
    monday = today - timedelta(days=today.weekday())
    return monday, monday + timedelta(days=27)


def load_credentials(username: str | None) -> tuple[str, str] | None:
    """Look up a stored password for automated (non-BankID) login.

    The password is only ever read from the OS keychain here, never from a CLI argument,
    file, or environment variable (see CLAUDE.md principle 3). Use manage_credentials.py
    to store it once.
    """
    if not username:
        return None
    import keyring

    from keyring_linux_bootstrap import bootstrap_linux_keyring

    bootstrap_linux_keyring()
    password = keyring.get_password(CREDENTIAL_SERVICE_NAME, username)
    if not password:
        raise SystemExit(
            f"No stored password found for {username!r} in the OS keychain. Run:\n"
            f"  python manage_credentials.py set --username {username}"
        )
    return username, password


async def async_main(args: argparse.Namespace) -> None:
    profile_dir = Path(args.profile).expanduser().resolve()
    output = Path(args.output).expanduser().resolve()
    profile_dir.mkdir(parents=True, exist_ok=True)
    output.parent.mkdir(parents=True, exist_ok=True)
    from local_config import resolve_username

    credentials = load_credentials(resolve_username(args.username))

    start_default, end_default = default_range()
    start = args.start or start_default
    end = args.end or end_default
    if end < start:
        raise SystemExit("--end must be on or after --start")

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=args.headless,
            viewport={"width": 1280, "height": 900},
        )
        page = context.pages[0] if context.pages else await context.new_page()

        try:
            await ensure_authenticated(page, credentials=credentials, headless=args.headless)
            html = await page.content()
            pupils = discover_pupils(html)
            if not pupils:
                raise RuntimeError("Could not discover pupils from the Hub homepage. InfoMentor may have changed its HTML structure.")

            print("Discovered pupils:")
            for pupil in pupils:
                print(f"  - {log_first_name(pupil.name)} ({pupil.switch_id})")
            print()

            children = []
            for pupil in pupils:
                children.append(
                    await extract_for_pupil(page, pupil, start, end, include_planning_details=not args.no_planning_details)
                )

            payload = {
                "schema_version": 3,
                "source": "InfoMentor Hub",
                "base_url": BASE_URL,
                "extracted_at": datetime.now().astimezone().isoformat(),
                "children": children,
            }
            output.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"\nWrote {output}")
        finally:
            await context.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Extract InfoMentor Hub data to study-centric JSON")
    parser.add_argument("--start", type=parse_date, help="Start date YYYY-MM-DD")
    parser.add_argument("--end", type=parse_date, help="End date YYYY-MM-DD")
    parser.add_argument("--profile", default=".infomentor-browser-profile", help="Persistent Chromium profile directory")
    parser.add_argument("--output", default="infomentor.json", help="Output JSON path")
    parser.add_argument("--headless", action="store_true", help="Run headless; requires an already valid session")
    parser.add_argument("--no-planning-details", action="store_true", help="Skip per-planning detail calls")
    parser.add_argument(
        "--username",
        help=(
            "InfoMentor username for automated (non-BankID) login. Defaults to whatever "
            "manage_credentials.py last stored, if any. Password is read from the OS "
            "keychain and never accepted as a CLI argument. Falls back to manual/BankID "
            "login if automated login doesn't complete in time, or if no username is known."
        ),
    )
    return parser


def main() -> None:
    try:
        asyncio.run(async_main(build_parser().parse_args()))
    except ManualLoginRequired as exc:
        print(f"ERROR: manual InfoMentor login required: {exc}", file=sys.stderr)
        raise SystemExit(EXIT_MANUAL_LOGIN_REQUIRED)


if __name__ == "__main__":
    main()
