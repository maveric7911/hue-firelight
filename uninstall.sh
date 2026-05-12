#!/usr/bin/env bash
# Stop + disable + remove the hue-firelight systemd user service.
# Does NOT touch your config file or the repo itself.
set -euo pipefail

UNIT_NAME="hue-firelight.service"
UNIT_DST="$HOME/.config/systemd/user/$UNIT_NAME"

systemctl --user stop    "$UNIT_NAME" 2>/dev/null || true
systemctl --user disable "$UNIT_NAME" 2>/dev/null || true
rm -f "$UNIT_DST"
systemctl --user daemon-reload

echo "uninstalled. config at ~/.config/hue-firelight/ left in place."
