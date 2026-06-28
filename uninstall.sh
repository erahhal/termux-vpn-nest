#!/data/data/com.termux/files/usr/bin/bash
# Uninstall termux-vpn-nest: remove the PATH symlink. Leaves your config
# (~/.config/termux-vpn-nest/config) and tailscaled state alone — delete
# those manually if you want a full clean.

set -e

BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
TARGET="$BIN_DIR/start-vpn"

if [ -L "$TARGET" ] || [ -f "$TARGET" ]; then
  rm -f "$TARGET"
  echo "[+] Removed $TARGET"
else
  echo "[*] Nothing to remove at $TARGET"
fi

echo "[*] Preserved:"
echo "    - ~/.config/termux-vpn-nest/config"
echo "    - ~/tailscaled.state"
echo "    Delete those manually if you want a fully clean uninstall."
