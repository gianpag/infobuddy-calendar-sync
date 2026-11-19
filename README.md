# InfoMentor Study Extractor v2

A local Python/Playwright extractor for `https://hub.infomentor.se`, designed around the practical parent question:

> **What do the children need to study, by when, and what material should they use?**

The extractor still captures the normal timetable, but the normalized output now prioritizes tests, quizzes, homework, study goals, preparation instructions and learning resources.

## Documentation

- [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md): data flow (InfoMentor → Study Brief,
  Google Calendar read sync → InkBuddy), processes, credentials.
- [docs/INSTALL.md](docs/INSTALL.md): setup, keyring (including cryptfile on headless
  hosts), accounts-UI login, systemd units and linger.
- [docs/MAINTENANCE.md](docs/MAINTENANCE.md): `inkbuddy` CLI, updates, re-linking
  Google, BankID, backups, troubleshooting.
- [docs/USER_GUIDE.md](docs/USER_GUIDE.md): the Google Calendar accounts UI and Study
  Brief.
- [Privacy Policy](PRIVACY.md): your data stays on your machine; the author collects
  nothing.
- [Terms of Service](TERMS.md): not a service; provided as is, at your own risk.
- Companion project: **InkBuddy**, which has the e-paper firmware, the feed-service
  and the visual designer.
- License: [MIT](LICENSE)

## Quickstart

The shortest path from a fresh clone to a daily-runnable command.

**1. Install:**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
playwright install chromium
```

**2. Choose how you'll log in to InfoMentor** — pick one:

- **BankID (default, zero config):** skip to step 4. A browser window will open the first
  time you run anything, and you complete the normal BankID flow yourself.
- **Username/password (only if your InfoMentor account supports it without BankID):**
  store it once, interactively:

  ```bash
  python manage_credentials.py set --username YOUR_USERNAME
  ```

  This also saves `YOUR_USERNAME` as the default, so you won't need to pass `--username`
  again. The password itself only ever lives in your OS keychain — see CLAUDE.md
  principle 3 if you want the full details.

**3. (Optional) Set up Google Calendar sync** — skip this if you just want the Study
Brief/change reports. See "Google Calendar sync" below for the one-time Google Cloud
Console steps; once you have `client_secret.json`, just drop it in the project's root
directory (already covered by `.gitignore`).

**4. Run it:**

```bash
python run_pipeline.py --headless --sync-calendar
```

That's the whole command for every future refresh — no other flags required once steps 2–3
are done. It extracts a 4-week window, regenerates the Study Brief, detects what changed
since last time, and syncs to Google Calendar, in that order.

**What happens with no flags — the defaults:**

| Thing | Default if you don't specify it |
|---|---|
| Extraction window | Current week through 4 weeks out (`infomentor_extract.py`'s `default_range()`) |
| InfoMentor login | BankID/manual, unless a username was stored via `manage_credentials.py` (then automated login is attempted first, with a 45s timeout before falling back to manual) |
| InfoMentor username | Whatever `manage_credentials.py set` last stored (in `.infomentor-config.json`, local and gitignored — never a password) |
| Browser profile | `.infomentor-browser-profile/` in the project directory |
| Output files | `out/infomentor-current.json`, `out/study-brief.{json,md}`, `out/changes.{json,md}` |
| Change-detection baseline | `.infomentor-state/latest.json`, created automatically on first run |
| Google OAuth client | `./client_secret.json` if present, otherwise you must pass `--google-client-secret` |
| Calendar sync horizon | 60 days ahead (independent of the 4-week extraction window; can't exceed it in practice) |
| Calendar sync scope | tests, exams, quizzes, homework, school events — not weekly goals, not regular timetable lessons |

Every default can be overridden with an explicit flag; run any script with `--help` to see them all.

## Authentication

The project **does not automate BankID** and never needs BankID credentials.

It launches Chromium with a persistent local profile. If InfoMentor asks you to authenticate, complete the normal InfoMentor/BankID flow in that browser. The extractor then reuses the authenticated InfoMentor session. When the session expires, run visibly again and authenticate normally.

Treat `.infomentor-browser-profile/` as sensitive and never commit or share it.

### Automated login (accounts without BankID)

If your InfoMentor account supports a direct username/password login (no BankID step),
you can opt into automated login for unattended/scheduled runs. This is a distinct login
path from BankID/SSO — see CLAUDE.md principle 3 for the exact constraints it must
satisfy (keychain-only storage, no plaintext files, bounded retry with fallback to manual
login).

Store the password once, interactively (never as a CLI argument, never in a file):

```bash
python manage_credentials.py set --username YOUR_USERNAME
```

That also saves `YOUR_USERNAME` as the local default (in `.infomentor-config.json`, never
the password), so `--username` doesn't need to be passed again:

```bash
python infomentor_extract.py --headless
python run_pipeline.py --headless
```

The password is always read from the OS keychain at run time, never from this file. Pass
`--username` explicitly to override the saved default (e.g. more than one deployment
sharing a machine).

If automated login doesn't complete within ~45 seconds, it falls back to the normal
manual/BankID flow (which will hang waiting for a human if run headless — automated login
is meant for accounts where it reliably succeeds). To remove a stored credential:

```bash
python manage_credentials.py clear --username YOUR_USERNAME
```

## Setup

Requires Python 3.10+.

```bash
python3 -m venv .venv
source .venv/bin/activate        # macOS/Linux
# .venv\\Scripts\\activate       # Windows PowerShell

