#!/usr/bin/env python3
"""Smoke-test the Renode fake CHIPoBLE transport without a full chip-tool.

Speaks the same framed byte protocol as src/platform/{silabs/efr32,Linux/ble}/FakeBLETransport.cpp:
    [1 byte type][2 bytes length, little-endian][payload...]
    type: 0x01 CONNECT, 0x02 DISCONNECT, 0x03 WRITE_REQUEST(C1),
          0x04 SUBSCRIBE, 0x05 UNSUBSCRIBE, 0x06 INDICATION(C2)

It connects to the TCP server that `matter-fake-transport.resc` exposes (CreateServerSocketTerminal),
sends CONNECT + SUBSCRIBE, and prints any frames the device emits. This does NOT perform PASE (no
SPAKE2+ crypto) -- it only verifies that the emulator's socket<->EUSART1 bridge and the firmware's
FakeBLETransport task are wired up: you should see the device log
    FakeBLETransport: CONNECT
    FakeBLETransport: SUBSCRIBE
in the Renode console, and (once the CHIPoBLE service is up) may see INDICATION frames here.

Usage:
    python3 fake_transport_probe.py [--host 127.0.0.1] [--port 3500] [--listen-secs 5]
"""
import argparse
import socket
import struct
import sys
import time

CONNECT, DISCONNECT, WRITE_REQUEST, SUBSCRIBE, UNSUBSCRIBE, INDICATION = 0x01, 0x02, 0x03, 0x04, 0x05, 0x06
TYPE_NAMES = {
    CONNECT: "CONNECT", DISCONNECT: "DISCONNECT", WRITE_REQUEST: "WRITE_REQUEST",
    SUBSCRIBE: "SUBSCRIBE", UNSUBSCRIBE: "UNSUBSCRIBE", INDICATION: "INDICATION",
}


def send_frame(sock, ftype, payload=b""):
    sock.sendall(bytes([ftype]) + struct.pack("<H", len(payload)) + payload)
    print(f"  -> {TYPE_NAMES.get(ftype, hex(ftype))} len={len(payload)}")


def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        chunk = sock.recv(n - len(buf))
        if not chunk:
            return None
        buf += chunk
    return buf


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=3500)
    ap.add_argument("--listen-secs", type=float, default=5.0)
    args = ap.parse_args()

    print(f"Connecting to fake transport at {args.host}:{args.port} ...")
    with socket.create_connection((args.host, args.port), timeout=10) as sock:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        print("Connected. Sending CONNECT + SUBSCRIBE:")
        send_frame(sock, CONNECT)
        time.sleep(0.2)
        send_frame(sock, SUBSCRIBE)

        print(f"Listening {args.listen_secs}s for device INDICATION frames ...")
        sock.settimeout(args.listen_secs)
        got = 0
        deadline = time.time() + args.listen_secs
        try:
            while time.time() < deadline:
                header = recv_exact(sock, 3)
                if header is None:
                    print("  device closed the connection")
                    break
                ftype = header[0]
                plen = header[1] | (header[2] << 8)
                payload = recv_exact(sock, plen) if plen else b""
                got += 1
                name = TYPE_NAMES.get(ftype, hex(ftype))
                print(f"  <- {name} len={plen} payload={payload[:32].hex()}{'...' if plen > 32 else ''}")
        except socket.timeout:
            pass

    print()
    print(f"Received {got} frame(s) from the device.")
    print("Check the Renode console for 'FakeBLETransport: CONNECT' and 'FakeBLETransport: SUBSCRIBE'"
          " -- their presence confirms the socket<->EUSART1 bridge and firmware task are working.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
