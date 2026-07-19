#!/usr/bin/env python3
# Measure the 802.15.4 MAC-ACK round-trip latency on the sim air, to show WHY attach fails.
# For each unicast data frame from the host leader (port 9001, AR=1), find the matching device
# ACK (port 9002, FCF=0x0002, same MAC seq) and report wall-clock latency vs the ~864us window.
import socket, struct, sys, time, collections

GROUP="224.0.0.116"; PORT=9000; LOCAL="127.0.0.1"
DUR=float(sys.argv[1]) if len(sys.argv)>1 else 90.0

rx=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
rx.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
try: rx.setsockopt(socket.SOL_SOCKET,0x0200,1)
except OSError: pass
rx.bind(("",PORT))
rx.setsockopt(socket.IPPROTO_IP,socket.IP_ADD_MEMBERSHIP,
              struct.pack("=4s4s",socket.inet_aton(GROUP),socket.inet_aton(LOCAL)))
rx.settimeout(1.0)

# pending[seq] = (t_sent, from_who) for the most recent unicast data frame awaiting an ACK
pending={}
lat_ms=[]            # data(from leader)->ack(from device) latencies, ms
lat_ms_rev=[]        # data(from device)->ack(from leader) latencies, ms
retx=collections.Counter()   # (who, seq) -> count, to show retransmits
print("measuring %ss ..."%DUR, flush=True)
end=time.time()+DUR
while time.time()<end:
    try: data,addr=rx.recvfrom(2048)
    except socket.timeout: continue
    t=time.time(); sport=addr[1]
    if len(data)<4: continue
    mpdu=data[1:]                       # strip channel byte
    if len(mpdu)<3: continue
    fcf=mpdu[0]|(mpdu[1]<<8); ftype=fcf&0x7; ar=(mpdu[0]>>5)&1; seq=mpdu[2]
    who={9001:"leader",9002:"device"}.get(sport,"?%d"%sport)
    if ftype==2:                        # ACK frame
        key=("device" if who=="leader" else "leader", seq)  # ack acks the OTHER side's data
        if key in pending:
            t0=pending.pop(key)
            dl=(t-t0)*1000.0
            (lat_ms if key[0]=="leader" else lat_ms_rev).append(dl)
    elif ftype in (1,3) and ar:         # data/cmd unicast expecting ack
        # only track unicast (dst != broadcast). crude: dst short 0xFFFF at bytes 5-6 when panid-compressed
        pending[(who,seq)] = t
        retx[(who,seq)] += 1

def stats(name, xs):
    if not xs:
        print("  %s: no matched ACKs"%name); return
    xs2=sorted(xs); n=len(xs2)
    print("  %s: n=%d  min=%.2fms  median=%.2fms  max=%.2fms  (802.15.4 ACK window ~0.864ms)"
          %(name, n, xs2[0], xs2[n//2], xs2[-1]))
    within=sum(1 for x in xs2 if x<=0.864)
    print("      within 864us window: %d/%d (%.0f%%)"%(within,n,100.0*within/n))

print("---- ACK round-trip latency ----")
stats("leader->device data, device ACK back", lat_ms)
stats("device->leader data, leader ACK back", lat_ms_rev)
multi=[(k,c) for k,c in retx.items() if c>=3]
print("  unicast frames retransmitted >=3x (no timely ACK): %d distinct (top: %s)"
      %(len(multi), sorted([c for _,c in multi], reverse=True)[:6]))