pip install -r requirements.txt
playwright install chromium
```

## Run

```bash
python infomentor_extract.py
```

By default it extracts a 4-week window: the current school week plus the following three weeks.

Choose a range:

```bash
python infomentor_extract.py \
  --start 2026-09-14 \
  --end 2026-09-27 \
  --output infomentor.json
```

After a successful visible login, headless runs may work while the session remains valid:

```bash
python infomentor_extract.py --headless
```

## Normalized model (`schema_version: 3`)

For every child:

```text
normalized/
├── schedule
│   └── regular lessons, times, rooms, teachers
├── important_dates
│   ├── tests
│   ├── quizzes
│   ├── homework
│   ├── assignments
│   ├── school_events
│   └── other
├── study_items
│   ├── what_to_study
│   ├── preparation_instructions
│   ├── weekly_goals
│   ├── topics
│   ├── assessment_mentions
│   └── resources
├── resources
│   └── deduplicated links/files across tasks and planning
├── planning
│   └── current learning units and materials
└── notifications
```

The original InfoMentor API payloads remain under `raw` for traceability and future improvements.

## Important-date logic

Classification is deterministic rather than AI-generated.

Strong signals include:

- `prov`, `test`, `exam` → test, high priority
- `läxförhör`, `förhör`, `quiz`, `exit ticket` → quiz, high priority
- `läxa`, `hemuppgift` → homework, high priority
- `veckans mål`, `veckans matte` → weekly goal
- school event phrases such as `idrottsdag`, `fotografering`, `studiedag` → school event

Known InfoMentor calendar types observed in the supplied data are also used as signals, but raw type IDs are preserved so the mapping can be adjusted if InfoMentor changes behavior.

### Embedded assessment dates

A weekly goal can mention a different future test date in its description. v2 keeps these separate.

Example conceptually:

```json
{
  "kind": "weekly_goal",
  "date": "2026-09-17",
  "assessment_mentions": [
    {
      "kind": "quiz",
      "date": "2026-09-22",
      "text": "Förhöret ... genomförs tisdagen den 22 september.",
      "derived": true
    }
  ]
}
```

Only dates that can be resolved conservatively are promoted into `important_dates`. Teacher wording is preserved.

## Resource extraction

Resources are discovered from multiple places, not only InfoMentor's `hasLinks` flag:

- URLs embedded directly in task/calendar HTML descriptions
- InfoMentor `links`, `files`, `references`, attachments and O365 objects
- planning-unit resources
- downloadable InfoMentor PDFs/files

Known providers are labeled when possible, including:

- Glosboken
- Google Classroom / Google
- Magma
- InfoMentor downloadable resources
- YouTube

For pasted links, the extractor tries to use the teacher's preceding label as the resource title.

Example:

```json
{
  "type": "link",
  "provider": "Glosboken",
  "title": "Träna på hur ar-verb böjs",
  "url": "https://...",
  "source": {
    "type": "planning",
    "source_id": "..."
  }
}
```

## Study guidance and provenance

The normalizer extracts useful teacher-written sentences into:

- `what_to_study`
- `preparation_instructions`
- `weekly_goals`
- `topics`

This is intentionally conservative. It does **not** silently invent study instructions. Each study item keeps its original `description` and a `source` object so downstream systems can always trace the normalized data back to InfoMentor.

## Re-normalize an existing extraction without logging in

While improving classification rules, you do not need to hit InfoMentor again if you already have a JSON containing `raw` data:

```bash
python renormalize.py infomentor.json --output infomentor.v2.json
```

This rebuilds only the normalized layer and preserves the existing raw extraction.

## Current internal InfoMentor endpoints

Observed endpoints used by the extractor:

- `POST /authentication/authentication/isauthenticated/`
- `GET /Account/PupilSwitcher/SwitchPupil/<id>`
- `POST /timetable/timetable/gettimetablelist`
- `POST /calendarv2/calendarv2/getentries`
- `POST /Task/Task/GetTask`
- `POST /NotificationApp/NotificationApp/appData`
- `POST /uolv2/uolv2/appData`
- `POST /UolV2/UolV2/GetUol`

These are internal Hub endpoints rather than a documented public API, so InfoMentor may change them.

## Security

- Never commit `.infomentor-browser-profile/`.
- Do not share extracted JSON publicly; it contains personal school information.
- Do not export session cookies or BankID data into source code.
- The extractor performs reads only; it does not modify InfoMentor records.

## Study Brief layer

The project now includes `study_brief.py`, which turns normalized InfoMentor data into a parent-friendly action view.

It deliberately separates:

- assessments/tests/quizzes that need preparation
- homework and weekly goals
- school events
- ordinary timetable lessons (reference only)

It also searches planning text for explicit dated items that have not yet appeared in the calendar. Swedish ISO-week phrases such as `Prov onsdag v. 39` are resolved conservatively using the extraction/reference date. These derived dates are marked as coming from planning rather than pretending they were calendar events.

Planning subjects are resolved through InfoMentor's own subject lookup, so internal IDs such as `5006` become labels such as `Moderna språk (språkval)`.

Run it against an existing extraction:

```bash
python study_brief.py infomentor.v3.json \
  --as-of 2026-09-12 \
  --days 28 \
  --json-output study-brief.json \
  --markdown-output study-brief.md
