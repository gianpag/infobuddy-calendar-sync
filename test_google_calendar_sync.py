from datetime import date
from pathlib import Path
from unittest.mock import MagicMock, patch

from google.auth.exceptions import RefreshError

from google_calendar_sync import DEFAULT_SYNC_KINDS, load_google_credentials, sync_child, sync_timetable


def test_load_google_credentials_falls_back_to_interactive_flow_when_refresh_token_revoked():
    # A cached refresh token can be revoked/expired independently of the app (e.g.
    # Google's 7-day limit for an OAuth client still in "Testing" publishing status).
    # This must fall back to the interactive consent flow, not crash -- see CLAUDE.md
    # principle 3's "fall back to manual rather than fail" pattern.
    stale_creds = MagicMock(valid=False, expired=True, refresh_token="stale")
    stale_creds.refresh.side_effect = RefreshError("invalid_grant: Token has been expired or revoked.")

    fresh_creds = MagicMock()
    fresh_creds.to_json.return_value = '{"token": "fresh"}'

    with (
        patch("google_calendar_sync.keyring.get_password", return_value='{"token": "stale"}'),
        patch("google_calendar_sync.keyring.set_password") as mock_set_password,
        patch("google_calendar_sync.Credentials.from_authorized_user_info", return_value=stale_creds),
        patch("google_calendar_sync.InstalledAppFlow.from_client_secrets_file") as mock_flow,
    ):
        mock_flow.return_value.run_local_server.return_value = fresh_creds
        result = load_google_credentials(Path("client_secret.json"), "default", interactive=True)

    assert result is fresh_creds
    mock_set_password.assert_called_once_with("infomentor-extractor-google-calendar", "default", '{"token": "fresh"}')


class FakeRequest:
    def __init__(self, fn):
        self._fn = fn

    def execute(self):
        return self._fn()


class FakeCalendarsResource:
    def __init__(self, service):
        self.service = service

    def get(self, calendarId):
        return FakeRequest(lambda: self.service._get_calendar(calendarId))

    def insert(self, body):
        return FakeRequest(lambda: self.service._insert_calendar(body))


class FakeCalendarListResource:
    def __init__(self, service):
        self.service = service

    def list(self, pageToken=None):
        return FakeRequest(lambda: {
            "items": [{"id": cid, "summary": s} for s, cid in self.service.calendars_by_summary.items()],
        })


class FakeEventsResource:
    def __init__(self, service):
        self.service = service

    def insert(self, calendarId, body):
        return FakeRequest(lambda: self.service._insert_event(calendarId, body))

    def update(self, calendarId, eventId, body):
        return FakeRequest(lambda: self.service._update_event(calendarId, eventId, body))

    def delete(self, calendarId, eventId):
        return FakeRequest(lambda: self.service._delete_event(calendarId, eventId))


class FakeCalendarService:
    """In-memory double for the tiny slice of the Calendar API this module uses."""

    def __init__(self):
        self.calendars_by_summary: dict[str, str] = {}
        self.calendars_by_id: dict[str, dict] = {}
        self.event_store: dict[str, dict[str, dict]] = {}
        self._next_cal_id = 1
        self._next_event_id = 1
        self.calls: list[tuple] = []

    def calendars(self):
        return FakeCalendarsResource(self)

    def calendarList(self):
        return FakeCalendarListResource(self)

    def events(self):
        return FakeEventsResource(self)

    def _get_calendar(self, calendar_id):
        if calendar_id not in self.calendars_by_id:
            raise Exception("404 not found")
        return self.calendars_by_id[calendar_id]

    def _insert_calendar(self, body):
        cid = f"cal{self._next_cal_id}"
        self._next_cal_id += 1
        self.calendars_by_id[cid] = {"id": cid, **body}
        self.calendars_by_summary[body["summary"]] = cid
        self.event_store[cid] = {}
        self.calls.append(("insert_calendar", body))
        return self.calendars_by_id[cid]

    def _insert_event(self, calendar_id, body):
        eid = f"ev{self._next_event_id}"
        self._next_event_id += 1
        self.event_store.setdefault(calendar_id, {})[eid] = body
        self.calls.append(("insert_event", calendar_id, body))
        return {"id": eid, **body}

    def _update_event(self, calendar_id, event_id, body):
        self.event_store[calendar_id][event_id] = body
        self.calls.append(("update_event", calendar_id, event_id, body))
        return {"id": event_id, **body}

    def _delete_event(self, calendar_id, event_id):
        self.event_store.get(calendar_id, {}).pop(event_id, None)
        self.calls.append(("delete_event", calendar_id, event_id))


