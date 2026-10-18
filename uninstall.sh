#!/bin/bash
# omarchy-magic-mouse uninstaller. Usage: ./uninstall.sh [--purge]
# --purge also deletes ~/.config/omarchy/magic-mouse.json and system files.
set -euo pipefail

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

UNIT_NAME="omarchy-magic-mouse.service"

if command -v systemctl >/dev/null; then
  systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
  rm -f "$HOME/.config/systemd/user/$UNIT_NAME"
  systemctl --user daemon-reload
fi

rm -f "$HOME/.local/bin/omarchy-magic-mouse"
rm -rf "$HOME/.local/share/omarchy-magic-mouse"

if [[ $PURGE == 1 ]]; then
  rm -f "$HOME/.config/omarchy/magic-mouse.json"
  if command -v sudo >/dev/null && sudo -v; then
    sudo rm -f /etc/udev/rules.d/99-omarchy-magic-mouse.rules
    sudo rm -f /etc/modules-load.d/omarchy-magic-mouse.conf
    sudo udevadm control --reload-rules
    echo "System files removed. (Your 'input' group membership was left alone.)"
  fi
  echo "Config removed."
else
  echo "Config kept at ~/.config/omarchy/magic-mouse.json (use --purge to remove)."
fi

echo "Uninstalled. Log out and back in to drop the 'input' group permissions."