```

The Markdown output is useful for quick human review; the JSON output is intended as the stable input for future Google Calendar, notifications, Home Assistant, or e-paper integrations.

### Duplicate handling

InfoMentor can represent the same assessment through more than one source. The brief merges only obvious duplicates on the same date and of the same assessment kind, while retaining all original source/provenance objects.

### Current design principle

The Study Brief is teacher-grounded. It does not invent study material. Derived dates are only promoted when the teacher text contains an explicit resolvable date/week phrase, and the original wording and source ID are retained.

## Stateful change detection

`change_watch.py` compares normalized extractions and reports only meaningful changes relevant to study control.

It currently detects:

- **new test / quiz / homework / weekly goal**
- **test or deadline date changed**
- **teacher study instructions changed**
- **resource added or removed**
- title/priority changes
- an item disappearing from the latest extraction

Assessment date changes and newly announced tests/quizzes are treated as the highest-severity changes.

### Easiest mode: keep a local baseline automatically

After each new extraction:

```bash
python change_watch.py infomentor.v3.json \
  --state-dir .infomentor-state \
  --json-output changes.json \
  --markdown-output changes.md
```

The **first run** creates `.infomentor-state/latest.json` as a baseline and reports no changes. Every later run compares the current extraction with that baseline and then updates the baseline.

The state folder contains school data and should remain private; add it to `.gitignore`.

### Explicit comparison

```bash
python change_watch.py previous.json current.json \
  --json-output changes.json \
  --markdown-output changes.md
