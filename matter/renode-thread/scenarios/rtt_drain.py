# Periodic SEGGER-RTT up-buffer 0 drain -> /tmp/rtt.log (host-readable).
# _SEGGER_RTT control block @ 0x200128b0; layout: acID[16], MaxUp(4), MaxDown(4), then aUp[0]=
# {sName(4), pBuffer(4), SizeOfBuffer(4), WrOff(4), RdOff(4), Flags(4)} at offset 24.
CB = 0x200128b0
UP0 = CB + 24
sb = self.SystemBus
try:
    pBuffer = sb.ReadDoubleWord(UP0 + 4)
    size    = sb.ReadDoubleWord(UP0 + 8)
    wr      = sb.ReadDoubleWord(UP0 + 12)
    rd      = sb.ReadDoubleWord(UP0 + 16)
    if pBuffer != 0 and 0 < size < 0x10000 and wr < size and rd < size and wr != rd:
        out = []
        i = rd
        n = 0
        while i != wr and n < size:
            out.append(chr(sb.ReadByte(pBuffer + i) & 0xff))
            i = (i + 1) % size
            n += 1
        sb.WriteDoubleWord(UP0 + 16, wr)   # advance RdOff so the target keeps writing (drain)
        f = open('/tmp/rtt.log', 'a')
        f.write(''.join(out))
        f.close()
except:
    pass
