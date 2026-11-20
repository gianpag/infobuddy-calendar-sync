# Maintenance (InfoBuddy)

Manage the Linux units with `systemctl --user` and `journalctl --user`:

```sh
systemctl --user status infobuddy-pipeline.timer infobuddy-generic-calendar-sync.timer infobuddy-link-ui
systemctl --user list-timers 'infobuddy-*'
systemctl --user start infobuddy-pipeline.service
systemctl --user start infobuddy-generic-calendar-sync.service
systemctl --user restart infobuddy-link-ui
journalctl --user -u infobuddy-pipeline.service
journalctl --user -u infobuddy-generic-calendar-sync.service
journalctl --user -u infobuddy-link-ui -f
```

## Updating

```sh
git pull --ff-only && .venv/bin/python -m pytest -q
systemctl --user restart infobuddy-link-ui      # timers use the new code on their next run
scheduling/linux/install.sh                      # only if a unit template changed (idempotent)
```

## Pipeline status

`.infomentor-state/sync_status.json` records the last run: `status`, `finished_at`,
`error`, `needs_relink` and `needs_manual_login`.

- **`needs_manual_login`**: the InfoMentor session expired. Run `run_pipeline.py`
  without `--headless` in a desktop session and complete BankID. The persistent browser
  profile is then reused by the timer.
- **`needs_relink`**: the Google push token is missing or expired. Re-link it with the
  device flow:

  ```sh
  .venv/bin/python google_calendar_sync.py --link-device --account default
  ```

## Generic calendar accounts

Re-link or replace an account in the accounts UI (`http://<host>:8200/`): **Disconnect**
it, then **Connect a Google account** again. Tokens stop working after a password change
or revoked access, and after 7 days if the OAuth app is still in "Testing".

## Backups

Back up these together, and keep them private:

- `.infomentor-state/`
- `.google-calendar-accounts.json`
- `client_secret*.json`
- `~/.config/infobuddy/keyring-secret.env`
- `~/.local/share/python_keyring/cryptfile_pass.cfg`

`out/` can be regenerated.

## Troubleshooting

| Symptom | Fix |
|---|---|
| Accounts UI unreachable | `systemctl --user status infobuddy-link-ui`: check that port 8200 is listening, then `journalctl --user -u infobuddy-link-ui`. |
| Locked out of the accounts UI | 5 failures within 15 min lock that IP out for 15 min. Wait, or `systemctl --user restart infobuddy-link-ui`. |
| Read sync not updating | `systemctl --user status infobuddy-generic-calendar-sync.timer`, then `journalctl --user -u infobuddy-generic-calendar-sync.service`. Re-link the account if its token expired. |
| Playwright fails on Linux | `.venv/bin/playwright install --with-deps chromium` |
| Keyring errors in a service | Check that `EnvironmentFile=` points at `keyring-secret.env` (`systemctl --user cat <unit>`). |