```

This is useful for testing and does not mutate a baseline.

### Output model

`changes.json` is designed to become the trigger layer for future notifications and calendar updates:

```json
{
  "change_count": 2,
  "changes": [
    {
      "type": "date_changed",
      "severity": "critical",
      "child": "Alex",
      "item": {
        "kind": "test",
        "date": "2026-09-18",
        "title": "Samhällsprov"
      },
      "before": {"date": "2026-09-17"},
      "after": {"date": "2026-09-18"}
    }
  ]
}
```

The change detector compares stable InfoMentor source IDs where available. For planning-derived assessments whose generated IDs may include dates, it uses the planning source plus a cautious title/subject similarity fallback so a moved test is more likely to be recognized as a date change rather than a deletion plus a new item.

## One-command pipeline

Once setup is complete, the easiest operational workflow is:

```bash
python run_pipeline.py
```

That performs, in order:

1. a fresh InfoMentor extraction
2. regeneration of the current Study Brief
3. comparison against the previous local baseline
4. generation of `changes.json` and `changes.md`

Outputs go to `out/` by default:

```text
out/
├── infomentor-current.json
├── study-brief.json
├── study-brief.md
├── changes.json
└── changes.md
```

The first pipeline run establishes the baseline. Subsequent runs only put newly detected differences in the `changes.*` files.

If the browser session is still authenticated, use:

```bash
python run_pipeline.py --headless
```

For a larger extraction window:

```bash
python run_pipeline.py \
  --start 2026-09-12 \
  --end 2026-10-11 \
  --days 21
