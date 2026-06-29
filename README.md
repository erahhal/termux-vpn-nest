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
- `install.sh` downloads the `tailscale` and `tailscaled` static binaries into `~/bin` for you (matching your CPU arch). No manual download needed; pin a version with `TAILSCALE_VERSION=1.80.0 ./install.sh` if you want.
- An auth/pre-auth key from your Headscale UI for first-time registration.

## Install

```sh
git clone https://github.com/erahhal/termux-vpn-nest.git
cd termux-vpn-nest
./install.sh
```

`install.sh` is idempotent and does everything except ask for your VPN URL /
key: `pip install --user h2` (the only Python dependency — pure Python, fast),
creates `~/bin` / `~/logs` / `~/state`, downloads the `tailscale` +
`tailscaled` static binaries for your CPU arch into `~/bin`, and symlinks
`start-vpn` into `$PREFIX/bin` so it's on PATH. Re-running it only re-downloads
the binaries when they're missing (or when you set `TAILSCALE_VERSION`). Run
`./uninstall.sh` to remove the symlink.

If the device's DNS is blocked (the hostile-network case), the installer's
downloads automatically fall back to DNS-over-HTTPS via `1.1.1.1`, so it still
works before the VPN is up.

## First run

```sh
start-vpn
```

You'll get prompted for two things on the first run:

1. **Headscale URL** (e.g. `https://vpn.example.com`). Saved to `~/.config/termux-vpn-nest/config` so you only enter it once.
2. **Pre-auth key** from your Headscale admin UI. Used once for registration; tailscaled saves the session in `~/state/tailscaled.state` for subsequent runs. The Mullvad DNS swap happens *after* this step, so your normal internet still works while you open the admin UI to generate the key.

Hit `Ctrl+C` to tear down. The script restores Mullvad's original DNS settings (another brief re-handshake), kills tailscaled, removes the custom route, resets `/etc/resolv.conf`, and remounts `/` read-only. The same teardown also runs if the script exits any other way — an error mid-startup, or Termux being closed (`SIGHUP`) — so it shouldn't leave the network wedged.

## Recovering a wedged network

**The persistent-DNS trap.** Mullvad stores its custom-DNS setting permanently — it survives reboots and app restarts. If a run is killed hard enough that teardown never ran (`kill -9`, Android killing Termux from the background), Mullvad is left advertising `100.100.100.100` (MagicDNS) as the device DNS. That server only answers while `tailscaled` is running, so once it's gone **the whole device has no working DNS the moment Mullvad connects — even after a reboot and even if you never run `start-vpn` again.** The symptom is exactly that: connect Mullvad, network looks dead.

Two ways to fix it:

- **Fastest, no root/script:** open the Mullvad app → Settings → DNS → turn **off** "Use custom DNS server". Done.
- **From Termux** (with Mullvad running so its management socket is up):

  ```sh
  start-vpn recover
  ```

  It starts nothing; it just undoes leftover state — restores Mullvad's DNS from the snapshot in `/data/local/tmp`, or, if that snapshot was wiped (a reboot can clear `/data/local/tmp`), forces Mullvad's DNS back to its default. It also clears the stale route, resets `/etc/resolv.conf`, and remounts `/` read-only.

## Subsequent runs

Just `start-vpn` — it'll reuse the saved session and the saved Headscale URL. If you want to force re-registration, generate a new key and pass it:

```sh
start-vpn nodekey:abcdef...
# or
AUTH_KEY=nodekey:abcdef... start-vpn
```

### It keeps asking for a pre-auth key every run

The session should persist in `~/state` so subsequent runs reconnect without a key. If you're re-prompted every time:

- **State must be a directory, not just a file.** `start-vpn` launches `tailscaled` with `--statedir=~/state`. With only `--state=<file>` (an earlier bug), tailscaled wrote the login profile to a non-persistent derived path and lost it on every restart — leaving just a tiny machine-key-only state file. If you upgraded from that version, the first run re-registers once and then sticks.
- **Don't use an *ephemeral* pre-auth key.** Headscale deletes ephemeral nodes the moment they disconnect, so you'd have to re-register every run no matter what. Generate a **reusable, non-ephemeral** key, and check the node's key-expiry isn't set too short.

## What's in the repo

- `start-vpn` — the orchestrator. Run as a normal user; it re-execs under `su` automatically.
- `mullvad_dns.py` — minimal gRPC client for Mullvad's management socket. Hand-rolled protobuf + the `h2` library to avoid pulling in `grpcio`. Supports `get`, `save <path>`, `restore <path>`, `custom <ip> [<ip>...]`, `default`.

## Caveats

- Setting Mullvad's DNS via the gRPC API triggers a WireGuard re-handshake (~3–5s of network outage). The script polls until ping recovers before continuing.
- Teardown runs on `Ctrl+C`, on termination/hangup signals, and on any other exit, so normal exits and crashes both clean up. Only an uncatchable `kill -9` (or a hard reboot) can still skip it — in that case run `start-vpn recover` (see above) to unwedge without rebooting.
- The script remounts `/` rw briefly so tailscaled can write `/etc/resolv.conf`. It puts it back ro on teardown.
- Mullvad's proto layout was reverse-engineered against [their open-source `management_interface.proto`](https://github.com/mullvad/mullvadvpn-app/blob/main/mullvad-management-interface/proto/management_interface.proto). If Mullvad changes the wire format, `mullvad_dns.py` may need updating.

## License

MIT.
