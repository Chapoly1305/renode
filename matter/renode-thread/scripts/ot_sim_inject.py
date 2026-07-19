import socket, struct, sys, time
GROUP="224.0.0.116"; PORT=9000; NODEID=1  # pretend to be OT-sim node 1
# bind tx socket to our node port so the bridge/OT peers see our node id via source port
tx=socket.socket(socket.AF_INET,socket.SOCK_DGRAM,socket.IPPROTO_UDP)
tx.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
try: tx.setsockopt(socket.SOL_SOCKET,0x0200,1)  # SO_REUSEPORT
except OSError: pass
tx.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_IF,socket.inet_aton("127.0.0.1"))
tx.setsockopt(socket.IPPROTO_IP,socket.IP_MULTICAST_LOOP,1)
tx.bind(("127.0.0.1",PORT+NODEID))
# frame = [channel=15][MPDU]; craft a minimal 802.15.4 ACK-ish/data MPDU (FC, seq, panid, addrs, payload, FCS placeholder)
mpdu=bytes([0x41,0xD8,0x99,0x34,0x12,0xFF,0xFF,0x11,0x22,0x33,0x44,0x55,0x66,0x77,0x88,0xAA,0xBB,0x00,0x00])
dg=bytes([15])+mpdu
for i in range(5):
    tx.sendto(dg,(GROUP,PORT)); print("sent",len(dg),"bytes to",GROUP,PORT,"from port",PORT+NODEID,flush=True); time.sleep(0.3)