```

A wider extraction range is useful because tests can be announced several weeks ahead in planning text even when they have not yet appeared as standalone calendar entries.

## Google Calendar sync

Syncs tests, exams, quizzes, homework and school events (not weekly goals, not regular
timetable lessons, not every planning note — see ROADMAP.md Phase 3) to a dedicated
Google Calendar per child ("School – ChildName"), created automatically if it doesn't
exist. Sync is idempotent: re-running updates existing events in place and removes events
for items that have genuinely disappeared from InfoMentor, rather than creating
duplicates.

**This project is designed to be self-hosted independently by each family** (see
CLAUDE.md "Distributable design") — there is no shared Google OAuth client. Each
deployment creates its own:

1. In [Google Cloud Console](https://console.cloud.google.com/): create a project, enable
   the **Google Calendar API**, configure the OAuth consent screen (External, add
   yourself as a test user), then create an **OAuth client ID** of type **Desktop app**
   and download the resulting `client_secret.json`. Place it in the project's root
   directory — that's the default location every script looks for it (already covered by
   `.gitignore`, but don't commit it regardless). Anywhere else works too via
   `--client-secret`/`--google-client-secret`.
2. Run a sync once to complete the one-time browser consent (opens a real browser window
   to log into Google and grant Calendar access):

   ```bash
   python google_calendar_sync.py out/infomentor-current.json
   ```

   The resulting refresh token is cached in the OS keychain (never a plaintext file), so
   subsequent runs — including headless ones — don't need the browser again.
3. Preview without touching Google Calendar at all:

   ```bash
   python google_calendar_sync.py out/infomentor-current.json --dry-run
   ```
4. Fold it into the one-command pipeline:

   ```bash
   python run_pipeline.py --sync-calendar
   ```

Useful flags: `--kinds test,exam,quiz,homework,school_event` (the default; comma-separated
list of study-item kinds to sync), `--days 60` (sync horizon), `--account` (keychain label,
useful if syncing to more than one Google account from the same machine).

### Re-linking the push account on a headless host

The push sync's first-time consent normally opens a local browser. On a headless host
(SSH-only server, systemd timer) that's impossible, so the sync **never** tries it there:
when no usable token is cached it exits with code 3 and a re-link hint, and
`run_pipeline.py` still writes the extraction, Study Brief and changes outputs, then
records `"needs_relink": true` in `.infomentor-state/sync_status.json` (shown by the
tray/menu bar apps) and exits nonzero. `--headless` pipeline runs also pass
`--non-interactive` to the calendar step explicitly.

To (re)authorize without a browser on that host, use the device flow. It needs the
same "TVs and Limited Input devices" client (`client_secret_device.json`) as the
generic calendar sync below:

```bash
python google_calendar_sync.py --link-device --account default
# prints a URL + code; approve on any device's browser; the token goes to the keychain
```

### Regular timetable (optional)

The recurring day-to-day timetable (which subjects happen when) is intentionally *not*
synced by default — see ARCHITECTURE.md/CLAUDE.md: it's background context, not something
that should compete with tests/homework for attention, so it's opt-in:

```bash
python google_calendar_sync.py out/infomentor-current.json --sync-timetable
python run_pipeline.py --sync-calendar --sync-timetable
```

This creates a separate calendar per child ("School – ChildName – Timetable"), so you can
show/hide it independently of tests/homework/events in Google Calendar's UI. It syncs the
*exact* lesson scheduled each day within the extraction window — not a recurring weekly
rule — so substitute teachers, room changes, and one-off schedule changes are always
reflected accurately rather than assumed from a "typical" pattern. It's idempotent the
same way assessment sync is: unchanged lessons are left alone, changed ones (room/teacher/
subject) are updated in place, and lessons that are still upcoming but have genuinely
disappeared (a real schedule change) have their event removed — a lesson simply aging past
today as the rolling 4-week window moves forward is not treated as a removal.

Timezone defaults to `Europe/Stockholm`; override with `--timezone` if needed.

## Generic Google Calendar sync

Separate from the Google Calendar sync above, which *pushes* InfoMentor study items
into per-child calendars. This one *reads* events from arbitrary personal/work Google
accounts/calendars for a non-InfoMentor consumer -- e.g. a generic calendar widget on
an e-paper dashboard (InkBuddy). It has no InfoMentor dependency at all (CLAUDE.md
principle 2: don't couple extraction to external integrations).

Supports linking more than one Google account and more than one calendar per account.
Designed to run unattended on a headless host (no local browser) via OAuth's **Device
Authorization Grant** -- the same "go to a URL, enter a code" flow TVs and streaming
boxes use, so you complete the one-time consent on your phone or laptop, not on the
headless machine itself.

**One-time setup** (per deployment, same self-hosted-OAuth-client model as the push
sync above, but this needs a *different* Google Cloud OAuth client type):

1. In the same (or a new) [Google Cloud Console](https://console.cloud.google.com/)
   project, with the Calendar API already enabled, create a second **OAuth client ID**
   -- this time of type **"TVs and Limited Input devices"** (not Desktop app/Web
   application; those aren't accepted for this grant type). Download the JSON and save
   it as `client_secret_device.json` in the project root (already covered by
   `.gitignore`).
2. Start the account-linking web UI:

   ```bash
   python calendar_link_ui.py
   ```

   Open `http://<this-machine>:8200/` in any browser (doesn't need to be the same
   machine), click **Connect a Google account**, then visit the shown URL and enter the
   code on whatever device you're reading this on. Once linked, pick which of that
   account's calendars should sync. Repeat for as many accounts as you want.
3. Run a sync:

   ```bash
   python google_calendar_read_sync.py
   ```

   Writes `out/generic-calendar.json` (`{"events": [{title, start, end, all_day,
   location?, source_id}]}`) merging every selected calendar across every linked
   account. A no-op (doesn't touch the file) if no accounts are linked yet. Useful
   flags: `--output` (default `out/generic-calendar.json`), `--days` (sync horizon,
   default 60).

Each linked account's refresh token is cached exclusively in the OS keychain (service
name `infomentor-extractor-generic-calendar-read`, distinct from the push sync's own
keychain entries) -- never a plaintext file. `.google-calendar-accounts.json` (local,
gitignored) stores only which accounts/calendars are selected, never a credential.

