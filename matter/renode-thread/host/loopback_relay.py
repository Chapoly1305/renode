#!/usr/bin/env python3
"""Tiny bidirectional TCP relay.

Used so a Linux `chip-tool` (which hard-connects to 127.0.0.1:$CHIP_FAKE_BLE_PORT in its own network
namespace -- see src/platform/Linux/ble/FakeBleTransport.cpp) can reach a Renode fake-transport socket
running on the Docker *host*. Inside the container run:

    python3 loopback_relay.py 3500 host.docker.internal 3500 &

so chip-tool's 127.0.0.1:3500 is forwarded to the host's Renode. On a Linux host you can skip this
entirely and use `docker run --network host`.

Verified on-host: probe -> relay -> Renode -> EUSART1 delivered CONNECT+SUBSCRIBE frames intact.
"""
import socket
import sys
import threading
import time


def pipe(src, dst):
    try:
        while True:
            data = src.recv(65536)
            if not data:
                break
            dst.sendall(data)
    except OSError:
        pass
    finally:
        for s in (src, dst):
            try:
                s.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass


def main():
    if len(sys.argv) != 4:
        print(f"usage: {sys.argv[0]} <listen-port> <remote-host> <remote-port>", file=sys.stderr)
        return 1
    listen_port = int(sys.argv[1])
    remote_host = sys.argv[2]
    remote_port = int(sys.argv[3])

    srv = socket.socket()
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", listen_port))
    srv.listen()
    print(f"relay 127.0.0.1:{listen_port} -> {remote_host}:{remote_port}", flush=True)

    while True:
        client, _ = srv.accept()
        # Connect upstream to the host's Renode. host.docker.internal routing can transiently fail
        # ("Network is unreachable"/"Connection refused") right after a Renode restart, so retry a
        # few times and -- crucially -- NEVER let one bad connection kill the accept loop (that would
        # leave the relay port dead and chip-tool sees "Connection refused" on every later attempt).
        upstream = None
        for attempt in range(10):
            try:
                upstream = socket.create_connection((remote_host, remote_port), timeout=3)
                break
            except OSError as e:
                print(f"relay: upstream connect failed ({e}); retry {attempt + 1}/10", flush=True)
                time.sleep(0.5)
        if upstream is None:
            print("relay: giving up on this client; keeping listener open", flush=True)
            try:
                client.close()
            except OSError:
                pass
            continue
        upstream.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        client.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        threading.Thread(target=pipe, args=(client, upstream), daemon=True).start()
        threading.Thread(target=pipe, args=(upstream, client), daemon=True).start()


if __name__ == "__main__":
    sys.exit(main())
