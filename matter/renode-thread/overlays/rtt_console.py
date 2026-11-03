#!/usr/bin/env python3
# Created: 2026-08-19
# Purpose: Attach a SEGGER-RTT console to a Renode boot probe WITHOUT touching the
#   emulated CPU path. The Aqara 4447 lock images have no UART console at all --
#   iprintf() -> _write() -> SEGGER_RTT_Write(), so every diagnostic the firmware
#   emits ({function():line} tagged, with its own severity) lands in a RAM ring
#   buffer that nothing in the harness was reading. This injects a background
#   monitor thread into a workspace's run.resc that drains that ring exactly the
#   way a J-Link RTT viewer does (read up-buffer, advance RdOff) and re-emits each
#   line into the Renode log as `RTT| ...`.
#
#   Deliberately NOT `cpu AddHook` and NOT a watchpoint: both run on the emulated
#   CPU's execution path, do host I/O per hit and perturb radio timing (that is
#   what contaminated 22 Aqara workspaces on 2026-08-18). This thread only reads
#   the system bus from the monitor thread on a wall-clock timer.
#
#   The control block is resolved AT RUNTIME by scanning SRAM for the 10-byte
#   SEGGER RTT acID and validating the SEGGER_RTT_CB fields that follow it, so the
#   same overlay works on every payload -- the acID is written by SEGGER_RTT_Init
#   byte-by-byte and appears NOWHERE in the flash image (verified: 0 hits across
#   all 34 Aqara 4447 ELFs), so it cannot be resolved statically from the ELF.
#   On an image with no RTT console the scan backs off and the thread DETACHES
#   after --give-up seconds, so it is inert on the Ltech/LG controls.
#
#   Layout used (SEGGER_RTT_CB), cb = address of acID:
#     cb+0x00 acID[16]   cb+0x10 MaxNumUpBuffers   cb+0x14 MaxNumDownBuffers
#     cb+0x18 aUp[0].sName  cb+0x1C pBuffer  cb+0x20 SizeOfBuffer
#     cb+0x24 WrOff         cb+0x28 RdOff    cb+0x2C Flags
#
# Retention: PERMANENT while the Aqara 4447 images are in the corpus.
#
# usage:
#   scripts/renode_overlays/rtt_console.py --in <base run.resc> --out <run.resc>
#         [--mode drain|read] [--hint 0x20019F1C] [--poll 0.25] [--give-up 60]
#         [--lo 0x20000000] [--hi 0x20040000] [--overlay OLD=NEW]
#
# NOTE on --mode: `drain` advances RdOff, which is what a real attached RTT probe
#   does and is required to see the TAIL of the log (Flags=0 is NO_BLOCK_SKIP, so
#   an undrained 8 KiB ring silently drops everything after it fills). `read` never
#   writes to device memory at all; use it if you need to prove the observer is
#   strictly read-only.
import argparse, os, sys

# The injected body. No single quotes anywhere: it is wrapped in exec('...') inside
# a double-quoted Renode monitor argument, so ' would terminate the string.
BODY = """
def _rtt_rd32(sb, a):
    d = bytearray(sb.ReadBytes(a, 4, True))
    return d[0] | (d[1] << 8) | (d[2] << 16) | (d[3] << 24)

def _rtt_ok(sb, cb, lo, hi):
    try:
        nu = _rtt_rd32(sb, cb + 0x10)
        nd = _rtt_rd32(sb, cb + 0x14)
        buf = _rtt_rd32(sb, cb + 0x1c)
        sz = _rtt_rd32(sb, cb + 0x20)
        wr = _rtt_rd32(sb, cb + 0x24)
        rd = _rtt_rd32(sb, cb + 0x28)
    except:
        return None
    if nu < 1 or nu > 16 or nd > 16:
        return None
    if buf < lo or buf >= hi:
        return None
    if sz < 16 or sz > 0x20000 or buf + sz > hi:
        return None
    if wr >= sz or rd >= sz:
        return None
    return (buf, sz, wr, rd)

def _rtt_scan(sb, lo, hi, hint):
    needle = bytearray([83, 69, 71, 71, 69, 82, 32, 82, 84, 84])
    if hint:
        try:
            if bytearray(sb.ReadBytes(hint, 10, True)) == needle and _rtt_ok(sb, hint, lo, hi):
                return hint
        except:
            pass
    step = 0x8000
    a = lo
    while a < hi:
        n = step + 16
        if a + n > hi:
            n = hi - a
        try:
            d = bytearray(sb.ReadBytes(a, n, True))
        except:
            a = a + step
            continue
        i = d.find(needle)
        while i >= 0:
            cb = a + i
            if (cb & 3) == 0 and _rtt_ok(sb, cb, lo, hi):
                return cb
            i = d.find(needle, i + 1)
        a = a + step
    return 0

def _rtt_main():
    import time
    L = Antmicro.Renode.Logging.Logger
    sb = monitor.Machine.SystemBus
    lo = __LO__
    hi = __HI__
    hint = __HINT__
    drain = __DRAIN__
    giveup = __GIVEUP__
    poll = __POLL__
    cb = 0
    buf = 0
    sz = 0
    rd = 0
    part = ""
    errs = 0
    t = 0.0
    iv = 0.25
    while True:
        time.sleep(iv)
        t = t + iv
        try:
            if cb == 0:
                cb = _rtt_scan(sb, lo, hi, hint)
                if cb == 0:
                    if t >= giveup:
                        L.Log(LogLevel.Warning, "RTT: no SEGGER RTT control block in SRAM after " + str(int(t)) + "s -- detaching, this image has no RTT console")
                        return
                    if iv < 2.0:
                        iv = iv * 2
                    continue
                r = _rtt_ok(sb, cb, lo, hi)
                buf = r[0]
                sz = r[1]
                rd = r[3]
                iv = poll
                L.Log(LogLevel.Warning, "RTT: console attached cb=" + hex(cb) + " buf=" + hex(buf) + " size=" + hex(sz) + " rdoff=" + str(rd) + " drain=" + str(drain))
            wr = _rtt_rd32(sb, cb + 0x24)
            if wr >= sz or wr == rd:
                continue
            if wr > rd:
                ch = bytearray(sb.ReadBytes(buf + rd, wr - rd, True))
            else:
                ch = bytearray(sb.ReadBytes(buf + rd, sz - rd, True)) + bytearray(sb.ReadBytes(buf, wr, True))
            rd = wr
            if drain:
                sb.WriteDoubleWord(cb + 0x28, wr)
            part = part + "".join([chr(c) for c in ch])
            while chr(10) in part:
                i = part.index(chr(10))
                ln = part[:i].replace(chr(13), "")
                part = part[i + 1:]
                L.Log(LogLevel.Warning, "RTT| " + ln)
            if len(part) > 400:
                L.Log(LogLevel.Warning, "RTT| " + part)
                part = ""
        except Exception as e:
            errs = errs + 1
            if errs < 4:
                L.Log(LogLevel.Warning, "RTT ERR: " + str(e))
            if errs > 500:
                return
"""

