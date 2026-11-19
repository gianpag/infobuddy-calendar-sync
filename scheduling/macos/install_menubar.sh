#!/usr/bin/env bash
# Installs the InfoBuddy menu bar app as a macOS LaunchAgent that starts at login.
# Shows the last sync status in the menu bar and offers a "Sync Now" button.
#
# Safe to re-run: it replaces any existing job with the same label.
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
PYTHON="$PROJECT_DIR/.venv/bin/python"
LABEL="com.infobuddy.menubar"
PLIST_DEST="$HOME/Library/LaunchAgents/$LABEL.plist"
TEMPLATE="$PROJECT_DIR/scheduling/macos/com.infobuddy.menubar.plist.template"

if [ ! -x "$PYTHON" ]; then
    echo "error: $PYTHON not found." >&2
    echo "Create the virtualenv and install requirements first (see README Quickstart)." >&2
    exit 1
fi

if ! "$PYTHON" -c "import rumps" 2>/dev/null; then
    echo "error: the 'rumps' package isn't installed in $PROJECT_DIR/.venv" >&2
    echo "Run: $PYTHON -m pip install -r requirements.txt" >&2
    exit 1
fi

mkdir -p "$PROJECT_DIR/logs"
mkdir -p "$HOME/Library/LaunchAgents"

sed -e "s#__PYTHON__#$PYTHON#g" -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" "$TEMPLATE" > "$PLIST_DEST"

launchctl unload "$PLIST_DEST" 2>/dev/null || true
launchctl load -w "$PLIST_DEST"

echo "Installed and loaded $LABEL"
echo "  Plist:   $PLIST_DEST"
echo "  Starts:  automatically at login (and now)"
echo "  Logs:    $PROJECT_DIR/logs/menubar.out.log / menubar.err.log"
echo
echo "Uninstall with:  launchctl unload \"$PLIST_DEST\" && rm \"$PLIST_DEST\""
