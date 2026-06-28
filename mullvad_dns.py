#!/usr/bin/env python3
"""
Minimal gRPC client for Mullvad's management socket.

Calls SetDnsOptions to switch Mullvad's DNS between default and custom
without restarting the daemon. Uses h2 + hand-rolled protobuf so we
don't need grpcio (which is slow/painful to install on Termux).

Usage:
  python3 mullvad_dns.py custom 100.100.100.100
  python3 mullvad_dns.py default
  python3 mullvad_dns.py get          # prints current DnsOptions
"""

import socket
import sys
import h2.connection
import h2.config
import h2.events

SOCKET_PATH = "/data/data/net.mullvad.mullvadvpn/no_backup/rpc-socket"
SERVICE = "mullvad_daemon.management_interface.ManagementService"


def encode_varint(value):
    out = bytearray()
    while True:
        b = value & 0x7F
        value >>= 7
        if value:
            out.append(b | 0x80)
        else:
            out.append(b)
            return bytes(out)


def encode_tag(field_number, wire_type):
    return encode_varint((field_number << 3) | wire_type)


def encode_length_delimited(field_number, data):
    return encode_tag(field_number, 2) + encode_varint(len(data)) + data


def encode_dns_options(state, addresses=None):
    """
    state: 0=DEFAULT, 1=CUSTOM
    addresses: list of strings (only meaningful when state=CUSTOM)

    Mullvad's server requires `default_options` and `custom_options` to be
    explicitly present (it rejects messages where they're absent even
    though proto3 normally treats absent == default), so we always emit
    both, even when empty.
    """
    out = bytearray()
    if state:
        out += encode_tag(1, 0) + encode_varint(state)
    # Always emit default_options as an empty submessage
    out += encode_length_delimited(2, b"")
    # Always emit custom_options; populate addresses if given
    custom_body = bytearray()
    for addr in addresses or []:
        custom_body += encode_length_delimited(1, addr.encode())
    out += encode_length_delimited(3, bytes(custom_body))
    return bytes(out)


def decode_varint(data, off):
    result = 0
    shift = 0
    while True:
        b = data[off]
        off += 1
        result |= (b & 0x7F) << shift
        if not (b & 0x80):
            return result, off
        shift += 7


def find_submessage(data, target_field):
    """Walk a protobuf payload and return the bytes of the first submessage
    with the given field number (wire type 2). Returns None if not found."""
    off = 0
    while off < len(data):
        tag, off = decode_varint(data, off)
        field = tag >> 3
        wire = tag & 7
        if wire == 0:  # varint
            _, off = decode_varint(data, off)
        elif wire == 1:  # 64-bit
            off += 8
        elif wire == 2:  # length-delimited
            length, off = decode_varint(data, off)
            if field == target_field:
                return data[off : off + length]
            off += length
        elif wire == 5:  # 32-bit
            off += 4
        else:
            raise ValueError(f"unsupported wire type {wire}")
    return None


def extract_dns_options(settings_payload):
    """Pull the DnsOptions sub-blob out of a full Settings payload.
    Settings.tunnel_options = field 6, TunnelOptions.dns_options = field 6."""
    tunnel_opts = find_submessage(settings_payload, 6)
    if tunnel_opts is None:
        return b""
    dns_opts = find_submessage(tunnel_opts, 6)
    return dns_opts if dns_opts is not None else b""


def grpc_frame(payload):
    # 1 byte compressed flag (0) + 4 byte big-endian length + payload
    return b"\x00" + len(payload).to_bytes(4, "big") + payload


DEBUG = False


def _dbg(*a):
    if DEBUG:
        print("[dbg]", *a, file=sys.stderr)


