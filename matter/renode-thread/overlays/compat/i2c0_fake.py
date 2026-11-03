# Class C: canned legacy I2C state for historical diagnostics only.
if request.IsRead:
    if request.Offset == 0x3C: # IF
        request.Value = 0x68 # TXC | RXDATAV | ACK
    elif request.Offset == 0x14: # STATUS
        request.Value = 0x1C0 # TXC | TXBL | RXDATAV
    elif request.Offset == 0x10: # STATE
        request.Value = 0 # Not busy
    else:
        request.Value = 0
