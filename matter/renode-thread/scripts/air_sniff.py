#!/usr/bin/env python3
# Passive sniffer for the OpenThread-simulation multicast air (224.0.0.116:9000).
# Non-intrusive (SO_REUSEPORT + group join). Classifies each frame by source port
# (9001=host leader node1, 9002=Renode bridge/device node2), validates the 802.15.4
# CRC-16/KERMIT FCS, and decodes the MAC dest so we can tell broadcast MLE from a
# UNICAST Parent Response (leader -> device).
import socket, struct, sys, time, collections

GROUP="224.0.0.116"; PORT=9000; LOCAL="127.0.0.1"
DUR=float(sys.argv[1]) if len(sys.argv)>1 else 60.0

def kermit(data):
    crc=0
    for b in data:
        crc^=b
        for _ in range(8):
            crc = (crc>>1)^0x8408 if (crc&1) else (crc>>1)
    return crc & 0xFFFF

def decode_dst(mpdu):
    # returns (dst_desc, is_unicast) ; best-effort 802.15.4 MHR parse
    if len(mpdu)<3: return ("?",False)
    fcf = mpdu[0] | (mpdu[1]<<8)
    dst_mode = (fcf>>10)&0x3   # 0 none,2 short,3 ext
    idx=3  # after FCF(2)+seq(1)
    if dst_mode==0: return ("no-dst",False)
    # dest PAN (2)
    if idx+2>len(mpdu): return ("trunc",False)
    idx+=2
    if dst_mode==2:
        if idx+2>len(mpdu): return ("trunc",False)
        d=mpdu[idx]|(mpdu[idx+1]<<8)
        return (("bcast 0xFFFF" if d==0xFFFF else "UNICAST 0x%04X"%d), d!=0xFFFF)
    if dst_mode==3:
        if idx+8>len(mpdu): return ("trunc",False)
        return ("UNICAST ext %s"%mpdu[idx:idx+8].hex(), True)
    return ("?",False)

rx=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
rx.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
try: rx.setsockopt(socket.SOL_SOCKET,0x0200,1)
except OSError: pass
rx.bind(("",PORT))
mreq=struct.pack("=4s4s",socket.inet_aton(GROUP),socket.inet_aton(LOCAL))
rx.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,mreq)
rx.settimeout(1.0)

stats=collections.Counter()
unicast_from_leader=0
fcs_bad=collections.Counter()
end=time.time()+DUR
print("sniffing %ss on %s:%d ..."%(DUR,GROUP,PORT),flush=True)
while time.time()<end:
    try: data,addr=rx.recvfrom(2048)
    except socket.timeout: continue
    sport=addr[1]
    if len(data)<3: continue
    ch=data[0]; mpdu=data[1:]
    who={9001:"leader",9002:"device"}.get(sport,"p%d"%sport)
    stats[who]+=1
    fcs_ok = len(mpdu)>=4 and kermit(mpdu[:-2])==(mpdu[-2]|(mpdu[-1]<<8))
    if not fcs_ok: fcs_bad[who]+=1
    dst,is_uni=decode_dst(mpdu)
    if who=="leader" and is_uni:
        unicast_from_leader+=1
        print("  LEADER->UNICAST len=%d ch=%d dst=%s fcs_ok=%s : %s"%(len(mpdu),ch,dst,fcs_ok,mpdu[:16].hex()),flush=True)
print("---- summary ----")
for who,c in stats.items(): print("  %s frames=%d fcs_bad=%d"%(who,c,fcs_bad[who]))
print("  leader UNICAST frames (Parent Response candidates)=%d"%unicast_from_leader)
