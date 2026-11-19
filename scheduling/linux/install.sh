#!/usr/bin/env bash
# Installs the InfoBuddy pipeline as a systemd --user timer (runs twice daily at 06:30
# and 18:30 -- edit infobuddy-pipeline.timer's OnCalendar lines to change the schedule
# before re-running this script).
#
# Requires a lingering user session so the timer fires even with nobody logged in --
# this script attempts to enable that for you (needs sudo once). Safe to re-run.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
UNIT_DIR="$HOME/.config/systemd/user"
SRC_DIR="$PROJECT_DIR/scheduling/linux"
# Shared with infobuddy-dashboard's feed-service install.sh if both run as this same
# user -- they'd otherwise unlock the same underlying keyrings.cryptfile store anyway.
KEYRING_SECRET_FILE="$HOME/.config/infobuddy/keyring-secret.env"

if [ ! -x "$PYTHON" ]; then
    echo "error: $PYTHON not found." >&2
    echo "Create the virtualenv and install requirements first (see README Quickstart)." >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/logs" "$UNIT_DIR"

# Only needed on a host with no reachable Secret Service provider (e.g. SSH-key-only
# access -- see keyring_linux_bootstrap.py); harmless to have even when unused. Never
# regenerated once it exists -- that would make anything already encrypted with the old
# password unreadable.
if [ ! -f "$KEYRING_SECRET_FILE" ]; then
    echo "Generating a keyring unlock secret at $KEYRING_SECRET_FILE ..."
    mkdir -p "$(dirname "$KEYRING_SECRET_FILE")"
    printf 'KEYRING_CRYPTFILE_PASSWORD=%s\n' "$(head -c 32 /dev/urandom | base64 | tr -d '\n')" > "$KEYRING_SECRET_FILE"
    chmod 600 "$KEYRING_SECRET_FILE"
else
    echo "Reusing existing keyring unlock secret at $KEYRING_SECRET_FILE"
fi

sed "s#__PYTHON__#$PYTHON#g; s#__PROJECT_DIR__#$PROJECT_DIR#g; s#__KEYRING_SECRET_FILE__#$KEYRING_SECRET_FILE#g" \
    "$SRC_DIR/infobuddy-pipeline.service.template" > "$UNIT_DIR/infobuddy-pipeline.service"
cp "$SRC_DIR/infobuddy-pipeline.timer" "$UNIT_DIR/infobuddy-pipeline.timer"

# Separate from the InfoMentor pipeline above -- the generic Google Calendar sync
# (google_calendar_read_sync.py) is an unrelated feature (CLAUDE.md principle 2: don't
# couple extraction to external integrations) with its own, more frequent schedule.
# Safe to install even with no accounts linked yet -- it's a no-op until you link one via
# calendar_link_ui.py.
sed "s#__PYTHON__#$PYTHON#g; s#__PROJECT_DIR__#$PROJECT_DIR#g; s#__KEYRING_SECRET_FILE__#$KEYRING_SECRET_FILE#g" \
    "$SRC_DIR/infobuddy-generic-calendar-sync.service.template" > "$UNIT_DIR/infobuddy-generic-calendar-sync.service"
cp "$SRC_DIR/infobuddy-generic-calendar-sync.timer" "$UNIT_DIR/infobuddy-generic-calendar-sync.timer"

# Long-running Google Calendar accounts UI (:8200). Stop any hand-started
# calendar_link_ui.py (e.g. in tmux) first or this unit can't bind the port.
sed "s#__PYTHON__#$PYTHON#g; s#__PROJECT_DIR__#$PROJECT_DIR#g; s#__KEYRING_SECRET_FILE__#$KEYRING_SECRET_FILE#g" \
    "$SRC_DIR/infobuddy-link-ui.service.template" > "$UNIT_DIR/infobuddy-link-ui.service"

systemctl --user daemon-reload
systemctl --user enable --now infobuddy-link-ui.service
systemctl --user enable --now infobuddy-pipeline.timer
systemctl --user enable --now infobuddy-generic-calendar-sync.timer

# A `systemctl --user` instance normally stops when you log out -- "linger" keeps it
# (and anything it manages, like this timer) running with nobody logged in, which is
# what an always-on box actually needs. Requires sudo once.
if ! loginctl show-user "$USER" 2>/dev/null | grep -q "Linger=yes"; then
    echo "Enabling linger for $USER (needs sudo)..."
    sudo loginctl enable-linger "$USER" || {
        echo "warning: couldn't enable linger automatically -- run this once as root:" >&2
        echo "  sudo loginctl enable-linger $USER" >&2
    }
fi

echo "Installed and enabled infobuddy-link-ui.service (http://<this host>:8200/, logs: journalctl --user -u infobuddy-link-ui)"
echo "  Set its login once: .venv/bin/python manage_link_ui_credentials.py --username <you>"
echo
echo "Installed and enabled infobuddy-pipeline.timer"
echo "  Runs at: 06:30 and 18:30 daily"
echo "  Logs:    $PROJECT_DIR/logs/pipeline.out.log / pipeline.err.log"
echo
echo "Test it right now with:   systemctl --user start infobuddy-pipeline.service"
echo "Check timer status with:  systemctl --user list-timers infobuddy-pipeline.timer"
echo "Check logs with:          journalctl --user -u infobuddy-pipeline.service"
echo "Uninstall with:           systemctl --user disable --now infobuddy-pipeline.timer && rm \"$UNIT_DIR/infobuddy-pipeline.timer\" \"$UNIT_DIR/infobuddy-pipeline.service\""
echo
echo "Installed and enabled infobuddy-generic-calendar-sync.timer"
echo "  Runs:    every 15 minutes (no-op until you link a Google account -- see README"
echo "           'Generic Google Calendar sync': run calendar_link_ui.py once to link one)"
echo "  Logs:    $PROJECT_DIR/logs/generic-calendar-sync.out.log / .err.log"
echo "Uninstall with:           systemctl --user disable --now infobuddy-generic-calendar-sync.timer && rm \"$UNIT_DIR/infobuddy-generic-calendar-sync.timer\" \"$UNIT_DIR/infobuddy-generic-calendar-sync.service\""