def make_child(items):
    return {
        "pupil": {"name": "Family, Alex", "switch_id": "1"},
        "normalized": {"study_items": items, "important_dates": {}, "planning": []},
    }


def make_child_with_schedule(lessons):
    return {
        "pupil": {"name": "Family, Alex", "switch_id": "1"},
        "normalized": {"study_items": [], "important_dates": {}, "planning": [], "schedule": lessons},
    }


def make_lesson(**overrides):
    lesson = {
        "start": "2026-09-14T08:10:00",
        "end": "2026-09-14T09:00:00",
        "subject": "Svenska",
        "subject_raw": "SV",
        "room": "Blå 1",
        "teachers": "Ada Lovelace",
        "notes": "",
        "all_day": False,
        "source": {"type": "timetable"},
    }
    lesson.update(overrides)
    return lesson


def make_item(**overrides):
    item = {
        "id": "1",
        "kind": "test",
        "priority": "high",
        "date": "2026-09-17",
        "title": "Prov: Ideologier och partier",
        "subjects": ["Samhällskunskap"],
        "description": "Läs del 1",
        "what_to_study": ["Del 1"],
        "preparation_instructions": [],
        "topics": [],
        "resources": [],
        "source": {"type": "calendar", "source_id": 1},
    }
    item.update(overrides)
    return item


def test_sync_creates_events_only_for_syncable_kinds():
    child = make_child([
        make_item(),
        make_item(id="2", kind="weekly_goal", priority="medium", title="Veckans mål NO"),
    ])
    service = FakeCalendarService()
    state = {}
    log = sync_child(service, child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)

    assert len([c for c in service.calls if c[0] == "insert_event"]) == 1
    assert any("Prov" in line for line in log)
    assert not any("Veckans mål" in line for line in log)


def test_sync_is_idempotent_on_unchanged_second_run():
    child = make_child([make_item()])
    service = FakeCalendarService()
    state = {}
    sync_child(service, child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)
    assert len([c for c in service.calls if c[0] == "insert_event"]) == 1

    service.calls.clear()
    sync_child(service, child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)
    assert service.calls == []


def test_sync_updates_existing_event_when_content_changes():
    child = make_child([make_item()])
    service = FakeCalendarService()
    state = {}
    sync_child(service, child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)

    changed_child = make_child([make_item(what_to_study=["Del 1", "Del 2"])])
    service.calls.clear()
    sync_child(service, changed_child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)

    assert [c[0] for c in service.calls] == ["update_event"]
    # Still exactly one event for this item -- not duplicated.
    assert sum(len(evs) for evs in service.event_store.values()) == 1


def test_sync_deletes_event_for_genuinely_removed_item():
    child = make_child([make_item()])
    service = FakeCalendarService()
    state = {}
    sync_child(service, child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)
    assert sum(len(evs) for evs in service.event_store.values()) == 1

    empty_child = make_child([])
    log = sync_child(service, empty_child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)

    assert any("Deleted event" in line for line in log)
    assert sum(len(evs) for evs in service.event_store.values()) == 0
    assert state["items"][list(state["items"].keys())[0]] == {}


def test_sync_fills_what_to_study_from_description_when_missing():
    # Regression: sync_child must apply the same fallback as the Study Brief, or a test
    # with no separately-derived guidance shows a completely empty event.
    item = make_item(what_to_study=[], description="Läs kapitel 3 och öva på glosorna.")
    child = make_child([item])
    service = FakeCalendarService()
    state = {}
    sync_child(service, child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)

    created_body = next(iter(next(iter(service.event_store.values())).values()))
    assert "Läs kapitel 3" in created_body["description"]


def test_sync_trims_untrimmed_planning_prep_and_includes_subject():
    # Regression: sync_child must apply the same concise_planning_prep trimming as the
    # Study Brief, or a planning-derived item dumps its entire historical prep list, and
    # must surface subjects, which build_event_description previously omitted entirely.
    long_prep = [f"Historical week {i} lesson note about something unrelated." for i in range(20)]
    long_prep.append("Repetera glosorna inför provet.")
    item = make_item(
        date="2026-09-23",
        derived_from_planning=True,
        subjects=["Moderna språk (språkval)"],
        preparation_instructions=long_prep,
        what_to_study=[],
        description="",
        source={"type": "planning", "source_id": "1"},
    )
    child = make_child([item])
    service = FakeCalendarService()
    state = {}
    sync_child(service, child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)

    created_body = next(iter(next(iter(service.event_store.values())).values()))
    description = created_body["description"]
    assert "Subject: Moderna språk (språkval)" in description
    assert "Repetera glosorna inför provet." in description
    assert "Historical week" not in description


