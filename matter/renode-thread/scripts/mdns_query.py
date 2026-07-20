#!/usr/bin/env python3
# Ephemeral-port mDNS querier: does NOT bind 5353 (avoids SO_REUSEPORT confound with
# otbr). Sends PTR queries and listens on its OWN socket for BOTH unicast responses
# (QU) and any multicast the kernel delivers. Reports anything it hears.
import socket, struct, sys, time

IFACE="infra0"; GROUP="ff02::fb"; PORT=5353
dur=float(sys.argv[1]) if len(sys.argv)>1 else 12.0
names=sys.argv[2:] or ["_meshcop._udp.local","_services._dns-sd._udp.local","_matter._tcp.local"]
ifidx=socket.if_nametoindex(IFACE)

def q(name, qu=False):
    hdr=struct.pack(">HHHHHH",0,0,1,0,0,0)
    body=b"".join(bytes([len(l)])+l.encode() for l in name.split("."))+b"\x00"
    qclass=1 | (0x8000 if qu else 0)   # QU bit -> request unicast response
    body+=struct.pack(">HH",12,qclass)
    return hdr+body

s=socket.socket(socket.AF_INET6,socket.SOCK_DGRAM)
s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_MULTICAST_IF,struct.pack("@I",ifidx))
s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_MULTICAST_LOOP,1)
s.setsockopt(socket.IPPROTO_IPV6,socket.IPV6_MULTICAST_HOPS,1)
s.bind(("",0))  # ephemeral
s.setblocking(False)

def labels(data):
    out,i,n=[],0,len(data)
    while i<n:
        l=data[i]
        if 1<=l<=63 and i+1+l<=n:
            c=data[i+1:i+1+l]
            if all(32<=b<127 for b in c): out.append(c.decode()); i+=1+l; continue
        i+=1
    return out

start=time.time(); last=0; heard=0
print(f"querying (QU) {names} on {IFACE} for {dur}s from ephemeral port ...")
while time.time()-start<dur:
    now=time.time()
    if now-last>1.5:
        for nm in names:
            try: s.sendto(q(nm,qu=True),(GROUP,PORT,0,ifidx))
            except OSError as e: print("send err",e)
        last=now
    try:
        data,addr=s.recvfrom(4096)
    except BlockingIOError:
        time.sleep(0.05); continue
    heard+=1
    print(f"RESP from {addr[0]}:{addr[1]} len={len(data)} labels={labels(data)[:14]}")
print(f"--- heard {heard} response packets ---")
