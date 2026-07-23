#!/usr/bin/env python3
# Drain the device's SEGGER-RTT up-buffer 0 to /tmp/rtt.log via the Renode MONITOR (telnet 127.0.0.1:3456),
# so we see the stock firmware's Matter/OpenThread console (e.g. the [SC] CASE Sigma1/Sigma2 handling) WITHOUT
# an in-emulation hook (less perturbing). Control block _SEGGER_RTT @ 0x200128b0; up[0] descriptor @ +24:
#   sName(4) pBuffer(4) SizeOfBuffer(4) WrOff(4) RdOff(4) Flags(4).
import socket, time, re, sys
CB = 0x200128b0
UP0 = CB + 24
DUR = float(sys.argv[1]) if len(sys.argv) > 1 else 600.0
PORT = int(sys.argv[2]) if len(sys.argv) > 2 else 3456

def connect():
    s = socket.create_connection(("127.0.0.1", PORT), timeout=5); s.settimeout(1.2); time.sleep(0.4)
    _drain(s)
    s.sendall(b"mach set 0\r\n"); time.sleep(0.2); _drain(s)
    return s

def _drain(s):
    try:
        while True:
            if not s.recv(16384): break
    except Exception:
        pass

def cmd(s, c):
    s.sendall((c + "\r\n").encode()); time.sleep(0.12); b = b""
    try:
        while True:
            d = s.recv(65536)
            if not d: break
            b += d
    except Exception:
        pass
    return b.decode(errors="replace")

def readbytes(s, addr, n):
    r = cmd(s, "sysbus ReadBytes 0x%x %d" % (addr, n))
    return bytes(int(v, 16) for v in re.findall(r"0x([0-9A-Fa-f]{2})", r))

def rd32(bs, off):
    return bs[off] | (bs[off+1] << 8) | (bs[off+2] << 16) | (bs[off+3] << 24)

def main():
    s = connect()
    out = open("/tmp/rtt.log", "a")
    out.write("\n==== rtt-drain start ====\n"); out.flush()
    end = time.time() + DUR
    while time.time() < end:
        try:
            desc = readbytes(s, UP0, 24)
            if len(desc) >= 20:
                pBuffer = rd32(desc, 4); size = rd32(desc, 8); wr = rd32(desc, 12); rd = rd32(desc, 16)
                if pBuffer and 0 < size < 0x10000 and wr < size and rd < size and wr != rd:
                    if wr > rd:
                        chunk = readbytes(s, pBuffer + rd, wr - rd)
                    else:
                        chunk = readbytes(s, pBuffer + rd, size - rd) + readbytes(s, pBuffer, wr)
                    out.write(chunk.decode(errors="replace")); out.flush()
                    cmd(s, "sysbus WriteDoubleWord 0x%x 0x%x" % (UP0 + 16, wr))  # advance RdOff
        except Exception as e:
            pass
        time.sleep(0.4)
    out.close(); s.close()

main()