See "Running automatically" below for scheduling `google_calendar_read_sync.py`
(`scheduling/linux/install.sh` installs both this and the InfoMentor pipeline as
separate systemd timers -- this one runs every 15 minutes, since it's a cheap API call
with no browser involved, unlike the InfoMentor pipeline).

**Manual end-to-end verification checklist** (the interactive consent step can't be
automated/tested, same as InfoMentor's own BankID flow):

1. Complete the Google Cloud Console steps above; confirm `client_secret_device.json`
   exists.
2. Run `python calendar_link_ui.py`, open it in a browser, click **Connect a Google
   account**.
3. Visit the shown verification URL (any device), enter the code, approve access.
4. Confirm the UI shows "Connected as &lt;your email&gt;" within a few seconds, then
   select at least one calendar with events in the next 60 days.
5. Run `python google_calendar_read_sync.py` and confirm it prints a non-zero event
   count and that `out/generic-calendar.json` contains real event titles/times matching
   that calendar.

### Calendar accounts UI login and autostart

`calendar_link_ui.py` (the Google Calendar accounts UI on :8200) is protected by a login
once credentials are set:

```bash
.venv/bin/python manage_link_ui_credentials.py --username <you>          # prompts
.venv/bin/python manage_link_ui_credentials.py --username <you> --password-stdin <<<"$PW"
```

The username and password are stored only in the keyring, under service
`infobuddy-link-ui`; see `link_ui_auth.py`. This is InfoBuddy's own entry, even when you
use the same login as InkBuddy's admin.

- **Browsers** get a `/login` page and a session cookie (`HttpOnly`, `SameSite=Strict`;
  `INFOBUDDY_COOKIE_SECURE=1` adds `Secure` behind HTTPS).
- **Every POST** needs the session's CSRF token.
- **Failed logins** are rate-limited: 5 per 15 min per IP.
- **Scripts** can use HTTP Basic.

Until a login is set, the UI stays open and warns about it at startup. On a headless host,
load `KEYRING_CRYPTFILE_PASSWORD` first, the same way the units do.

On Linux, `scheduling/linux/install.sh` also installs **`infobuddy-link-ui.service`**, a
`systemd --user` service with `Restart=on-failure` and journal logs, so the UI starts at
boot (with linger enabled). InkBuddy's admin header links to it as "Google Calendar
accounts".

On a host running InkBuddy too, InkBuddy's **`inkbuddy` CLI** (`scripts/inkbuddy` in that
repo) manages all of these:

- `inkbuddy status` shows each unit's state, PID, uptime and port, the last pipeline
  result from `sync_status.json`, and when each device was last seen.
- `inkbuddy restart link`
- `inkbuddy logs pipeline -f`
- `inkbuddy run calsync`

## Running automatically

Once `run_pipeline.py --headless --sync-calendar --sync-timetable` works reliably by
hand (do that first — see Quickstart), you can schedule it to run unattended. Both
options below run it twice daily at 06:30 and 18:30; edit the schedule file before
installing if you want different times.

### macOS (launchd)

```bash
./scheduling/macos/install.sh
```

Installs a per-user LaunchAgent (`~/Library/LaunchAgents/com.infobuddy.pipeline.plist`).
Logs go to `logs/launchd.out.log` / `logs/launchd.err.log` in the project directory
(gitignored — they contain real item titles/subjects, same privacy rules as any other
extracted data). Test it immediately with `launchctl start com.infobuddy.pipeline`;
uninstall with the command the script prints at the end.

Caveats: a LaunchAgent only runs while you're logged in, and only fires exactly on
schedule if the Mac is awake — if it's asleep, launchd runs the job as soon as it wakes
instead of at the exact time. If you want the Mac to wake up for this, that's a separate
`pmset repeat wake` schedule (not configured by the install script).

### Windows (Task Scheduler)

```powershell
powershell -ExecutionPolicy Bypass -File scheduling\windows\install.ps1
```

