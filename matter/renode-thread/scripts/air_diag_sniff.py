#!/usr/bin/env python3
# Disambiguation sniffer for the OT-sim air (224.0.0.116:9000 on 127.0.0.1), wall-clock
# (unix epoch, directly comparable to chip-tool's [<epoch>] log timestamps).
# MAC payload is link-encrypted so we can't read UDP ports, but size + MAC frame-type +
# unicast/broadcast are UNENCRYPTED. A burst of LARGE (~100-127B) UNICAST DATA frames is a
# fragmented CASE message (Sigma1 host->device ~2 frags; Sigma2 device->host ~6 frags);
# MLE advertisements/ACKs are small. Direction is by source port:
#   9002 = Renode bridge  -> device->host  (a frame the DEVICE transmitted)
#   9003 = otbr ot-rcp    -> host->device  (a frame OTBR transmitted toward the device)
import socket, struct, sys, time

GROUP="224.0.0.116"; PORT=9000; LOCAL="127.0.0.1"
DUR=float(sys.argv[1]) if len(sys.argv)>1 else 900.0
BIG=95  # MPDU byte threshold: above this = likely a 6LoWPAN fragment of a large datagram (CASE)

def kermit(data):
    crc=0
    for b in data:
        crc^=b
        for _ in range(8):
            crc = (crc>>1)^0x8408 if (crc&1) else (crc>>1)
    return crc & 0xFFFF

FTYPE={0:"beacon",1:"DATA",2:"ACK",3:"CMD"}
def parse_mhr(mpdu):
    if len(mpdu)<3: return ("?","?")
    fcf=mpdu[0]|(mpdu[1]<<8)
    ftype=FTYPE.get(fcf&0x7,"?")
    dst_mode=(fcf>>10)&0x3
    idx=3
    dst="no-dst"
    if dst_mode!=0 and idx+2<=len(mpdu):
        idx+=2  # dst PAN
        if dst_mode==2 and idx+2<=len(mpdu):
            d=mpdu[idx]|(mpdu[idx+1]<<8)
            dst=("BCAST" if d==0xFFFF else "UNI:%04X"%d)
        elif dst_mode==3:
            dst="UNI:ext"
    return (ftype,dst)

rx=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
rx.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
try: rx.setsockopt(socket.SOL_SOCKET,0x0200,1)
except OSError: pass
rx.bind(("",PORT))
mreq=struct.pack("=4s4s",socket.inet_aton(GROUP),socket.inet_aton(LOCAL))
rx.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,mreq)
rx.settimeout(1.0)

DIR={9002:"d2h",9003:"h2d"}
end=time.time()+DUR
sys.stdout.write("air-diag-sniff start\n"); sys.stdout.flush()
while time.time()<end:
    try: data,addr=rx.recvfrom(2048)
    except socket.timeout: continue
    except OSError: break
    sport=addr[1]
    if len(data)<3: continue
    mpdu=data[1:]  # data[0]=channel
    d=DIR.get(sport,"p%d"%sport)
    ftype,dst=parse_mhr(mpdu)
    n=len(mpdu)
    # Only log the interesting frames: any LARGE frame (CASE fragment candidate), and any
    # UNICAST data frame, tagged. Skips the flood of small broadcast MLE/ACKs.
    big = n>=BIG
    if big or (ftype=="DATA" and dst.startswith("UNI")):
        tag = "  <== CASE-FRAG?" if (big and ftype=="DATA" and dst.startswith("UNI")) else ""
        sys.stdout.write("%.3f %s len=%d %s %s%s\n"%(time.time(),d,n,ftype,dst,tag))
        sys.stdout.flush()
sys.stdout.write("air-diag-sniff done\n"); sys.stdout.flush()
