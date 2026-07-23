#!/usr/bin/env python3
"""Convert Renode radio log lines ("Sending frame ...") into a BLE LL pcap.

Reproduces the same 10-byte pseudo-header that
src/Plugins/WiresharkPlugin/BLESniffer.cs prepends to each frame, so the
output uses DLT 256 (BLUETOOTH_LE_LL_WITH_PHDR) and opens directly in
Wireshark/tshark without any extra plugin.
"""
import re
import struct
import sys

CHANNEL_TO_WIRESHARK_INDEX = {
    37: 0, 0: 1, 1: 2, 2: 3, 3: 4, 4: 5, 5: 6, 6: 7, 7: 8, 8: 9,
    9: 10, 10: 11, 38: 12, 11: 13, 12: 14, 13: 15, 14: 16, 15: 17,
    16: 18, 17: 19, 18: 20, 19: 21, 20: 22, 21: 23, 22: 24, 23: 25,
    24: 26, 25: 27, 26: 28, 27: 29, 28: 30, 29: 31, 30: 32, 31: 33,
    32: 34, 33: 35, 34: 36, 35: 37, 36: 38, 39: 39,
}

ADVERTISEMENT_ACCESS_ADDRESS = 0x8e89bed6
FLAGS_BASE = 0x1 | 0x2 | 0x4 | 0x8 | 0x10 | 0x20 | 0x400 | 0x800 | 0x1000 | 0x2000

LINE_RE = re.compile(
    r"Sending frame at (\d\d):(\d\d):(\d\d)\.(\d+) on channel (\d+) "
    r"\((\d+)Hz\): ([0-9A-Fa-f-]+)"
)

PCAP_GLOBAL_HEADER = struct.pack("<IHHiIII", 0xa1b2c3d4, 2, 4, 0, 0, 65535, 256)


def build_ble_header(channel, frame):
    wireshark_index = CHANNEL_TO_WIRESHARK_INDEX[channel]
    access_address = frame[0:4]
    flags = FLAGS_BASE  # PDU type Advertisement == 0, so no extra bits
    return bytes([wireshark_index, 0x00, 0x00, 0x00]) + access_address + struct.pack("<H", flags)


def parse_frames(path):
    frames = []
    with open(path, "r", errors="replace") as f:
        for line in f:
            m = LINE_RE.search(line)
            if not m:
                continue
            h, mi, s, frac, channel, freq, hexbytes = m.groups()
            frac = frac.ljust(9, "0")[:9]
            sim_ns = (((int(h) * 60 + int(mi)) * 60 + int(s)) * 1_000_000_000) + int(frac)
            channel = int(channel)
            frame = bytes(int(b, 16) for b in hexbytes.split("-"))
            frames.append((sim_ns, channel, int(freq), frame))
    return frames


def write_pcap(frames, out_path):
    with open(out_path, "wb") as f:
        f.write(PCAP_GLOBAL_HEADER)
        for sim_ns, channel, _freq, frame in frames:
            header = build_ble_header(channel, frame)
            packet = header + frame
            ts_sec = sim_ns // 1_000_000_000
            ts_usec = (sim_ns % 1_000_000_000) // 1000
            f.write(struct.pack("<IIII", ts_sec, ts_usec, len(packet), len(packet)))
            f.write(packet)


def main():
    if len(sys.argv) != 3:
        print(f"usage: {sys.argv[0]} <renode-log-file> <output.pcap>", file=sys.stderr)
        sys.exit(1)

    frames = parse_frames(sys.argv[1])
    if not frames:
        print("No 'Sending frame' lines found in the log.", file=sys.stderr)
        sys.exit(1)

    write_pcap(frames, sys.argv[2])
    print(f"Wrote {len(frames)} BLE frames to {sys.argv[2]}")


if __name__ == "__main__":
    main()
