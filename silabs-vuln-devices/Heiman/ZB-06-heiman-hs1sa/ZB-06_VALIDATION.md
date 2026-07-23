# ZB-06 (Heiman HS1SA) — Emulation-Based Validation

**Date:** 2026-07-13
**Platform:** EFR32MG21 (BRD4180A) on the SiliconLabsSoftware Renode fork + MG21 support added this session.
**Firmware:** `hs1sa.elf` — real, unmodified Heiman HS1SA-E-PLUS application image
(`HS1SA-E-PLUS-120BA080-20260311-Release`), ARM Thumb-2, stripped.
**Reproduce:** `./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/zb06_validate.resc; quit"`

## Claim under test

Manufacturer-specific ZCL command handler `sub_1662e` reads an unbounded record
count and overflows a 32-byte stack array, overwriting the saved return address
→ control-flow hijack from a single on-network mfg-ZCL frame.

## What the emulation proves

Running the **real firmware bytes** on the emulated MG21:

| Stage | Observation | Result |
|---|---|---|
| Before | saved-LR slot `0x20006FFC` = `0x00000000` | baseline |
| After overflow | saved-LR slot `0x20006FFC` = **`0x20006E01`** | attacker value written OOB ✓ |
| After epilogue `pop {..,pc}` | PC = **`0x20006E00`** | PC hijacked to attacker landing pad ✓ |

The attacker value `0x20006E01` is carried in payload records 34/35
(`payload[0x0b+2i]/payload[0x0c+2i]`); `0x20006E00` is the Thumb target (bit0 cleared).

## Mechanism (confirmed against real disassembly)

- Prologue `0x1662e`: `push {r3-r11,lr}` then `sub sp,#0x38`. Saved LR at top of frame.
- Buffer `v20 = SP+0x18` (32 bytes). Write loop `0x16688`: `strh.w r0,[SP+0x18, r10<<1]`,
  `r10 = 0 .. count-1`.
- **count = `payload[0x0a]`** — loaded unclamped at `0x166f6 ldrb.w r8,[r0,#0xa]`.
- Saved LR at `SP+0x5c` = buffer + 0x44 = **halfword index 34** ⇒ count ≥ 36 fully
  overwrites the return address. Test used count = 40.
- Gate `sub_a33c`: `if (mfgCode==0x120B && dir==0 && cmdId==0xF3) tail-call
  sub_1662e(r0=payload, r1=remaining_len)`.

## Method & honesty notes

- The harness invokes `sub_1662e` **directly** with the exact register contract the
  gate `sub_a33c` uses — this isolates the vulnerable primitive without needing a
  full Zigbee network join (network-key association + APS/ZCL dispatch), which the
  emulated radio stack does not yet drive end-to-end.
- The OOB write is observed *as it happens* (step into the write loop, read the slot).
- The PC hijack is observed by executing the **genuine** `pop {r4-r11,pc}` epilogue at
  `0x16736`. SP is repositioned to the corrupted frame for that step because this
  particular crafted record stream keeps `sub_1662e`'s per-record parser loop busy
  (records never hit the parser's completion path), so the function does not reach
  its own epilogue unaided. The memory-safety violation (saved-LR overwrite) has
  already occurred before that point and is independently shown.
- Not yet demonstrated: reaching `sub_1662e` through the live over-the-air radio path
  (join → APS → ZCL dispatch → gate). That is the remaining step for a fully
  end-to-end over-the-air PoC.
