# InfoMentor Extractor — Agent Instructions

## Project goal

This project extracts school information from InfoMentor Hub and turns it into structured, actionable study information for a parent.

The primary goal is **not** simply calendar synchronization.

The most important use case is helping a parent understand:

1. Upcoming tests, exams, quizzes and important deadlines
2. What each child needs to study
3. How they should prepare
4. Links, files and resources required for preparation
5. Homework and weekly goals
6. What changed since the previous extraction
7. Regular timetable information as supporting context

Consumers: Google Calendar (implemented), the InkBuddy e-paper dashboard (implemented,
reads the Study Brief file), and notifications (planned).

---

## Core principles

### 1. Preserve source truth

Never replace teacher-provided text with derived interpretation.

Derived information may be added, but preserve:
- original description
- source ID
- source type
- source URL
- provenance
- whether a field/date was derived

Teacher-provided instructions are authoritative.

### 2. Separate extraction from interpretation

Maintain these conceptual layers:

InfoMentor
→ raw extraction
→ deterministic normalization
→ Study Brief
→ change detection
→ external integrations

Do not couple InfoMentor extraction directly to Google Calendar, notifications or UI code.

### 3. Authentication: BankID stays manual; direct username/password may be automated under strict conditions

BankID/SSO login is never automated. DO NOT attempt to:
- automate BankID
- capture BankID credentials
- bypass BankID/SSO authentication

For accounts where InfoMentor itself supports direct username/password login (no BankID
step), automated login is permitted **only** under all of the following conditions:
- the credential is entered once, interactively, via a dedicated credential-management
  command (e.g. `manage_credentials.py`) that prompts with `getpass` so it never appears
  in shell history, process listings, or logs
- the credential is stored exclusively in the OS keychain (via the `keyring` package) —
  never in a plaintext file, never in an environment variable committed anywhere, never
  inside normalized/extracted JSON
- the credential is never printed, logged, or included in error messages/exceptions
- scheduled/headless runs read the credential from the keychain at run time only

Authentication uses a persistent Playwright browser profile (session cookies), regardless
of which login path is used.

If the session is expired and BankID/SSO is the login method:
- open the browser
- allow the user to authenticate normally
- tolerate InfoMentor/SSO redirect chains
- wait for the authenticated Hub session
- continue extraction

If the session is expired and username/password login is configured:
- submit the stored credential to InfoMentor's own login form (never to a third-party
  or BankID endpoint)
- fall back to the manual flow above if automated login fails, rather than retrying
  indefinitely or prompting for a credential outside the dedicated management command

**Google Calendar OAuth follows the same storage rule.** The one-time browser consent
flow's resulting refresh token is cached exclusively in the OS keychain, never in a
`token.json` or any other plaintext file. This project is designed for distributable,
self-hosted use (see "Distributable design" below): each deployment brings its own Google
Cloud OAuth client (`client_secret.json`, kept local and gitignored, never committed or
shared across deployments) — do not hard-code or bundle a shared OAuth client for
multiple unrelated households to use.

### 3a. Distributable design