def test_sync_respects_days_horizon():
    # An item due far in the future is left alone this run (not synced yet), and must not
    # be deleted just because it's outside the horizon on a later run either.
    far_future_item = make_item(id="3", date="2027-06-01")
    child = make_child([far_future_item])
    service = FakeCalendarService()
    state = {}
    log = sync_child(service, child, reference=date(2026, 9, 12), kinds=DEFAULT_SYNC_KINDS, sync_days=60, state=state, dry_run=False)

    assert service.calls == []
    assert "Deleted event" not in " ".join(log)


def test_sync_timetable_creates_one_event_per_lesson_in_its_own_calendar():
    child = make_child_with_schedule([
        make_lesson(start="2026-09-14T08:10:00", end="2026-09-14T09:00:00", subject="Svenska"),
        make_lesson(start="2026-09-14T09:10:00", end="2026-09-14T10:00:00", subject="Matematik"),
    ])
    service = FakeCalendarService()
    state = {}
    log = sync_timetable(service, child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")

    assert len([c for c in service.calls if c[0] == "insert_event"]) == 2
    assert service.calendars_by_summary == {"School – Alex – Timetable": "cal1"}
    assert any("Svenska" in line for line in log)
    assert any("Matematik" in line for line in log)


def test_sync_timetable_event_has_room_and_teacher_in_description():
    child = make_child_with_schedule([make_lesson(room="Blå 1", teachers="Ada Lovelace")])
    service = FakeCalendarService()
    state = {}
    sync_timetable(service, child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")

    created_body = next(iter(next(iter(service.event_store.values())).values()))
    assert "Room: Blå 1" in created_body["description"]
    assert "Teacher(s): Ada Lovelace" in created_body["description"]
    assert created_body["start"] == {"dateTime": "2026-09-14T08:10:00", "timeZone": "Europe/Stockholm"}


def test_sync_timetable_is_idempotent_on_unchanged_second_run():
    child = make_child_with_schedule([make_lesson()])
    service = FakeCalendarService()
    state = {}
    sync_timetable(service, child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")
    assert len([c for c in service.calls if c[0] == "insert_event"]) == 1

    service.calls.clear()
    sync_timetable(service, child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")
    assert service.calls == []


def test_sync_timetable_updates_when_room_changes():
    child = make_child_with_schedule([make_lesson(room="Blå 1")])
    service = FakeCalendarService()
    state = {}
    sync_timetable(service, child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")

    moved_child = make_child_with_schedule([make_lesson(room="Röd 3")])
    service.calls.clear()
    sync_timetable(service, moved_child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")

    assert [c[0] for c in service.calls] == ["update_event"]
    assert sum(len(evs) for evs in service.event_store.values()) == 1


def test_sync_timetable_does_not_delete_historical_lessons_that_aged_out_of_window():
    # A lesson in the past simply falling out of the rolling extraction window is not a
    # real schedule change; its calendar event (history) must be left alone.
    child = make_child_with_schedule([make_lesson(start="2026-09-01T08:10:00", end="2026-09-01T09:00:00")])
    service = FakeCalendarService()
    state = {}
    sync_timetable(service, child, reference=date(2026, 9, 1), state=state, dry_run=False, timezone="Europe/Stockholm")

    later_child = make_child_with_schedule([])  # lesson no longer in the (now later) extraction window
    log = sync_timetable(service, later_child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")

    assert "Deleted event" not in " ".join(log)
    assert sum(len(evs) for evs in service.event_store.values()) == 1


def test_sync_timetable_deletes_upcoming_lesson_that_genuinely_disappeared():
    # A still-upcoming lesson disappearing is a real schedule change (e.g. cancelled) and
    # must be cleaned up.
    child = make_child_with_schedule([make_lesson(start="2026-09-20T08:10:00", end="2026-09-20T09:00:00")])
    service = FakeCalendarService()
    state = {}
    sync_timetable(service, child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")

    changed_child = make_child_with_schedule([])
    log = sync_timetable(service, changed_child, reference=date(2026, 9, 12), state=state, dry_run=False, timezone="Europe/Stockholm")

    assert any("Deleted event" in line for line in log)
    assert sum(len(evs) for evs in service.event_store.values()) == 0
