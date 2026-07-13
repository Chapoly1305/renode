# OTA Reachability Chains — source→sink for each Zigbee finding

Reference for the over-the-air reachability PoCs. Each chain is the static
xref path from the radio RX entry to the vulnerable sink. The OTA PoC injects a
real 802.15.4/ZCL frame and hooks these nodes to observe how far the frame
propagates *on its own* (no forced PC).

**Shared precondition (all Zigbee findings):** the attacker frame is a
manufacturer-specific ZCL command carried in an APS payload. Reaching the ZCL
dispatch requires the frame to pass NWK/APS layers — i.e. the attacker holds the
**network key** (joined/commissioned node). So the reachability PoC must either
(a) drive the firmware to establish/join a network first, or (b) inject at a
layer above NWK/APS decryption, or (c) document the network-membership
precondition as the exploitability boundary.

## ZB-06 Heiman HS1SA (sink sub_1662e @0x1662E)
```
sub_1CC4E → sub_C22C(@0xC22C, 802.15.4 MTU dequeue via sub_120B0)
          → sub_E376(@0xE376) → sub_7D46(@0x7D46) → sub_7B5A(@0x7B5A)
          → sub_a33c(@0xA33C, gate: mfgCode==0x120B && dir==0 && cmdId==0xF3)
          → sub_1662e(@0x1662E, record-count overflow)
```

## ZB-02 Aqara feeder (sink sub_14D08 @0x14D08)
```
sub_1CD38 (ZCL RX, "RX len %d, ep %x, clus 0x%2x")
  → j_sub_5972 → sub_14EB4 (emberCommandReceivedCallback;
       gate: mfg cluster 0xFCC0 + cmdId==2 + msg_type==0xFFF1)
  → sub_14DEC → sub_14D08 (when frame[0]!=0 segmented flag)
```
Auth gate sub_17b7c reads *0x2000495e (default 0) = **default-open**.
Heap OOB (segment_index=frame[2] unbounded), not saved-LR.

## ZB-03 Innr plug (sink sub_22E06 @0x22E06)
```
sub_27158 → sub_27306(@0x274AE, ZCL/APS dispatcher; cluster @[0x2000F7A0]+2)
          → sub_163F0(@0x16464) → sub_F8A8(@0xF8E4) → sub_C5F2(@0xC64A)
          → sub_22E06 (mfg cmd 0x8004; a3[0] indexes table @0x2000F434)
```
sub_F882 copies ZCL payload into 82-byte stack buf (capped 0x52), passed as a3/a4.

## ZB-05 Niko switchx2 (sink sub_D290 @0xD290)
```
(radio APS inbound) → ZCL Write-Attribute dispatch
          → sub_D290 (0xFC00 mfg WriteAttr parser, case 0x107)
```
Requires only network-key membership; no further gate. Report's this-round flash
scan missed this handler (RAM-based indirect dispatch); confirmed from
prior-session evidence + disasm.

## Notes for the PoC
- RX-enable in the model: firmware must write RAC `RXENSourceEnable` (SWRXEN) via
  the sequencer to put the radio in RxSearch/RxFrame. The medium delivers a frame
  (`WirelessMedium.FrameSentHandler`) only if `receiver.Channel == sender.Channel`
  AND the victim radio calls up into firmware on RX-complete IRQ.
- Frame delivery: `radio.ReceiveFrame(byte[], IRadio sender)` — needs a valid
  sender IRadio and matching channel.
- ZCL frame layering (see ZB-02 build_zcl_messages.py): APS clusterId, then ZCL
  header `struct.pack("<B H B B", frame_control, mfg_code, sequence, command_id)`
  with frame_control mfg-specific bit set (e.g. 0x15 = cluster-specific +
  manufacturer-specific + disable-default-response), then command body.