Registers a per-user Scheduled Task (`InfoBuddyPipeline`) that runs
`scheduling\windows\run_pipeline.bat`, which activates the project's virtual environment
and logs output to `logs\pipeline.out.log` / `logs\pipeline.err.log`. Uses
`-StartWhenAvailable`, so a missed run (PC off/asleep) fires as soon as it's next on —
the closest equivalent to launchd's deferred-wake behavior. Test immediately with
`Start-ScheduledTask -TaskName InfoBuddyPipeline`; uninstall with
`Unregister-ScheduledTask -TaskName InfoBuddyPipeline`.

The core Python code has no OS-specific dependencies (the OS keychain integration via
`keyring`, Playwright, and the Google OAuth consent flow all work the same way on
Windows) — only the scheduling mechanism differs between platforms.

### Linux (systemd)

```bash
./scheduling/linux/install.sh
```

Installs a `systemd --user` timer (`infobuddy-pipeline.timer`) that runs
`infobuddy-pipeline.service` at 06:30 and 18:30, and enables lingering
(`loginctl enable-linger`) so it keeps firing with nobody logged in — needs `sudo` once.
Logs go to `logs/pipeline.out.log` / `logs/pipeline.err.log`. Test immediately with
`systemctl --user start infobuddy-pipeline.service`; check the schedule with
`systemctl --user list-timers infobuddy-pipeline.timer`; uninstall with the command the
script prints at the end.

The same script also installs a second, independent timer
(`infobuddy-generic-calendar-sync.timer`) running `google_calendar_read_sync.py` every
15 minutes — see "Generic Google Calendar sync" above. It's a no-op until you link a
Google account via `calendar_link_ui.py`; logs go to
`logs/generic-calendar-sync.out.log` / `.err.log`.

Two Linux-specific setup steps the macOS/Windows paths don't need:

- **Playwright's browser needs its system libraries installed explicitly** on most
  minimal Linux installs (Ubuntu Server, a fresh VM, etc.):
  ```bash
  .venv/bin/playwright install --with-deps chromium
  ```
  (`playwright install chromium` alone downloads the browser but not the OS-level shared
  libraries it needs to actually launch headless.)
- **`keyring` needs a running Secret Service provider** (this is what macOS Keychain and
  Windows Credential Manager give you automatically; a minimal/headless Linux install
  usually has neither running). **If this box is only ever accessed via a password
  login** (console or `PasswordAuthentication` SSH), `gnome-keyring` + PAM works:
  ```bash
  sudo apt install -y gnome-keyring
  # add to /etc/pam.d/common-auth:    auth optional pam_gnome_keyring.so
  # add to /etc/pam.d/common-session: session optional pam_gnome_keyring.so auto_start
  ```
  then log in once (that way) to unlock it before running `manage_credentials.py` or the
  pipeline for the first time.

  **If this box is accessed purely via SSH keys** (verified against a real VM: SSH
  pubkey auth never triggers PAM's auth stage at all, so the above *never* unlocks,
  not just "until next login"), `scheduling/linux/install.sh` instead sets up
  `keyrings.cryptfile` (an AES-encrypted file keyring — see `keyring_linux_bootstrap.py`)
  automatically: it generates a random master password into a `0600` file
  (`~/.config/infobuddy/keyring-secret.env`, shared with `infobuddy-dashboard`'s
  feed-service if both run as this same user) and wires it into the service via
  systemd's `EnvironmentFile=`. This only activates when that file is present/the
  `KEYRING_CRYPTFILE_PASSWORD` env var is set — macOS, Windows, and a Linux box with a
  real Secret Service provider are all unaffected. **Trade-off, stated plainly**: that
  master password is a secret living in a file (locked down to this user only), which is
  a real if narrower departure from "never a plaintext file" — it protects the file at
  rest, but the file's own location is only as safe as this account. Not equivalent to
  macOS/Windows' always-available, hardware-backed OS keychains; a deliberate, scoped
  trade-off for a headless/SSH-key-only host, not a general change to how credentials
  are stored elsewhere.

