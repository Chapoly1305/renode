# Aliro U400 — Pre-Auth Reachability of the L2CAP Reassembly Overflow (static proof)

**Date:** 2026-07-18 · **Target:** Aqara U400 fw v3110 (`analysis/U400_ls.bndb`).
Companion to `ALIRO_L2CAP_DYNAMIC_VALIDATION.md` (the overflow) and
`ALIRO_UNLOCK_REACHABILITY_POC.md` (overflow → PC = unlock). This doc answers the
remaining question a vendor PoC needs: **can an *unpaired / unauthenticated* BLE peer
reach the overflow?**

## Verdict

**YES — pre-authentication, and pre-application-crypto.** The overflow is in the SDK
L2CAP CoC **transport reassembly** and fires on raw wire bytes as fragments arrive.
Every hop from "radio link up" to the overflowing `memcpy` was walked in the call
graph and decompiled; **none is gated on pairing, bonding, encryption, PASE/CASE, or
commissioning.** The only preconditions are public protocol constants (SPSM + a
device-local readiness flag).

This is a **static** proof (call graph + decompilation). It is stronger than the
emulator PoC on the *auth* question: the emulator pre-seeds the opened-channel state,
whereas this shows there is no auth check to seed around.

## The chain (all addrs U400, thumb2)

```
[radio link up — no pairing required to establish a GAP connection]
   │  sl_bt_evt_connection_opened   (masked id 0x00600a0, logs "BLE Connected")
   ▼
sub_805df38   aliro_ble backend event handler (registered @sub_805db80:0x805dc30)
   │  case 0x600a0:  *0x2001b754++ ;  sub_805dd04(...)
   ▼
sub_805dd04 → sub_80ec036 → sub_805e9c8 / sub_805e9ca   (aliro_ble_l2cap_start_recv_data)
   │  UNCONDITIONAL on connect. No auth check.
   ▼
sub_805e5fc  aliro_l2cap_transfer_create  → 60-byte descriptor, SPSM=0x0080 (descriptor+0x16),
   │  max_sdu=500, max_pdu=247, vtable ptr=0x20002828 ; registered via sub_801df64
   ▼  into RX list head *0x20013878
[attacker opens an L2CAP LE Credit-Based channel on the Aliro SPSM]
   │  sl_bt_evt_l2cap_le_channel_open_request  (masked id 0x14300a0)
   ▼
sub_801de58  sli_bt_l2cap_transfer_on_bt_event  → sub_801d81c  (open-request responder)
   │  ACCEPT criteria — ONLY:
   │    (1) a transfer registered for this connection  (auto-registered above, pre-auth)
   │    (2) request SPSM == transfer SPSM 0x0080        (fixed compiled-in constant)
   │    (3) sub_805e124() != 0  = *0x20024074           (device-local readiness flag, set at boot)
   │  → sub_80aa368(conn, cid, max_sdu, max_pdu, credit, 0)   (open-channel-response = ACCEPT)
   │  *** NO sl_bt_sm_* / no encryption / no bonding / no authorization ***
   ▼
[attacker sends l2cap_channel_data PDUs]
   │  sl_bt_evt_l2cap_channel_data  (masked id 0x34300a0)
   ▼
sub_801de58 → sub_801d96c  (channel_data reassembly handler)
   │  blx r7 = vtable[1] = on_data_received @0x805e7b0
   ▼
on_data_received @0x805e7b0:  memcpy(*(t+0x34)+offset, data, frag_len)   ← HEAP OVERFLOW, no clamp
   (post-copy bounds check @0x801d9c8 runs AFTER the memcpy — too late)
```

## Why "pre-auth" and "pre-crypto" both hold

- **Pre-auth:** the transfer is registered on `sl_bt_evt_connection_opened` — the very
  first event after the GAP link is established, before SMP pairing, before any Matter
  PASE/CASE, before commissioning. Channel acceptance (`sub_801d81c`) adds no auth check;
  it calls the open-channel-response BGAPI with success purely on SPSM + version-flag match.
- **Pre-crypto:** the overflowing `memcpy` is in the **transport reassembly** layer
  (`on_data_received` copies each fragment into the `malloc(sdu_size)` SDU buffer as it
  arrives). Any Aliro application-layer authentication/decryption operates on the
  *completed SDU* higher up — it never runs before the per-fragment `memcpy`. So even a
  peer that could never pass Aliro's app-layer crypto still triggers the overflow during
  reassembly.

## Preconditions are NOT authentication

| Gate | What it is | Attacker cost |
|---|---|---|
| Transfer registered for the connection | auto-created on GAP connect | free (just connect) |
| SPSM == 0x0080 | fixed protocol constant, identical on every U400 | none — public/discoverable |
| `*0x20024074 != 0` | device-local firmware-readiness flag, set during init | none — true on any booted lock |

None is a per-device secret, a shared key, or an authenticated handshake. They gate
*protocol correctness*, not *authorization*.

## Honest scope / caveats

- **Static proof.** Method = Binary Ninja call-graph traversal + decompilation of every
  hop (`sub_805df38`, `sub_805dd04`, `sub_80ec036`, `sub_805e9c8`, `sub_805e5fc`,
  `sub_801de58`, `sub_801d81c`, `sub_805e124`, `sub_80aa368`). I did **not** dynamically
  execute the full `connect → CoC-open → channel_data` sequence through the real `sl_bt`
  radio stack in Renode (radio init wedges at `b .` @0x808105c — the reason the dynamic
  overflow/hijack PoCs inject at the handler and pre-seed state). The auth conclusion does
  not depend on execution: it is the *absence* of any auth branch on the path.
- **SPSM literal:** read as `0x0080` from `aliro_l2cap_transfer_create` (`descriptor+0x16`).
  Worth confirming against the Aliro spec's SPSM, but its exact value does not affect the
  pre-auth conclusion (it is a constant, not a secret).
- `*0x20024074` writer not located via xref (likely `.data`/init-set); treated as a
  boot-time readiness flag. It is read, not attacker-controlled, and non-zero on a normally
  running lock.

## Bottom line for the vendor report

The reassembly heap overflow (CWE-787) is reachable by **any BLE peer in radio range that
connects and opens an L2CAP CoC channel on the Aliro SPSM — with no pairing, bonding, or
encryption.** Combined with `ALIRO_UNLOCK_REACHABILITY_POC.md` (overflow → corrupt
`transfer+0x04` vtable ptr → firmware `blx` → PC = door-unlock `sub_80714f4`), this is a
**pre-auth remote memory-corruption with a demonstrated control-flow path to the
security-critical unlock routine** — not a post-auth bug and not a mere DoS.

The one part that is layout-dependent (not auth-dependent) remains the real-device **heap
groom** placing the descriptor after the SDU buffer — the same caveat noted in
`ALIRO_UNLOCK_REACHABILITY_POC.md` §4.
