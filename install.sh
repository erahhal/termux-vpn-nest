#!/data/data/com.termux/files/usr/bin/bash
# Install termux-vpn-nest:
#   - ensures the h2 Python dependency is present
#   - symlinks `start-vpn` into Termux's $PREFIX/bin so it's on PATH
#
# Run from a normal Termux shell (NOT su): `./install.sh`

set -e

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
TARGET="$BIN_DIR/start-vpn"

if [ "$EUID" -eq 0 ]; then
  echo "[-] Run this as your normal Termux user, not root." >&2
  exit 1
fi

echo "[*] Installing h2 (Python dependency for the Mullvad gRPC client)..."
pip install --user h2

echo "[*] Linking $TARGET -> $SCRIPT_DIR/start-vpn"
mkdir -p "$BIN_DIR"
ln -sfn "$SCRIPT_DIR/start-vpn" "$TARGET"

echo
echo "[+] Done. Run \`start-vpn\` from anywhere."
echo "    On first run you'll be asked for your Headscale URL; it gets saved to"
echo "    ~/.config/termux-vpn-nest/config and reused on subsequent runs."
