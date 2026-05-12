#!/usr/bin/env bash
# Install hue-firelight as a systemd user service so the flame fires
# automatically when you log in. Re-run anytime; it's idempotent.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_NAME="hue-firelight.service"
UNIT_SRC="$REPO_DIR/systemd/$UNIT_NAME"
UNIT_DST="$HOME/.config/systemd/user/$UNIT_NAME"
CONFIG_FILE="$HOME/.config/hue-firelight/config.json"

red()   { printf '\033[31m%s\033[0m\n' "$*" >&2; }
green() { printf '\033[32m%s\033[0m\n' "$*"; }

# --- 1. config check ---
if [[ ! -f "$CONFIG_FILE" ]]; then
    red "missing config: $CONFIG_FILE"
    red "Copy config.example.json there and fill it in. See README.md Setup section."
    exit 1
fi

# --- 2. unit file install ---
if [[ ! -f "$UNIT_SRC" ]]; then
    red "unit file not found: $UNIT_SRC"
    exit 1
fi

mkdir -p "$(dirname "$UNIT_DST")"

# Rewrite ExecStart to point at this checkout (handles non-standard clone paths).
# Use a different sed delimiter since $REPO_DIR contains /.
sed "s|^ExecStart=.*|ExecStart=$REPO_DIR/hue-flame-stream.py|" \
    "$UNIT_SRC" > "$UNIT_DST"

# --- 3. reload + enable + start ---
systemctl --user daemon-reload
systemctl --user enable --now "$UNIT_NAME"

sleep 2
if systemctl --user is-active --quiet "$UNIT_NAME"; then
    green "✓ hue-firelight is running"
    systemctl --user --no-pager --lines=0 status "$UNIT_NAME" | head -3
    echo
    echo "Follow logs:  journalctl --user -u $UNIT_NAME -f"
    echo "Stop:         systemctl --user stop $UNIT_NAME"
    echo "Uninstall:    $REPO_DIR/uninstall.sh"
else
    red "service failed to start; check: journalctl --user -u $UNIT_NAME --no-pager | tail -30"
    exit 1
fi
