# User guide (InfoBuddy)

For the dashboard itself (designer, widgets, button, rotation), see InkBuddy's
user guide.

## Google Calendar accounts UI

Open it from InkBuddy's designer header (**Google Calendar accounts ↗**), or go directly
to `http://<host>:8200/`.

1. **Sign in** with the link-UI login. Sessions last 12 h of inactivity; **Log out** is
   at the top of every page.
2. **Connect a Google account:** you get a short code. Open the shown Google URL on any
   device, sign in, and enter the code. The page refreshes by itself until linking
   finishes.
3. **Choose calendars** for that account. The ID under each name is what an InkBuddy
   calendar widget can filter on.
4. **Repeat** for more accounts. Use **Manage** to change the selection and
   **Disconnect** to remove an account and its stored token.

The read sync runs every 15 minutes and writes `out/generic-calendar.json`, which
InkBuddy's calendar widget displays.

## Study Brief

The pipeline runs twice daily and writes `out/study-brief.json` and `.md`: tests,
homework, goals and what to study. InkBuddy's *Study calendar* widget shows it, in
Swedish, English or Italian. If it stops updating, see [MAINTENANCE.md](MAINTENANCE.md)
(BankID manual login).
