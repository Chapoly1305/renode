# ZB-06 — Heiman HS1SA smoke detector (stack → saved-LR + PC hijack → full RCE)

| | |
|---|---|
| Finding | ZB-06 |
| Vendor / model | Heiman HS1SA smoke detector |
| Chip / board | EFR32MG21 (Cortex-M33) · `brd4180a.repl` |
| ELF | `hs1sa.elf` (present in this folder; git-ignored — also in `scripts/silabs-vuln/`) |
| Class | stack overflow → saved-LR overwrite + PC hijack (full 4-byte control) |
| Sink | `sub_1662e` (mfg `0x120B` / cmdId `0xF3`) |
| Gate | `sub_7D46` (ZCL cmd dispatch) → `sub_7B5A` → `sub_a33c` gate (mfgCode 0x120B && dir 0 && cmdId 0xF3) → tail-call `sub_1662e` |
| Highest result | **full working RCE**; also the exemplar for dispatch injection, APS routing, and real-radio reachability |

## The bug

`sub_1662e` uses `payload[0x0a]` as an **unclamped** loop bound and writes halfword
records into a 32-byte stack buffer at SP+0x18; index 34 lands on the saved LR
(SP+0x5c), so count ≥ 36 overwrites the saved return address — full 4-byte control.

## What was validated (this device is documented end-to-end)

| Level | Script | Result |
|---|---|---|
| function-level | `zb06_validate.resc` | saved-LR → `0x20006E01`; epilogue `pop` → PC = `0x20006E00` |
| dispatch injection | `zb06_dispatch_inject.resc` | plaintext mfg-0x120B/0xF3 frame at `sub_7D46`; `7D46→7B5A→a33c gate→1662e` all natural; saved-LR `0x2000FFB4` → `0x20006E01` |
| **APS endpoint routing** | `zb06_aps_route.resc` | enters one layer higher at APS parser `sub_E376`; firmware's own endpoint/cluster/profile search matches an installed endpoint record and dispatches to the sink's own epilogue — no forced PC |
| real-radio reachability | `zb06_ota_joined.resc` | a **real** injected 802.15.4 frame reaches the firmware's MAC receive ISR (blocked above MAC by NWK/APS crypto) |
| **full RCE** | `zb06_rce.resc` | in-frame shellcode executes; magic `0x20017000` `0xDEADBEEF`→`0xC0DE1337` |

### Layered reachability (what is / isn't crossed)

| Layer | Status |
|---|---|
| PHY / FRC / MAC receive (real radio-injected frame) | ✅ `zb06_ota_joined.resc` |
| **NWK/APS decryption + network-key membership** | ❌ the documented exploitability precondition |
| APS endpoint/cluster-affinity routing | ✅ `zb06_aps_route.resc` (endpoint record installed = joined-device state) |
| ZCL command dispatch → gate `sub_a33c` | ✅ natural |
| sink `sub_1662e` (unclamped count → saved-LR) | ✅ natural |

The only layer not crossed dynamically is NWK/APS crypto + membership — exactly
the precondition the report states ("requires network-key membership").

### RCE

The frame carries the 20-byte Cortex-M shellcode; the firmware's own overflow loop
copies it to `0x2000FF70` and overwrites the saved LR with `0x2000FF71`
(entry|Thumb); the genuine epilogue `ldmia sp!,{r4-r11,pc}` @`0x16736` loads PC
from the corrupted slot and the shellcode runs. Buffer index 0–33 (68 bytes) is
usable for shellcode; index 34+ is the saved LR; the sentinel checks
(`payload[8]<5`, `payload[r7+9]<0xf1`) sit outside the record region, so the
shellcode bytes pass freely.

## The tlib bug this device surfaced (verified, kept in-fork)

The unmodified HS1SA firmware is in a **~1.4 ms reset loop** on stock Renode: at
`0x266A4` the image has `EA4F 000D` = `MOV.W R0, SP` (Thumb-2 T3, Rm=SP), which
tlib's Cortex-M33 wrongly rejected as UNDEFINSTR → the firmware's fault handler
`SYSRESETREQ`s. This is real Renode behaviour (matches upstream issue #884; ARM
ARM DDI0403E.b confirms the encoding is legal), fixed in our fork (MVE-gated
decoder change in `translate.c`). After the fix the firmware's own MAC brings the
radio up to RxSearch and the real-radio probe above works. See
`../../../scripts/silabs-vuln/VALIDATION_REPORT.md` "Emulator findings".

## Reproduce

```
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/<script>.resc"
```

| Script | Success signal |
|---|---|
| `zb06_validate.resc` | epilogue `pop` → PC = `0x20006E00` |
| `zb06_dispatch_inject.resc` | `MARK_7D46…→MARK_1662E_SINK`; saved-LR `0x2000FFB4` = `0x20006E01` |
| `zb06_aps_route.resc` | `MARK_E504_SEARCH_MATCH → … → MARK_16736_SINK_RET`; saved-LR `0x2000FF6C` = `0x20006E01` |
| `zb06_ota_joined.resc` | real frame delivered to MAC receive ISR (radio log) |
| `zb06_rce.resc` | magic `0x20017000` = `0xC0DE1337` |

## Forced vs natural

The single injection entry (`sub_7D46`, or `sub_E376` for the APS variant) with
the stack's register contract bypasses NWK/APS crypto = joined-device model.
`zb06_aps_route.resc` installs one endpoint record (`cluster=0x0104,
profile=0x0000, endpoint=0xF3`) — models a joined device's runtime-registered
endpoint. Everything below the entry runs on the firmware's own code with no
forced PC. Shellcode is carried in the frame.

## Files
- `scripts/hs1sa.resc` — boot harness for this image.
- `scripts/zb06_validate.resc` — function-level sink + PC hijack.
- `scripts/zb06_dispatch_inject.resc` — dispatch-injection reachability.
- `scripts/zb06_aps_route.resc` — APS endpoint-routing crossing.
- `scripts/zb06_ota_joined.resc` — real-radio probe (frame reaches MAC). The
  superseded first probe `zb06_ota.resc` lives in `scripts/silabs-vuln/`.
- `scripts/zb06_rce.resc` — full RCE (shellcode exec).
- `ZB-06_VALIDATION.md` — original detailed methodology writeup.
- ELF `hs1sa.elf` — present in this folder; git-ignored (not committed). Also in `scripts/silabs-vuln/`.
- OTA `HS1SA-E-PLUS-120BA080-20260311-Release.ota` — native Zigbee OTA distribution
  image (git-ignored); the ELF above is its extracted executable. Source:
  `zigbeeFirmware.zip` (koenkk mirror). **Naming note:** the corpus labels this
  image "LEDVANCE" (rebrand of the same Heiman HS1SA `120BA080` 20260311 release);
  it is byte-identical to the validated ELF (sha256 `aed8883f…`).
