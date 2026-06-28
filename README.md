# termux-vpn-nest

Chain a Termux-launched [Tailscale](https://tailscale.com/) / [Headscale](https://github.com/juanfont/headscale) client through the Mullvad Android app, with device-wide MagicDNS that survives DoH-defaulting browsers.

The setup it solves:

- You're on Android in a hostile network environment.
- The outer hop is the official Mullvad app (registered as Android's `VpnService`) so its WireGuard tunnel pierces the firewall.
- The inner hop is a userspace `tailscaled` running inside Termux, registered against a self-hosted Headscale server, that lets you reach your home tailnet's subnet routes.
- You want every app on the device — including Chrome/Brave/Firefox with their default DoH — to resolve internal names (e.g. `admin.homeassistant.lan` → `10.0.0.x`) without per-browser config.

Doing this naively breaks in three places, all of which the script handles:

1. `tailscaled`'s outbound sockets carry the `fwmark 0x80000` anti-loop tag, which Android sends to a `main` routing table that has no default route — every dial fails with `network is unreachable`. Fix: install a default route into `main` that exits via Mullvad's tun.
2. Android ships without `/etc/resolv.conf`. tailscaled's Go resolver falls back to `[::1]:53` and times out. Fix: remount `/` rw and seed a `nameserver 1.1.1.1` line so tailscaled can resolve Headscale's hostname during bootstrap; tailscaled rewrites it to MagicDNS once connected.
3. Mullvad announces its tunnel DNS to every app via `VpnService.Builder.addDnsServer()`. We want that to be `100.100.100.100` (MagicDNS) instead of Mullvad's resolver. The script talks to Mullvad's management gRPC socket directly to flip `DnsOptions` live — no daemon restart, no force-stop, just a few-second WireGuard re-handshake.

## Requirements

- Rooted Android, with Magisk granting `su` to Termux.
- Termux with `bash`, `python` (3.11+), `pip`, `iptables`, `curl`.
- The official Mullvad VPN Android app, signed in and connected.
- `tailscale` and `tailscaled` binaries placed at `~/tailscale` and `~/tailscaled` (download from <https://pkgs.tailscale.com/stable/#static>).
- An auth/pre-auth key from your Headscale UI for first-time registration.

## Install

```sh
git clone https://github.com/erahhal/termux-vpn-nest.git
cd termux-vpn-nest
./install.sh
```

`install.sh` does two things: `pip install --user h2` (the only Python
dependency — pure Python, fast), and symlinks `start-vpn` into
`$PREFIX/bin` so it's on PATH. Run `./uninstall.sh` to remove the symlink.

## First run

```sh
start-vpn
```

You'll get prompted for two things on the first run:

1. **Headscale URL** (e.g. `https://vpn.example.com`). Saved to `~/.config/termux-vpn-nest/config` so you only enter it once.
2. **Pre-auth key** from your Headscale admin UI. Used once for registration; tailscaled saves the session in `~/tailscaled.state` for subsequent runs.

Hit `Ctrl+C` to tear down. The script restores Mullvad's original DNS settings (another brief re-handshake), kills tailscaled, removes the custom route, and remounts `/` read-only.

## Subsequent runs

Just `start-vpn` — it'll reuse the saved session and the saved Headscale URL. If you want to force re-registration, generate a new key and pass it:

```sh
start-vpn nodekey:abcdef...
# or
AUTH_KEY=nodekey:abcdef... start-vpn
```

## What's in the repo

- `start-vpn` — the orchestrator. Run as a normal user; it re-execs under `su` automatically.
- `mullvad_dns.py` — minimal gRPC client for Mullvad's management socket. Hand-rolled protobuf + the `h2` library to avoid pulling in `grpcio`. Supports `get`, `save <path>`, `restore <path>`, `custom <ip> [<ip>...]`, `default`.

## Caveats

- Setting Mullvad's DNS via the gRPC API triggers a WireGuard re-handshake (~3–5s of network outage). The script polls until ping recovers before continuing.
- If the script dies between `mullvad_dns_set` and `mullvad_dns_restore` (kill -9, phone reboot), Mullvad will be left with `100.100.100.100` as its DNS. You can either re-run the script and Ctrl+C cleanly, or open the Mullvad app and change DNS back to default manually.
- The script remounts `/` rw briefly so tailscaled can write `/etc/resolv.conf`. It puts it back ro on Ctrl+C.
- Mullvad's proto layout was reverse-engineered against [their open-source `management_interface.proto`](https://github.com/mullvad/mullvadvpn-app/blob/main/mullvad-management-interface/proto/management_interface.proto). If Mullvad changes the wire format, `mullvad_dns.py` may need updating.

## License

MIT.
