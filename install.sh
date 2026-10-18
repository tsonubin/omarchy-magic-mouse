#!/bin/bash
# omarchy-magic-mouse installer: user-level install, optional system setup.
# Usage: ./install.sh [--no-enable] [--no-system]
set -euo pipefail

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TARGET_DIR="$HOME/.local/share/omarchy-magic-mouse"
BIN_LINK="$HOME/.local/bin/omarchy-magic-mouse"
UNIT_DIR="$HOME/.config/systemd/user"
UNIT_NAME="omarchy-magic-mouse.service"

ENABLE=1
SYSTEM=1
for arg in "$@"; do
  case "$arg" in
    --no-enable) ENABLE=0 ;;
    --no-system) SYSTEM=0 ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

echo "Installing to $TARGET_DIR"
mkdir -p "$TARGET_DIR"
rm -rf "$TARGET_DIR/magicmouse" "$TARGET_DIR/bin"
cp -r "$SOURCE_DIR/magicmouse" "$SOURCE_DIR/bin" "$TARGET_DIR/"
chmod +x "$TARGET_DIR/bin/omarchy-magic-mouse"

mkdir -p "$HOME/.local/bin"
ln -sfn "$TARGET_DIR/bin/omarchy-magic-mouse" "$BIN_LINK"

"$BIN_LINK" config --init

mkdir -p "$UNIT_DIR"
cp "$SOURCE_DIR/systemd/omarchy-magic-mouse.service" "$UNIT_DIR/"
if command -v systemctl >/dev/null; then
  if systemctl --user daemon-reload; then
    if [[ $ENABLE == 1 ]]; then
      if systemctl --user enable --now "$UNIT_NAME"; then
        echo "Service enabled and started."
      else
        echo "WARNING: could not enable $UNIT_NAME — run: systemctl --user enable --now $UNIT_NAME" >&2
      fi
    fi
  else
    echo "WARNING: systemctl --user unavailable." >&2
    echo "         Enable later with: systemctl --user enable --now $UNIT_NAME" >&2
  fi
fi

if [[ $SYSTEM == 1 ]]; then
  if command -v sudo >/dev/null && sudo -v; then
    sudo install -Dm644 "$SOURCE_DIR/udev/99-omarchy-magic-mouse.rules" \
      /etc/udev/rules.d/99-omarchy-magic-mouse.rules
    sudo install -Dm644 "$SOURCE_DIR/system/modules-load.d/omarchy-magic-mouse.conf" \
      /etc/modules-load.d/omarchy-magic-mouse.conf
    sudo usermod -aG input "$USER"
    sudo udevadm control --reload-rules
    sudo modprobe uinput 2>/dev/null || true
    echo "System setup done. Log out and back in for the 'input' group to apply."
  else
    echo "WARNING: sudo unavailable — skipped udev rule, uinput module and 'input' group." >&2
    echo "         The daemon cannot grab devices until those are set up." >&2
  fi
fi

echo
echo "Next:"
echo "  omarchy-magic-mouse doctor   # verify permissions and integration"
echo "  omarchy-magic-mouse devices  # see which devices are managed"
echo "  omarchy-magic-mouse config   # tune gestures, scrolling, actions"