This project is built to be cloned and self-hosted independently by unrelated families,
not run as a shared hosted service. Concretely:
- Never assume a single family/child/school; nothing may be hard-coded that would only
  work for one deployment (already covered by "never hard-code pupil IDs/child
  names/school-specific IDs" below).
- Per-deployment secrets (InfoMentor credential, Google OAuth client, refresh tokens)
  live only in that machine's OS keychain or local gitignored files — never centralized,
  never shared between deployments.
- Do not build multi-tenant infrastructure (shared server, per-tenant data isolation,
  tenant onboarding UI) unless explicitly requested — that is a different, much larger
  system than what this project currently is.

### 4. Be conservative when deriving dates

Teacher descriptions sometimes contain dates such as:

- `Förhöret genomförs tisdagen den 22 september`
- `Prov onsdag v.39`

Derived dates are useful but must remain explicitly marked, for example:

- `derived: true`
- `derived_from_description: true`

Never silently replace InfoMentor-provided dates with inferred dates.

### 5. Assessments are more important than normal calendar entries

Conceptually classify:

**HIGH**
- exam
- test / `prov`
- quiz / `läxförhör` / `förhör`
- homework deadline

**MEDIUM**
- weekly goals
- assignments
- preparation tasks

**NORMAL**
- school events

**BACKGROUND**
- regular timetable lessons

Do not present all calendar entries as equivalent.

### 6. Resource extraction must inspect content

Do not rely only on InfoMentor flags such as `hasLinks`.

Resources may occur inside:
- HTML descriptions
- plain text
- task links
- planning entries
- `attachments`
- `resources[]`
- `links[]`
- `files[]`
- `references[]`
- O365 resources

Recognize providers where possible:
- Glosboken
- Google Docs / Google Classroom references
- Magma
- InfoMentor resources
- other URLs

### 7. Planning data is important

Planning units may contain future assessments before they appear as dedicated calendar entries.

Example:

`Prov onsdag v.39`

Planning must therefore participate in assessment discovery and resource association.

### 8. Avoid destructive deduplication

InfoMentor may represent the same assessment through multiple objects.

Do not delete apparent duplicates unless there is a reliable identity rule.

Prefer:
- linking related records
- provenance
- canonical display grouping

over destructive merging.

---

## Current pipeline

`run_pipeline.py`

1. Extract from InfoMentor
2. Normalize
3. Generate Study Brief
4. Compare against previous state
5. Produce change report

Typical outputs:

```text
out/
├── infomentor-current.json
├── study-brief.json
├── study-brief.md
├── changes.json
└── changes.md
```

---

## Change detection

Meaningful changes include:

- new assessment
- changed assessment date
- new homework
- changed study instructions
- newly added resource
- removed resource
- item removed/completed
- changed priority/title

New tests/quizzes and assessment date changes should receive the highest priority.

---

## Engineering guidelines

- Prefer Python unless there is a strong reason otherwise.
- Keep modules small and testable.
- Prefer deterministic parsing over LLM calls.
- Add regression tests for every InfoMentor edge case discovered.
- Never hard-code pupil IDs.
- Never hard-code child names.
- Never hard-code school-specific IDs where they can be discovered.
- Never commit `.infomentor-browser-profile`.
- Never commit authentication/session data.
- Never commit extracted private school data.
- Keep raw API responses available for debugging.
- Preserve backwards compatibility with existing normalized JSON where practical.

Before changing classification or date-parsing rules, add a fixture or regression test demonstrating the behavior being changed.

---

## Privacy

This project handles children's school information.

Treat all extracted data as private.

`.gitignore` should exclude at minimum:

```text
.infomentor-browser-profile/
.infomentor-state/
out/
infomentor*.json
study-brief*.json
changes*.json
client_secret*.json
token.json
.infomentor-config.json
logs/
```

Scheduled-run logs (`logs/`) print item titles/subjects and are therefore private child
data, same as any other extracted output — never commit them.

Do not add real pupil data to test fixtures. Use anonymized synthetic fixtures.

---

## Before editing

When starting work:

1. Read `README.md`.
2. Read `CLAUDE.md`.
3. Read `ARCHITECTURE.md`.
4. Read `DATA_MODEL.md`.
5. Read `ROADMAP.md`.
6. Inspect tests before changing behavior.
7. Run the current test suite.
8. Understand the current pipeline before refactoring.

Do not perform broad refactors unless they directly support an agreed goal.

---

## Immediate development priorities

1. Improve assessment detection and duplicate linking/grouping.
2. Improve association between assessments and relevant planning/resources.
3. Improve Study Brief quality and actionability.
4. Improve state/change tracking.
5. ~~Add Google Calendar integration for selected important dates.~~ Done
   (`google_calendar_sync.py`; scheduling for all three OSes is done too).
6. Add notification delivery.
7. E-paper/dashboard integration comes later. (The separate InkBuddy project already
   consumes `out/study-brief.json` and `out/generic-calendar.json`; don't add
   dashboard-specific work here unless asked.)

Do not prioritize UI work yet.
