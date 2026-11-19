from datetime import date

import pytest

from infomentor_extract import extract_timetable


class FakeTimetablePage:
    """Fake Playwright page recording each gettimetablelist request's date window and
    returning one canned lesson per request so tests can assert both the chunking
    (how many requests, what windows) and the concatenated result."""

    def __init__(self):
        self.requested_windows: list[tuple[str, str]] = []

    async def evaluate(self, script, args=None):
        if args is None:
            return 0  # stand-in for `new Date().getTimezoneOffset()`, called separately first
        start = args["formBody"]["start"]
        end = args["formBody"]["end"]
        self.requested_windows.append((start, end))
        return [{"start": f"{start}T08:00:00", "title": f"lesson-{start}"}]


@pytest.mark.asyncio
async def test_extract_timetable_chunks_wide_range_into_weekly_requests():
    # Regression test for a live-verified InfoMentor server quirk: a single request
    # spanning more than ~a week silently drops the first two weekdays of every week
    # after the first (confirmed by re-requesting the same missing dates alone, which
    # came back complete) -- see extract_timetable()'s docstring. Chunking into <=7-day
    # requests must never depend on that undocumented limit.
    page = FakeTimetablePage()
    entries = await extract_timetable(page, date(2026, 9, 14), date(2026, 10, 9))  # 26 days
    assert page.requested_windows == [
        ("2026-09-14", "2026-09-20"),
        ("2026-09-21", "2026-09-27"),
        ("2026-09-28", "2026-10-04"),
        ("2026-10-05", "2026-10-09"),
    ]
    assert len(entries) == 4  # one canned entry per chunk request, concatenated


@pytest.mark.asyncio
async def test_extract_timetable_does_not_chunk_a_range_within_one_week():
    page = FakeTimetablePage()
    entries = await extract_timetable(page, date(2026, 9, 21), date(2026, 9, 27))  # exactly 7 days
    assert page.requested_windows == [("2026-09-21", "2026-09-27")]
    assert len(entries) == 1
