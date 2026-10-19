#!/bin/bash
# omarchy-magic-mouse uninstaller.
# Usage: ./uninstall.sh [--purge] [--no-system] [--dry-run]
#
# The config is user data: plain uninstall keeps it, --purge MOVES it aside to
# a timestamped .bak next to it. Nothing is ever deleted from ~/.config.
set -euo pipefail

PURGE=0
SYSTEM=1
DRY_RUN=0
for arg in "$@"; do
  case "$arg" in
    --purge) PURGE=1 ;;
    --no-system) SYSTEM=0 ;;
    --dry-run) DRY_RUN=1 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

if [[ -z ${HOME:-} ]]; then
  echo "refusing to uninstall: HOME is unset" >&2
  exit 2
fi

run() {
  if (( DRY_RUN )); then
    echo "dry-run: $*"
  else
    "$@"
  fi
}

UNIT_NAME="omarchy-magic-mouse.service"

if command -v systemctl >/dev/null; then
  run systemctl --user disable --now "$UNIT_NAME" 2>/dev/null || true
  run rm -f "$HOME/.config/systemd/user/$UNIT_NAME"
  run systemctl --user daemon-reload 2>/dev/null || true
fi

run rm -f "$HOME/.local/bin/omarchy-magic-mouse"
run rm -rf "$HOME/.local/share/omarchy-magic-mouse"

if [[ $PURGE == 1 ]]; then
  CONFIG="$HOME/.config/omarchy/magic-mouse.json"
  if [[ -e $CONFIG ]]; then
    BACKUP="$CONFIG.bak-$(date +%Y%m%d%H%M%S)"
    run mv "$CONFIG" "$BACKUP"
    [[ $DRY_RUN == 1 ]] || echo "Config moved aside to $BACKUP"
  fi
  if [[ $SYSTEM == 1 ]] && { (( DRY_RUN )) || { command -v sudo >/dev/null && sudo -v; }; }; then
    run sudo rm -f /etc/udev/rules.d/99-omarchy-magic-mouse.rules
    run sudo rm -f /etc/modules-load.d/omarchy-magic-mouse.conf
    run sudo udevadm control --reload-rules
    [[ $DRY_RUN == 1 ]] || echo "System files removed. ('input' group membership left alone.)"
  fi
else
  echo "Config kept at ~/.config/omarchy/magic-mouse.json (--purge moves it aside)."
fi

echo "Uninstalled. Log out and back in to drop the 'input' group permissions."
