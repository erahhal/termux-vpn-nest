#!/data/data/com.termux/files/usr/bin/bash
# Install termux-vpn-nest:
#   - ensures the h2 Python dependency is present
#   - creates ~/bin (tailscale binaries), ~/logs (runtime logs), ~/state (session state)
#   - downloads the static tailscale + tailscaled binaries into ~/bin
#   - symlinks `start-vpn` into Termux's $PREFIX/bin so it's on PATH
#
# Idempotent: safe to re-run. It only re-downloads the tailscale binaries
# when they're missing or when you ask for a different version via
# TAILSCALE_VERSION (e.g. `TAILSCALE_VERSION=1.80.0 ./install.sh`).
#
# Run from a normal Termux shell (NOT su): `./install.sh`

set -e

SCRIPT_DIR="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
PREFIX_BIN_DIR="${PREFIX:-/data/data/com.termux/files/usr}/bin"
TARGET="$PREFIX_BIN_DIR/start-vpn"

# Where start-vpn looks for the tailscale binaries and writes its logs/state.
HOME_BIN_DIR="${HOME}/bin"
HOME_LOG_DIR="${HOME}/logs"
HOME_STATE_DIR="${HOME}/state"

# Pin a specific Tailscale release with TAILSCALE_VERSION=1.80.0, or leave
# unset to grab the current stable.
TAILSCALE_VERSION="${TAILSCALE_VERSION:-}"

if [ "$EUID" -eq 0 ]; then
  echo "[-] Run this as your normal Termux user, not root." >&2
  exit 1
fi

# Public DNS-over-HTTPS resolver (an IP, so it needs no name resolution).
# Used as a fallback when the device's system resolver is blocked/broken —
# common on the hostile networks this whole project exists to punch through.
DOH_URL="https://1.1.1.1/dns-query"

# curl that survives a broken system resolver: try the normal resolver first,
# then retry the exact same request resolving names via DoH instead.
robust_curl() {
  curl "$@" 2>/dev/null && return 0
  echo "[*] System DNS failed; retrying via DNS-over-HTTPS ($DOH_URL)..." >&2
  curl --doh-url "$DOH_URL" "$@"
}

# Map `uname -m` onto Tailscale's static-build arch names.
tailscale_arch() {
  case "$(uname -m)" in
    aarch64|arm64)        echo "arm64" ;;
    armv7l|armv7|armv6l|arm) echo "arm" ;;
    x86_64|amd64)         echo "amd64" ;;
    i686|i386)            echo "386" ;;
    *) echo "" ;;
  esac
}

# Download + extract the static tailscale/tailscaled into ~/bin.
install_tailscale_binaries() {
  local arch ver url tarball tmp extract_dir
  arch="$(tailscale_arch)"
  if [ -z "$arch" ]; then
    echo "[-] Unsupported CPU arch '$(uname -m)'. Download tailscale/tailscaled" >&2
    echo "    manually from https://pkgs.tailscale.com/stable/#static into $HOME_BIN_DIR." >&2
    return 1
  fi

  ver="$TAILSCALE_VERSION"
  if [ -z "$ver" ]; then
    echo "[*] Resolving latest stable Tailscale version..." >&2
    ver="$(robust_curl -fsSL 'https://pkgs.tailscale.com/stable/?mode=json' \
      | grep -o '"Version"[[:space:]]*:[[:space:]]*"[^"]*"' \
      | head -1 | grep -o '[0-9][0-9.]*')"
  fi
  if [ -z "$ver" ]; then
    echo "[-] Couldn't determine the Tailscale version. Set TAILSCALE_VERSION=x.y.z and retry." >&2
    return 1
  fi

  tarball="tailscale_${ver}_${arch}.tgz"
  url="https://pkgs.tailscale.com/stable/${tarball}"
  tmp="$(mktemp -d)"
  trap 'rm -rf "$tmp"' RETURN

  echo "[*] Downloading $tarball ..."
  if ! robust_curl -fSL --progress-bar "$url" -o "$tmp/$tarball"; then
    echo "[-] Download failed: $url" >&2
    return 1
  fi

  echo "[*] Extracting tailscale + tailscaled into $HOME_BIN_DIR ..."
  tar -xzf "$tmp/$tarball" -C "$tmp"
  extract_dir="$tmp/tailscale_${ver}_${arch}"
  install -m 0755 "$extract_dir/tailscale"  "$HOME_BIN_DIR/tailscale"
  install -m 0755 "$extract_dir/tailscaled" "$HOME_BIN_DIR/tailscaled"
  echo "[+] Installed tailscale $ver ($arch)."
}

echo "[*] Installing h2 (Python dependency for the Mullvad gRPC client)..."
pip install --user h2

echo "[*] Creating $HOME_BIN_DIR, $HOME_LOG_DIR and $HOME_STATE_DIR..."
mkdir -p "$HOME_BIN_DIR" "$HOME_LOG_DIR" "$HOME_STATE_DIR"

# Only fetch the binaries when they're missing, unless a specific version was
# requested (in which case always (re)install it) — keeps re-runs cheap.
if [ -n "$TAILSCALE_VERSION" ] \
   || [ ! -x "$HOME_BIN_DIR/tailscale" ] || [ ! -x "$HOME_BIN_DIR/tailscaled" ]; then
  install_tailscale_binaries
else
  echo "[*] tailscale binaries already present in $HOME_BIN_DIR; skipping download."
  echo "    (set TAILSCALE_VERSION=x.y.z to force a specific version.)"
fi

echo "[*] Linking $TARGET -> $SCRIPT_DIR/start-vpn"
mkdir -p "$PREFIX_BIN_DIR"
ln -sfn "$SCRIPT_DIR/start-vpn" "$TARGET"

echo
echo "[+] Done. Run \`start-vpn\` from anywhere."
echo "    On first run you'll be asked for your Headscale URL; it gets saved to"
echo "    ~/.config/termux-vpn-nest/config and reused on subsequent runs."
