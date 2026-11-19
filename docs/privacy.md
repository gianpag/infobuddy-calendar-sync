---
title: Privacy Policy
permalink: /privacy
---

# Privacy Policy

**Effective date: 19 November 2026**

This policy describes how InfoBuddy (“the software”) handles information. InfoBuddy is personal software that you install and run on your own computer. It is not a service. The author does not operate any servers for InfoBuddy, does not collect information from you, and has no access to your data.

Questions about this policy can be raised as a GitHub issue on the [gianpag/infobuddy-calendar-sync](https://github.com/gianpag/infobuddy-calendar-sync/issues) repository.

## Who this applies to

You run InfoBuddy yourself. You are the operator of your own copy. This policy explains what the software does on your machine and what it does not do.

## Your data belongs to you

All data used by InfoBuddy belongs exclusively to you. That includes InfoMentor information, calendar events, Google account information, login sessions, and any credentials or tokens. This data stays on your own computer and, when you choose to use Google Calendar, in your own Google account. The author of the software never receives it and cannot see it.

## What the software does

When you run it, InfoBuddy may:

- Sign in to InfoMentor as you, on your machine, to read school information you already have access to, and store the results locally.
- Sign in to Google, on your machine, to create, update, or delete events in your Google Calendar, or to read calendars you select, as you direct.

InfoBuddy sends data only to:

- InfoMentor, when you ask the software to sign in and extract information.
- The Google Calendar API (and the related Google account API needed to identify the signed-in account), when you ask the software to connect a Google account or sync calendars.

Nothing is sent to the author or to any other third party.

## Google access

Depending on which feature you use, InfoBuddy requests only these Google permissions (OAuth scopes):

- `https://www.googleapis.com/auth/calendar` — used by the InfoMentor-to-Calendar sync, so the software can create, update, and delete events in calendars in your Google account (for example, calendars used for school items you choose to sync).
- `https://www.googleapis.com/auth/calendar.readonly` — used by the read-only calendar sync, so the software can list and read events from calendars you select.
- `https://www.googleapis.com/auth/userinfo.email` — used with the read-only sync to read the email address of the Google account you linked, so the software can label and manage that account on your machine.

InfoBuddy's use and transfer to any other app of information received from Google APIs will adhere to the [Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy), including the Limited Use requirements.

In particular:

- Google user data is used only to provide and improve the user-facing features you run locally (calendar sync and account linking).
- It is not used for advertising, not sold, and not transferred to anyone else, except to Google as needed to call the APIs you authorized.
- The author has no access to this data. Only you, on your own machine, can see it.

## How to revoke Google access

You can disconnect a Google account in InfoBuddy's calendar accounts interface, if you use it. You should also revoke InfoBuddy's access in your Google account at [myaccount.google.com/permissions](https://myaccount.google.com/permissions). After you revoke access, the software can no longer use that Google account until you sign in again.

## How to delete local data

All local data lives on the computer where you run InfoBuddy. To remove it, stop the software, disconnect any linked Google accounts, delete stored credentials and tokens from your operating system's keychain or secret store, and delete the software's local data directories and files (including any extracted output, logs, browser profile, configuration, and OAuth client files you added). Deleting the project folder does not by itself remove keychain entries or access you granted in your Google account; revoke that access as described above.

## Changes

The author may update this policy by changing the file in the repository. The effective date at the top will be updated when that happens. Your continued use of a copy of the software is at your own choice and risk.
