# Installation (InfoBuddy)

For the whole system (hardware, firmware, feed-service), see the companion InkBuddy project.

```sh
git clone https://github.com/gianpag/infobuddy-calendar-sync && cd infobuddy-calendar-sync
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/playwright install --with-deps chromium
```

1. **InfoMentor login:** BankID by default; see the README's "Authentication" section.
   Accounts without BankID can store a login with
   `.venv/bin/python manage_credentials.py set --username <user>`.
2. **Google push sync (optional):** see the README's "Google Calendar sync" section.
3. **Generic calendar read sync:**
   1. Create an OAuth client of type *TVs and Limited Input devices* and save it as
      `client_secret_device.json`.
   2. Link accounts via the accounts UI.
4. **Accounts UI login:**

   ```sh
   .venv/bin/python manage_link_ui_credentials.py --username <you>                  # prompts
   .venv/bin/python manage_link_ui_credentials.py --username <you> --password-stdin <<<"$PW"
   ```

   The login is stored in the keyring under `infobuddy-link-ui`. Until it's set, the UI
   is open and prints a warning at startup.
5. **Headless keyring:** `scheduling/linux/install.sh` creates
   `~/.config/infobuddy/keyring-secret.env` (mode 600) once. Never regenerate it.
   Before running any `manage_*` script in a shell, load the same value:

   ```sh
   export KEYRING_CRYPTFILE_PASSWORD="$(sed -n 's/^KEYRING_CRYPTFILE_PASSWORD=//p' ~/.config/infobuddy/keyring-secret.env)"
   ```

6. **Autostart:**

   ```sh
   scheduling/linux/install.sh
   ```

   This installs and enables the pipeline timer, the calendar-sync timer and
   `infobuddy-link-ui.service`, and enables linger (via sudo) so they run at boot without
   anyone logging in. Stop any hand-started `calendar_link_ui.py` first. macOS and
   Windows schedulers are covered in the README's "Running automatically" section.
