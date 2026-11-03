#!/usr/bin/env python3
# Created: 2026-08-18
# Purpose: Extract, inspect, verify and redact the Silicon Labs *provision page*
#   that HEIMAN (VID 4619) firmware reads at FLASH_BASE+FLASH_SIZE-FLASH_PAGE_SIZE
#   (0x0817E000 on a 1536K MG24). The page is above __main_flash_end__, is in no
#   GBL tag, and is therefore absent from every OTA-lifted image -- Renode serves
#   0xFF there and HEIMAN's GetSpake2pVerifier takes an unprovisioned-default
#   branch that returns a 16-byte "verifier", aborting Server::Init. Written to
#   replace 34 hand-poked `sysbus WriteDoubleWord` lines with a real page taken
#   off hardware (compass_knowledgebase/golden_cases/case_studies/silabs/
#   heiman-smoke-4619-4102/backup/full_flash_0x08000000_0x180000.elf, an SWD dump).
# Retention: PERMANENT while HEIMAN is in the emulation corpus.
"""HEIMAN Silicon Labs provision-page tool.

Subcommands
  extract  <full-flash-dump.elf> -o page.bin   pull the 8 KiB page out of a dump
  inspect  <page.bin>                          decode every field the firmware reads
  redact   <page.bin> -o out.bin               zero the plaintext DAC private key,
                                               recompute the page checksum
  csa-creds <page.bin> -o out.bin              SUBSTITUTE the CSA test commissioning
                                               credentials (passcode 20202021, salt
                                               "SPAKE2P Key Salt", 1000 iterations,
                                               discriminator 3840) for the device's
                                               own, recompute the checksum. Only for
                                               harness paths that hardcode 20202021;
                                               prefer the unmodified page.

Page layout (derived by disassembling the 17 code sites in every HEIMAN ELF that
reference the base 0x0817E000; identical in all 28 HEIMAN images in the corpus,
verified by matching the LDR.W imm12 encodings for each offset):

  0x000  u32     0  (attestation key slot header; observed 0)
  0x004  65 B    DAC public key, SEC1 uncompressed (0x04 || X || Y)
  0x045  3 B     padding
  0x048  32 B    DAC PRIVATE key, big-endian P-256 scalar, PLAINTEXT
  0x068  u32     DAC DER length          0x06c  DAC DER  (slot 600 B)
  0x2c4  u32     PAI DER length          0x2c8  PAI DER  (slot 600 B)
  0x520  u32     CD  DER length          0x524  CD  DER  (slot 1024 B)
  0x924  u32     discriminator           (>=0x1000 -> firmware default 0xF00)
  0x928  u32     setup passcode
  0x92c  u32     SPAKE2+ iterations      (>0xFFFE -> firmware default 1000)
  0x930  u32     salt b64 length         0x934  salt, base64 ASCII
  0x974  u32     verifier b64 length     0x978  verifier, base64 ASCII
  0xa2c  u32     product id              0xa30  u32 vendor id
  0xa34  char[32] product name           0xa54  char[32] vendor name
  0xa74  char[32] hardware version str   0xa94  u32 hardware version
  0xadc  char[]  serial number
  0xb84  u32     checksum = sum of bytes [0x000,0xb84) mod 2**32

  Every length/string field is also read for the erased pattern (0xFFFFFFFF /
  leading 0xFF) and falls back to a compiled-in default when erased.
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import struct
import sys

PAGE_ADDR = 0x0817E000
PAGE_SIZE = 0x2000
CHECKSUM_OFF = 0xB84
KEY_OFF, KEY_LEN = 0x48, 32
CURVE_N = 0xFFFFFFFF00000000FFFFFFFFFFFFFFFFBCE6FAADA7179E84F3B9CAC2FC632551


def u32(p: bytes, o: int) -> int:
    return struct.unpack_from("<I", p, o)[0]


def cstr(p: bytes, o: int, n: int) -> str:
    return p[o:o + n].split(b"\x00")[0].decode("utf-8", "replace")


def checksum(p: bytes) -> int:
    return sum(p[0:CHECKSUM_OFF]) & 0xFFFFFFFF


def spake2p_verifier(passcode: int, salt: bytes, iterations: int) -> str:
    """Matter SPAKE2+ verifier (w0 || L), base64. Needs `cryptography`."""
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

    ws = hashlib.pbkdf2_hmac("sha256", struct.pack("<I", passcode), salt, iterations, 80)
    w0 = int.from_bytes(ws[:40], "big") % CURVE_N
    w1 = int.from_bytes(ws[40:], "big") % CURVE_N
    L = ec.derive_private_key(w1, ec.SECP256R1()).public_key().public_bytes(
        Encoding.X962, PublicFormat.UncompressedPoint)
    return base64.b64encode(w0.to_bytes(32, "big") + L).decode()


def cmd_extract(a) -> int:
    raw = open(a.dump, "rb").read()
    if raw[:4] == b"\x7fELF":
        # J-Link/OpenOCD dumps in this tree are a single PROGBITS section holding
        # the flash image; section addr is 0 and the file offset is what matters.
        shoff, shentsize, shnum, shstrndx = (
            struct.unpack_from("<I", raw, 32)[0],
            struct.unpack_from("<H", raw, 46)[0],
            struct.unpack_from("<H", raw, 48)[0],
            struct.unpack_from("<H", raw, 50)[0],
        )
        strtab_off = struct.unpack_from("<I", raw, shoff + shstrndx * shentsize + 16)[0]
        body = None
        for i in range(shnum):
            o = shoff + i * shentsize
            nameoff, stype, _fl, addr, off, size = struct.unpack_from("<6I", raw, o)
            name = raw[strtab_off + nameoff:raw.index(b"\x00", strtab_off + nameoff)].decode()
            if stype == 1 and size >= 0x100000:
                body, body_base = raw[off:off + size], addr or a.flash_base
                print(f"section {name} addr=0x{addr:08x} size=0x{size:x}", file=sys.stderr)
        if body is None:
            print("no large PROGBITS section found", file=sys.stderr)
            return 2
    else:
        body, body_base = raw, a.flash_base
    idx = (a.addr - (body_base or a.flash_base))
    if not 0 <= idx <= len(body) - PAGE_SIZE:
        print(f"page 0x{a.addr:08x} not inside dump", file=sys.stderr)
        return 2
    page = body[idx:idx + PAGE_SIZE]
    open(a.out, "wb").write(page)
    print(f"wrote {a.out} ({len(page)} bytes, {sum(1 for b in page if b != 0xff)} non-erased)")
    return 0


def cmd_inspect(a) -> int:
    p = open(a.page, "rb").read()
    if len(p) != PAGE_SIZE:
        print(f"warning: {len(p)} bytes, expected {PAGE_SIZE}", file=sys.stderr)
    print(f"vendor id     0x{u32(p,0xa30):04x} ({u32(p,0xa30)})")
    print(f"product id    0x{u32(p,0xa2c):04x} ({u32(p,0xa2c)})")
    print(f"product name  {cstr(p,0xa34,32)!r}")
    print(f"vendor name   {cstr(p,0xa54,32)!r}")
    print(f"hw version    {u32(p,0xa94)} / {cstr(p,0xa74,32)!r}")
    print(f"serial number {cstr(p,0xadc,32)!r}")
    print(f"discriminator {u32(p,0x924)}")
    print(f"passcode      {u32(p,0x928)}")
    print(f"iterations    {u32(p,0x92c)}")
    salt_b64 = p[0x934:0x934 + u32(p, 0x930)]
    ver_b64 = p[0x978:0x978 + u32(p, 0x974)]
    print(f"salt      ({u32(p,0x930):3d}) {salt_b64.decode('ascii','replace')}")
    print(f"verifier  ({u32(p,0x974):3d}) {ver_b64.decode('ascii','replace')}")
    for off, name in ((0x68, "DAC"), (0x2c4, "PAI"), (0x520, "CD ")):
        n = u32(p, off)
        head = p[off + 4:off + 10].hex() if 0 < n < 0x1000 else "-"
        print(f"{name} len {n:4d} head {head}")
    key = p[KEY_OFF:KEY_OFF + KEY_LEN]
    present = any(b not in (0x00, 0xFF) for b in key)
    print(f"DAC private key at 0x{KEY_OFF:03x}: {'PRESENT (plaintext!)' if present else 'absent/redacted'}")
    got, want = checksum(p), u32(p, CHECKSUM_OFF)
    print(f"checksum      computed 0x{got:08x} stored 0x{want:08x} {'OK' if got == want else 'MISMATCH'}")
    try:
        calc = spake2p_verifier(u32(p, 0x928), base64.b64decode(salt_b64), u32(p, 0x92c))
        print(f"verifier vs (passcode,salt,iterations): "
              f"{'CONSISTENT' if calc.encode() == ver_b64 else 'INCONSISTENT'}")
    except Exception as exc:  # pragma: no cover - diagnostic only
        print(f"verifier recompute skipped: {exc}")
    return 0


def cmd_csa_creds(a) -> int:
    """Replace the commissioning credentials with the published CSA test set.

    This is a *substitution* -- four fields stop being the manufacturer's data.
    Only do it for a harness path that cannot be told a different passcode (the
    otbr flow in ~/renode-matter-groundtruth hardcodes `20202021 3840`).
    `--pase-read` takes PASE_PASSCODE/PASE_DISCRIMINATOR from the environment and
    needs none of this.
    """
    salt_b64 = base64.b64encode(b"SPAKE2P Key Salt")
    ver_b64 = spake2p_verifier(20202021, b"SPAKE2P Key Salt", 1000).encode()
    p = bytearray(open(a.page, "rb").read())
    struct.pack_into("<I", p, 0x924, 3840)
    struct.pack_into("<I", p, 0x928, 20202021)
    struct.pack_into("<I", p, 0x92c, 1000)
    p[0x930:0x974] = b"\x00" * (0x974 - 0x930)
    struct.pack_into("<I", p, 0x930, len(salt_b64))
    p[0x934:0x934 + len(salt_b64)] = salt_b64
    p[0x974:0xa2c] = b"\x00" * (0xa2c - 0x974)
    struct.pack_into("<I", p, 0x974, len(ver_b64))
    p[0x978:0x978 + len(ver_b64)] = ver_b64
    struct.pack_into("<I", p, CHECKSUM_OFF, checksum(bytes(p)))
    open(a.out, "wb").write(bytes(p))
    print(f"wrote {a.out}: discriminator/passcode/iterations/salt/verifier replaced with "
          f"the CSA test set, checksum recomputed to 0x{u32(bytes(p),CHECKSUM_OFF):08x}")
    return 0


def cmd_redact(a) -> int:
    p = bytearray(open(a.page, "rb").read())
    p[KEY_OFF:KEY_OFF + KEY_LEN] = b"\x00" * KEY_LEN
    struct.pack_into("<I", p, CHECKSUM_OFF, checksum(bytes(p)))
    open(a.out, "wb").write(bytes(p))
    print(f"wrote {a.out}: DAC private key at 0x{KEY_OFF:03x}..0x{KEY_OFF+KEY_LEN:03x} zeroed, "
          f"checksum recomputed to 0x{u32(bytes(p),CHECKSUM_OFF):08x}")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    e = sub.add_parser("extract")
    e.add_argument("dump")
    e.add_argument("-o", "--out", required=True)
    e.add_argument("--addr", type=lambda s: int(s, 0), default=PAGE_ADDR)
    e.add_argument("--flash-base", type=lambda s: int(s, 0), default=0x08000000)
    e.set_defaults(fn=cmd_extract)

    i = sub.add_parser("inspect")
    i.add_argument("page")
    i.set_defaults(fn=cmd_inspect)

    c = sub.add_parser("csa-creds")
    c.add_argument("page")
    c.add_argument("-o", "--out", required=True)
    c.set_defaults(fn=cmd_csa_creds)

    r = sub.add_parser("redact")
    r.add_argument("page")
    r.add_argument("-o", "--out", required=True)
    r.set_defaults(fn=cmd_redact)

    a = ap.parse_args()
    return a.fn(a)


if __name__ == "__main__":
    raise SystemExit(main())
