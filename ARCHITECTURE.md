# Architecture

## Overview

The project is intentionally layered so that InfoMentor-specific behavior stays isolated from downstream consumers.

```text
InfoMentor Hub
      │
      ▼
Extraction
      │
      ▼
Raw data
      │
      ▼
Normalization
      │
      ├── schedule
      ├── important dates
      ├── study items
      ├── planning
      ├── resources
      └── notifications
      │
      ▼
Study Brief
      │
      ▼
Change detection
      │
      ├── future Google Calendar sync
      ├── future notifications
      └── future e-paper/dashboard
```

---

## 1. Authentication

Authentication is browser-session based.

The project uses Playwright with a persistent Chromium profile.

The login flow may involve several redirects:

```text
InfoMentor Hub
→ InfoMentor/legacy Mentor login
→ municipality/SSO
→ BankID
→ InfoMentor callback
→ authenticated Hub session
```

BankID itself is never automated.

For InfoMentor accounts that support a direct username/password login (no BankID step),
that specific login path may be automated, but only with the credential stored in the OS
keychain and a bounded retry that falls back to the manual flow — see CLAUDE.md principle
3 for the full constraints. This is a distinct login path from BankID/SSO and never
touches it.

The extractor should tolerate redirect races and wait for the final authenticated Hub state rather than assuming an intermediate navigation must complete cleanly.

Session state is stored only in the local persistent browser profile.

---

## 2. Extraction layer

The extractor obtains data from the same endpoints used by InfoMentor Hub.

Known useful areas include:

- timetable
- calendar entries
- tasks
- notifications
- planning / units of learning
- pupil switching

The extractor discovers pupils dynamically and switches pupil context rather than hard-coding pupil IDs.

Raw responses should be preserved because InfoMentor data structures may change and future normalization improvements may need fields that are not currently surfaced.

---

## 3. Normalization layer

Normalization converts InfoMentor-specific structures into a stable internal model.

The normalized model separates:

- regular schedule
- important dates
- study items
- resources
- planning
- notifications

Normalization should be deterministic and source-preserving.

Where information is inferred from teacher-written text, the inferred field must remain marked as derived.

---

## 4. Assessment discovery

Assessments may come from several places:

- calendar entries
- InfoMentor tasks
- planning text
- teacher descriptions
- embedded text such as `Prov onsdag v.39`

Therefore assessment discovery must not rely on calendar type IDs alone.

The system should detect:
- tests / `prov`
- quizzes / `förhör` / `läxförhör`
- homework
- weekly goals
- school events

Planning-derived assessments must retain provenance to the planning item that produced them.

---

## 5. Resource extraction

Resources may be represented as structured objects or embedded inside HTML/text.

Resource extraction should inspect:
- task descriptions
- calendar descriptions
- planning content
- links
- files
- references
- attachments
- O365 resources
- direct InfoMentor downloadable resources

Known providers include:
- Glosboken
- Google Docs / Google Classroom references
- Magma
- InfoMentor-hosted files

Resources should be normalized independently so they can later be attached to assessments or preparation plans.

---

## 6. Study Brief

The Study Brief is the family-facing derived layer.

It should prioritize:
1. upcoming tests/exams/quizzes
2. what to study
3. preparation instructions
4. preparation resources
5. homework/deadlines
6. weekly goals
7. background schedule only where useful

The Study Brief is not intended to be a raw dump of teacher planning.

It should remain concise and actionable while preserving links to the source/provenance.

---

## 7. Change detection

The project compares successive normalized states.

Important changes include:
- new assessment
- assessment date changed
- new homework
- preparation instructions changed
- resource added
- resource removed
- item disappeared/completed

This is intentionally separate from extraction.

External notifications should eventually consume change events rather than independently inspecting InfoMentor.

---

## 8. Future integrations

### Google Calendar — implemented (`google_calendar_sync.py`)

Google Calendar receives only calendar-appropriate information, consuming the same
item-construction logic as the Study Brief (`study_brief.py`) rather than InfoMentor
directly. Each self-hosted deployment configures its own Google Cloud OAuth client (see
CLAUDE.md "Distributable design"); the refresh token is cached in the OS keychain.

Likely candidates:
- tests/exams/quizzes
- hard homework deadlines
- important school events

Do not automatically push:
- every weekly goal
- every planning note
- preparation text as separate events
- every timetable lesson unless explicitly configured

Preparation material belongs in event descriptions or the Study Brief.

**Timetable sync — implemented, opt-in (`--sync-timetable`).** The regular timetable goes
into its own per-child calendar, separate from tests/homework/events, so it can be
toggled independently. Syncs exact daily lesson instances rather than a recurring weekly
rule, so substitute teachers and one-off schedule changes are reflected accurately instead
of assumed from a "typical" pattern.

### Generic Google Calendar sync — implemented (`google_calendar_read_sync.py`)

Unrelated to the push sync above and to InfoMentor entirely: reads events from
arbitrary personal/work Google accounts/calendars (multi-account, multi-calendar),
normalizing them into `out/generic-calendar.json` for a non-InfoMentor consumer such as
an e-paper dashboard's generic calendar widget. Uses OAuth's Device Authorization Grant
(`google_device_flow.py`) rather than a browser-based flow, since it's meant to run
unattended on a headless host; accounts are linked via a small local web UI
(`calendar_link_ui.py`). Each linked account's refresh token is cached in its own OS
keychain entry, same storage rule as the push sync, under a distinct service name.

### Notifications

Notifications should be driven by meaningful change events, for example:
- new test discovered
- test date moved
- new resource for an upcoming assessment
- new homework deadline

### Scheduling and status — implemented (`scheduling/`, `run_pipeline.py`, `sync_status.py`)

Per-OS installers: launchd (macOS), Task Scheduler (Windows), systemd `--user` timers
(Linux: pipeline at 06:30/18:30, generic calendar read sync every 15 min).
`run_pipeline.py` writes `sync_status.json`; optional desktop status apps
(`menubar_app.py` on macOS, `tray_app.py` on Windows) show it and offer "Sync Now".
There is no status UI on Linux.

### E-paper

The e-paper/dashboard is a consumer of the normalized/Study Brief layer. The separate
InkBuddy project already does this (Study Brief + generic calendar files, same host).

It should never depend directly on InfoMentor endpoints.

---

## Design constraints

- Private child data must stay local unless explicitly integrated with another service.
- BankID remains manual.
- Authentication details are not source data.
- Raw source data and derived data must remain distinguishable.
- Downstream integrations should not depend on InfoMentor internal type IDs.
