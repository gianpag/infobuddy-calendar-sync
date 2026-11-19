# Roadmap

## Current state

Implemented foundation:

- Playwright-based InfoMentor extraction
- persistent browser profile
- manual BankID authentication
- optional automated username/password login for accounts without BankID (OS keychain only, see CLAUDE.md principle 3)
- handling of InfoMentor/SSO redirect chains
- dynamic pupil discovery/switching
- timetable extraction
- calendar extraction
- task enrichment
- notification extraction
- planning extraction
- normalized schedule/study/resource model
- assessment classification
- assessment discovery from teacher/planning text
- Swedish explicit-date and ISO-week assessment parsing
- resource extraction from embedded HTML/text and structured resources
- Study Brief generation
- stateful change detection
- single pipeline command
- Google Calendar sync (idempotent, per-child, self-hosted OAuth), incl. optional timetable sync
- canonical assessment linking (Phase 1, item 1)
- generic multi-account Google Calendar *read* sync (`google_calendar_read_sync.py`,
  device-flow OAuth, `calendar_link_ui.py` web UI on port 8200) → `out/generic-calendar.json`
  — unrelated to InfoMentor; consumed by the InkBuddy e-paper dashboard
- scheduled/unattended runs on macOS (launchd), Windows (Task Scheduler) and Linux
  (systemd `--user` timers: pipeline at 06:30 and 18:30, generic calendar sync every
  15 min) — see `scheduling/` and README "Running automatically"
- sync status file (`sync_status.json`) + optional macOS menu bar / Windows tray app
  with "Sync Now"
- normalized output carries `schema_version: 3`; Study Brief carries `study_brief_version: 1`

The project is now beyond proof-of-concept.

---

## Phase 1 — Harden study intelligence

### 1. Assessment linking / canonical grouping

**Status: implemented.** `link_assessment_groups()` in `infomentor_extract.py` assigns a
`canonical_id` to `important_dates`/`study_items` entries (tests/quizzes/homework/
assignments) that match on kind + date + subject-set + overlapping title, and
`normalized.assessment_groups` lists the resulting multi-source groups. See
DATA_MODEL.md "Canonical assessment linking" for the schema and matching rules, and
`test_normalization.py`'s `test_assessment_groups_*` tests for the guarded behavior (in
particular, that same-day/same-subject records are *not* linked unless titles also
overlap). No source record is merged or deleted.

Goal:

Recognize when several InfoMentor records describe the same assessment without deleting source data.

Possible signals:
- normalized subject
- date
- similar title
- shared task/source ID
- planning text references
- teacher/context

Output should support:

```text
canonical assessment
├── calendar source
├── task source
└── planning source
```

Do not implement fuzzy destructive merging.

### 2. Better assessment-resource association

Currently resources can be extracted globally or from planning units.

Improve linking so a test can directly expose:

```text
test
├── what_to_study
├── preparation instructions
└── relevant resources
```

Association should use strong contextual evidence first:
- same source/planning unit
- explicit assessment reference
- subject
- temporal proximity
- matching terminology

### 3. Study Brief refinement

Improve signal-to-noise.

Desired Study Brief ordering:

1. imminent tests/exams/quizzes
2. study material/resources for those assessments
3. upcoming homework
4. weekly goals
5. relevant school events

Avoid dumping historical planning prose.

### 4. Subject normalization

Normalize known timetable abbreviations where safe, while preserving the original.

Examples may include:
- MA → Matematik
- SV → Svenska
- EN → Engelska
- NO → NO / science context

Do not guess ambiguous abbreviations.

---

## Phase 2 — Stateful workflow

### 5. Improve change semantics

Distinguish:
- newly published
- modified
- rescheduled
- resource added
- preparation instructions updated
- completed/disappeared

Avoid noisy changes from irrelevant formatting/HTML differences.

### 6. Stable identifiers

Define durable keys for normalized items across extraction runs.

Source IDs should be preferred where stable.

Derived assessments need deterministic IDs.

---

## Phase 3 — Google Calendar

**Status: implemented** (`google_calendar_sync.py`). Per-child calendar auto-created,
idempotent create/update/delete via a local stable-identity state file, OAuth refresh
token cached in the OS keychain. Each self-hosted deployment brings its own Google Cloud
OAuth client (see README.md "Google Calendar sync" and CLAUDE.md "Distributable design").
Regular timetable sync (`--sync-timetable`) is also implemented, opt-in, into a separate
per-child calendar so it can be toggled independently of assessments/homework — syncs
exact daily lesson instances rather than a recurring rule. Scheduled/unattended runs
are implemented too (`scheduling/{macos,windows,linux}/`, see README "Running
automatically").

Goal:

Use Google Calendar as a selected output, not the canonical data store.

Suggested calendar policy:

### Sync
- tests
- exams
- quizzes
- important homework deadlines
- important school events

### Usually do not sync
- every weekly goal
- every planning post
- all study topics
- all timetable lessons

unless explicitly configured.

Calendar descriptions should contain:
- what to study
- preparation instructions
- key resource links
- InfoMentor source link where available

Potential calendars:

```text
School – Alex
School – Sam
```

or optionally separate:
- Assessments
- Homework

Design sync to be idempotent.

Never create duplicate events on repeated runs.

---

## Phase 4 — Notifications

Notify only on actionable changes.

Examples:

- New test announced
- Test moved from Thursday to Tuesday
- New preparation resource added
- Homework deadline added/changed

Potential delivery:
- Home Assistant
- Telegram
- mobile notifications
- email if desired

Avoid sending notifications for every extraction.

---

## Phase 5 — Study planning assistance

Once source extraction is trustworthy, derive optional preparation plans.

Example:

```text
Spanish test — Wed 23 Sep

Sun
- vocabulary

Mon
- AR/ER verb conjugation

Tue
- reading comprehension
- review Classroom exercises

Wed
- test
```

This must remain clearly derived and never be confused with teacher-provided instructions.

---

## Phase 6 — Dashboard / e-paper

**Status: started externally.** The separate InkBuddy project already consumes
`out/study-brief.json` (its `study_calendar` widget) and `out/generic-calendar.json`
(its `calendar` widget) via same-host file reads. Further dashboard-driven work in
this repo should still wait until the data pipeline is stable.

The dashboard should consume the normalized/Study Brief API, never InfoMentor directly.

Likely content:
- next assessment
- countdown
- what to study today
- homework due
- tomorrow's timetable
- newly changed items

---

## Technical debt / investigation areas

- Validate session lifetime and headless reuse over time.
- Confirm endpoint behavior after InfoMentor deployments/updates.
- Add anonymized fixtures for more calendar/planning type variants.
- Improve malformed/complex HTML handling.
- Improve Swedish date expression coverage.
- ~~Add explicit schema/version to normalized JSON.~~ Done (`schema_version: 3`); `generic-calendar.json` still has no version field.
- Consider structured logging.
- Consider a local configuration file for sync/display preferences.

---

## Definition of success

The system is successful when a parent can answer, without manually browsing InfoMentor:

- What tests are coming?
- What exactly should each child study?
- Where are the materials?
- What homework is due?
- Did anything important change?
- What should we focus on this week?