BANNER = """
# ---- SEGGER RTT console drain (scripts/renode_overlays/rtt_console.py) --------
# Observation only: a monitor-thread reader of the firmware's own RTT ring.
# No cpu AddHook, no watchpoint, nothing on the emulated execution path.
# mode=%s  scan=%s..%s  hint=%s  poll=%ss  give-up=%ss
"""


def encode(body):
    if "'" in body:
        raise SystemExit("FATAL: the injected body must not contain a single quote")
    return body.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def block(mode, lo, hi, hint, poll, giveup):
    body = (BODY
            .replace("__LO__", hex(lo))
            .replace("__HI__", hex(hi))
            .replace("__HINT__", hex(hint))
            .replace("__DRAIN__", "True" if mode == "drain" else "False")
            .replace("__GIVEUP__", str(giveup))
            .replace("__POLL__", str(poll)))
    out = BANNER % (mode, hex(lo), hex(hi), hex(hint), poll, giveup)
    out += 'python "exec(\'%s\')"\n' % encode(body)
    out += 'python "import threading"\n'
    out += 'python "threading.Thread(target=_rtt_main).start()"\n'
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--in", dest="src", required=True, help="base run.resc to inject into")
    ap.add_argument("--out", required=True)
    ap.add_argument("--mode", choices=["drain", "read", "off"], default="drain")
    ap.add_argument("--lo", default="0x20000000")
    ap.add_argument("--hi", default="0x20040000")
    ap.add_argument("--hint", default="0x20019F1C",
                    help="address tried before the full scan (Aqara 4447 CB); 0 to disable")
    ap.add_argument("--poll", type=float, default=0.25)
    ap.add_argument("--give-up", dest="giveup", type=float, default=60.0)
    ap.add_argument("--overlay", action="append", default=[],
                    help="OLD=NEW textual substitution applied to the base run.resc")
    a = ap.parse_args()

    txt = open(a.src).read()
    for sub in a.overlay:
        old, new = sub.split("=", 1)
        if old not in txt:
            raise SystemExit("FATAL: --overlay source %r not present in %s" % (old, a.src))
        txt = txt.replace(old, new)

    lines = txt.rstrip("\n").split("\n")
    # inject immediately before the final bare `start`
    idx = None
    for i in range(len(lines) - 1, -1, -1):
        if lines[i].strip() == "start":
            idx = i
            break
    if idx is None:
        raise SystemExit("FATAL: %s has no `start` line to inject before" % a.src)
    if a.mode != "off":
        lines.insert(idx, block(a.mode, int(a.lo, 16), int(a.hi, 16),
                                int(a.hint, 16), a.poll, a.giveup).rstrip("\n"))
    d = os.path.dirname(os.path.abspath(a.out))
    if d:
        os.makedirs(d, exist_ok=True)
    open(a.out, "w").write("\n".join(lines) + "\n")
    print("wrote %s (mode=%s)" % (a.out, a.mode))


main()