These Linux instructions/units are written carefully and the keyring fallback above was
verified end-to-end against a real SSH-key-only VM (including confirming the gnome-
keyring+PAM path genuinely does *not* work there) — but the rest (Playwright, systemd
timer behavior) hasn't had the same from-scratch verification as the macOS/Windows
paths. Sanity-check the first run.

### Windows system tray icon (optional)

A small system tray icon that shows when the pipeline last ran (color-coded: gray =
never run, blue = syncing, green = ok, red = failed) with a **Sync Now** button — the
Windows counterpart to the macOS menu bar app below, sharing the same status file and
logic (`sync_status.py`).

```powershell
pip install -r requirements.txt   # installs pystray + Pillow on Windows
python tray_app.py                # try it interactively first
```

Right-click the icon for: last-sync status, **Sync Now** (runs `run_pipeline.py
--headless --sync-calendar --sync-timetable`, same flags as the scheduled task —
override via `"manual_sync_args"` in `.infomentor-config.json`), and shortcuts to open
the latest Study Brief, Changes report, and log file.

To have it start automatically at login:

```powershell
powershell -ExecutionPolicy Bypass -File scheduling\windows\install_tray.ps1
```

Registers a per-user Scheduled Task (`InfoBuddyTray`, trigger: at logon) that launches
`tray_app.py` with `pythonw.exe` (no console window). Uninstall with
`Unregister-ScheduledTask -TaskName InfoBuddyTray`.

Status is read from `.infomentor-state\sync_status.json` (gitignored, written by every
`run_pipeline.py` run — including ones triggered by the scheduled task — so the tray icon
always reflects the true last run).

### macOS menu bar app (optional)

A small menu bar item that shows when the pipeline last ran (and whether it succeeded),
plus a "Sync Now" button for a manual, one-click run — useful alongside the scheduled
launchd job above, without needing a terminal.

```bash
pip install -r requirements.txt   # installs rumps on macOS
python menubar_app.py             # try it interactively first
```

Menu items: last-sync status (relative time + ok/failed), **Sync Now** (runs
`run_pipeline.py --headless --sync-calendar --sync-timetable`, same flags as the scheduled
job — override via `"manual_sync_args"` in `.infomentor-config.json`), and shortcuts to
open the latest Study Brief, Changes report, and log file.

To have it start automatically at login:

```bash
./scheduling/macos/install_menubar.sh
```

Installs a LaunchAgent (`~/Library/LaunchAgents/com.infobuddy.menubar.plist`, label
`com.infobuddy.menubar`) separate from the pipeline's own scheduled job. Uninstall the
same way: `launchctl unload ~/Library/LaunchAgents/com.infobuddy.menubar.plist && rm
~/Library/LaunchAgents/com.infobuddy.menubar.plist`.

Status is read from `.infomentor-state/sync_status.json` (gitignored, written by every
`run_pipeline.py` run, including ones triggered outside the menu bar app — e.g. the
launchd schedule — so the menu bar always reflects the true last run).

## Login redirect troubleshooting

InfoMentor's BankID login is a redirect chain rather than a single page load:

`hub.infomentor.se -> legacy Mentor/SSO -> municipality/BankID -> Hub OAuth callback`

The extractor intentionally does **not** automate BankID. When the stored browser
profile is not authenticated, run the pipeline without `--headless` and complete
the login in the opened browser window.

Version 4.1 tolerates Playwright's benign `Navigation is interrupted by another
navigation` / `net::ERR_ABORTED` errors that can occur while InfoMentor hands the
browser from the Hub login endpoint to its SSO flow. The extractor then waits for
the final return to `hub.infomentor.se` and verifies the session before continuing.

If authentication still does not complete:

1. Close any Chromium window using `.infomentor-browser-profile`.
2. Run `python run_pipeline.py` again without `--headless`.
3. Complete BankID in the opened browser.
4. Once a successful run has stored the session, later runs may use
   `python run_pipeline.py --headless` until InfoMentor expires the session.