def call(method, request_body, want_response=False):
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.connect(SOCKET_PATH)

    config = h2.config.H2Configuration(client_side=True, header_encoding="utf-8")
    conn = h2.connection.H2Connection(config=config)
    conn.initiate_connection()
    sock.sendall(conn.data_to_send())

    stream_id = conn.get_next_available_stream_id()
    _dbg("sending headers, path=", f"/{SERVICE}/{method}", "body=", request_body.hex())
    conn.send_headers(
        stream_id,
        [
            (":method", "POST"),
            (":scheme", "http"),
            (":path", f"/{SERVICE}/{method}"),
            (":authority", "localhost"),
            ("content-type", "application/grpc+proto"),
            ("te", "trailers"),
            ("user-agent", "mullvad-dns-min/0.1"),
        ],
    )
    sock.sendall(conn.data_to_send())

    framed = grpc_frame(request_body)
    _dbg("sending body, framed=", framed.hex())
    conn.send_data(stream_id, framed, end_stream=True)
    sock.sendall(conn.data_to_send())

    response_data = bytearray()
    response_headers = []
    trailers = {}
    rst_code = None
    sock.settimeout(10)
    while True:
        try:
            chunk = sock.recv(65535)
        except socket.timeout:
            break
        if not chunk:
            break
        events = conn.receive_data(chunk)
        for ev in events:
            _dbg("event:", type(ev).__name__, repr(ev))
            if isinstance(ev, h2.events.ResponseReceived):
                response_headers = list(ev.headers)
            elif isinstance(ev, h2.events.DataReceived):
                response_data += ev.data
                conn.acknowledge_received_data(ev.flow_controlled_length, ev.stream_id)
            elif isinstance(ev, h2.events.TrailersReceived):
                for k, v in ev.headers:
                    trailers[k] = v
            elif isinstance(ev, h2.events.StreamReset):
                rst_code = ev.error_code
            elif isinstance(ev, h2.events.StreamEnded):
                sock.sendall(conn.data_to_send())
                sock.close()
                if rst_code is not None:
                    raise RuntimeError(
                        f"stream reset by server, error_code={rst_code}; "
                        f"headers={response_headers} trailers={trailers}"
                    )
                # grpc-status may arrive in trailers (normal case) or in
                # the response headers (Trailers-Only for short replies).
                hdrs = dict(response_headers)
                grpc_status = trailers.get("grpc-status") or hdrs.get(
                    "grpc-status"
                )
                grpc_message = trailers.get("grpc-message") or hdrs.get(
                    "grpc-message", ""
                )
                if grpc_status is None:
                    raise RuntimeError(
                        f"no grpc-status in trailers; "
                        f"headers={response_headers} trailers={trailers} "
                        f"data={bytes(response_data).hex()}"
                    )
                if grpc_status != "0":
                    raise RuntimeError(
                        f"gRPC error status={grpc_status} message={grpc_message!r}"
                    )
                payload = bytes(response_data[5:]) if response_data else b""
                return payload
        sock.sendall(conn.data_to_send())
    sock.close()
    raise RuntimeError(
        f"unexpected EOF; headers={response_headers} "
        f"trailers={trailers} data={bytes(response_data).hex()} rst={rst_code}"
    )


def main():
    if len(sys.argv) < 2:
        print(__doc__, file=sys.stderr)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "default":
        body = encode_dns_options(state=0)
        call("SetDnsOptions", body)
        print("Mullvad DNS set to default")
    elif cmd == "custom":
        if len(sys.argv) < 3:
            print("usage: mullvad_dns.py custom <ip> [<ip> ...]", file=sys.stderr)
            sys.exit(1)
        body = encode_dns_options(state=1, addresses=sys.argv[2:])
        call("SetDnsOptions", body)
        print(f"Mullvad DNS set to custom: {', '.join(sys.argv[2:])}")
    elif cmd == "get":
        resp = call("GetSettings", b"")
        dns_opts = extract_dns_options(resp)
        import binascii

        print(f"DnsOptions: {len(dns_opts)} bytes -> {binascii.hexlify(dns_opts).decode()}")
    elif cmd == "save":
        if len(sys.argv) < 3:
            print("usage: mullvad_dns.py save <path>", file=sys.stderr)
            sys.exit(1)
        resp = call("GetSettings", b"")
        dns_opts = extract_dns_options(resp)
        with open(sys.argv[2], "wb") as f:
            f.write(dns_opts)
        print(f"Saved current DnsOptions ({len(dns_opts)} bytes) to {sys.argv[2]}")
    elif cmd == "restore":
        if len(sys.argv) < 3:
            print("usage: mullvad_dns.py restore <path>", file=sys.stderr)
            sys.exit(1)
        with open(sys.argv[2], "rb") as f:
            body = f.read()
        call("SetDnsOptions", body)
        print(f"Restored DnsOptions from {sys.argv[2]}")
    else:
        print(f"unknown command: {cmd}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
