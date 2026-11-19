#!/usr/bin/env bash
# Installs the InfoBuddy pipeline as a macOS LaunchAgent (per-user scheduled job).
# Runs twice daily at 06:30 and 18:30 -- edit com.infobuddy.pipeline.plist.template
# to change the schedule before re-running this script.
#
# Safe to re-run: it replaces any existing job with the same label.
#
# Note: LaunchAgents only run while you're logged in, and only fire on schedule if the
# Mac is awake at that time -- if it's asleep, launchd runs the job as soon as it wakes
# instead. Use `sudo pmset repeat wakeorpoweron MTWRFSU 06:25:00` if you want the Mac to
# wake up in time (not configured by this script).
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LABEL="com.infobuddy.pipeline"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$PROJECT_DIR/scheduling/macos/com.infobuddy.pipeline.plist.template"

if [ ! -x "$PYTHON" ]; then
    echo "error: $PYTHON not found." >&2
    echo "Create the virtualenv and install requirements first (see README Quickstart)." >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$HOME/Library/LaunchAgents"

sed -e "s#__PYTHON__#$PYTHON#g" -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" "$TEMPLATE" > "$PLIST_DEST"

launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"

echo "Installed and loaded $LABEL"
echo "  Plist:   $PLIST_DEST"
echo "  Runs at: 06:30 and 18:30 daily"
echo "  Logs:    $PROJECT_DIR/logs/launchd.out.log / launchd.err.log"
echo
echo "Test it right now with:   launchctl start $LABEL"
echo "Check it's loaded with:   launchctl list | grep $LABEL"
echo "Uninstall with:           launchctl unload \"$PLIST_DEST\" && rm \"$PLIST_DEST\""
