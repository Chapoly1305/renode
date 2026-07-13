# Silicon Labs EFR32 — Emulation-Based Vulnerability Validation

All Silicon Labs findings in the `vuln-reports` corpus were validated dynamically
by loading the **real, unmodified firmware** on an emulated EFR32 in Renode and
driving the vulnerable function to observe the memory-safety violation actually
occur (out-of-bounds write / saved-return-address overwrite / PC hijack).

**Date:** 2026-07-13 · **Renode:** 1.16.0 (SiliconLabsSoftware fork, .NET build)

## Platforms built for this work

| Chip | Board repl | Notes |
|---|---|---|
| EFR32MG21 (Series 2, Cortex-M33) | `platforms/boards/silabs/brd4180a.repl` | **new this work** — MG21 was previously unsupported (`efr32xG21.repl` + `DeviceFamily.EFR32MG21`) |
| EFR32MG13 (Series 1, Cortex-M4) | `platforms/boards/silabs/brd4162a.repl` | pre-existing |
| EFR32MG24 (Series 2, Cortex-M33) | `platforms/boards/silabs/brd4186c.repl` | pre-existing |

All 11 firmware images load and execute on these platforms. (The function-level
validations below force PC into the vulnerable function and do not depend on a
stable full boot — which matters because two emulator-fidelity gaps, documented
under "Emulator findings" below, otherwise prevent these real firmwares from
completing boot.)

## Results — 11/11 CONFIRMED

| ID | Device | Chip | Function | Class | Observed result |
|---|---|---|---|---|---|
| ZB-02 | Aqara pet feeder | MG21 | `sub_14D08` (0xFCC0 MIoT) | heap OOB write | attacker offset → write at base+65476 (`0x20014FC4`) = `0x41414141` |
| ZB-03 | Innr plug | MG21 | `sub_22E06` (mfg 0x8004) | stack → saved-LR | saved-LR `0x20006FFC` → `0x20006E01` (full control) |
| ZB-04 | Aqara switch n0agl1 | **MG13** | `sub_5014` (0xFCC0) | constrained stack overflow | saved R4/R5/R6 = `0xAAAAAAAA`; saved-LR = `0xDEADBEAA` (1 byte, 82B-cap bounded) |
| ZB-05 | Niko switchx2 | MG21 | `sub_D290` (0xFC00 WriteAttr) | stack → saved-LR | saved-LR `0x2000FFFC` → `0x20005001` (full control) |
| ZB-06 | Heiman HS1SA | MG21 | `sub_1662e` (mfg 0x120B/0xF3) | stack → saved-LR + PC hijack | saved-LR → `0x20006E01`; epilogue `pop` → PC = `0x20006E00` |
| MT-01 | Tuya 4701 pid79 | MG24 | `sub_804F3DC` (OTA MCU parse) | stack → own saved-LR | saved-LR `0x2000FFFC` → `0x20006E01`; 56 bytes consumed |
| MT-02 | Tuya 4701 pid436 | MG24 | `sub_80B64A8` (mfg 0x125DFC60 TLV) | unbounded memcpy → saved-LR | sentinel at real 0x454 distance → `0x20006E01` |
| MT-03 | Heiman 4619 | MG24 | `sub_8008E9C` (heimanMfgMsg) | stack → saved-LR | saved-LR `0x2000FFFC` → `0x20006E01` |
| MT-04 | Leedarson thermostat 4456 | MG24 | `sub_800E1D0` (SetWeeklySchedule) | stack OOB → saved-LR | saved-LR `0x200103CC` → `0xEEEEBEEF` (low half = attacker transitionTime); R5=13 boundary contrast verified |
| MT-05 | Heiman Matter 4619/4101 | MG24 | `sub_8009224` (heimanMfgMsg 0xF000) | stack → saved-LR + **PC hijack** | saved-return slot → `0x08007001`; epilogue `pop` → **PC = `0x08007000`** |
| MT-08 | Tuya door lock 4701/3310 | MG24 | `sub_8016194`→`sub_804A1BC` (mfg 0x125DFC32) | **heap** OOB write | sentinel past 48B heap buffer (`+48`) → `0xDEADBEEF` (real handler + direct-sink) |

## Method

For each device the vulnerable function is invoked **directly** with the exact
register/argument contract its gate/dispatcher uses (verified against
`llvm-objdump` disassembly of the stripped image). Renode then single-steps to
the sink and the target memory slot is read to observe the overwrite. Where the
epilogue is reachable without faulting (ZB-06, MT-05) the `pop {..,pc}` is
executed to demonstrate the actual PC hijack.

This isolates the memory-safety primitive on real firmware bytes without needing
the full network stack (Zigbee join / Matter CASE session). What is **forced**
vs. driven naturally is documented in each `*_validate.resc` header — notably
MT-04, where the TLV iterator's live-heap/object-graph dependency means the loop
index is forced (the store instructions, EA arithmetic, and frame geometry are
all real and unmodified).

## Scope of the claim — what is and is NOT proven

The results above prove the **memory-safety primitive** (the sink) on real
firmware: given the documented arguments, the vulnerable function performs the
out-of-bounds write / saved-LR overwrite / PC hijack. This is the CWE at the
instruction level, verified dynamically.

It does **not** by itself prove **over-the-air reachability** — that a real
radio frame received by the un-modified firmware propagates through the
NWK/APS/ZCL dispatch and actually calls the sink with attacker-controlled
arguments. Function-level validation forces entry, bypassing that question.
Reachability is the axis where the corpus itself has produced false positives
(ZB-01/07/08/09 were retracted largely for unconfirmed reachability). The OTA
probe below tests it directly.

## Over-the-air reachability probe (ZB-06, representative)

`zb06_ota.resc` injects a **real** 802.15.4 frame carrying the mfg-0x120B/0xF3
ZCL command into the booted HS1SA firmware's radio (`radio.ReceiveFrame`,
byte-array literal with comma separators; the `IRadio sender` may be the radio
itself), with PC hooks on every chain node
(`sub_C22C→sub_E376→sub_7D46→sub_7B5A→sub_a33c→sub_1662e`), and runs the
firmware's own code — no forced PC.

**Result: OTA reachability is PARTIAL — proven up through the firmware's MAC
receive path from a REAL injected frame (no forced PC), blocked above the MAC.**
This took two passes; the first pass's conclusion was wrong and is corrected here.

### Renode Cortex-M33 fidelity bug found along the way (verified)

The un-modified HS1SA firmware is actually in a **~1.4 ms reset loop**, not
booting cleanly. Pinpointed cause: at **`0x266A4`** the image has
`EA4F 000D` = `MOV.W R0, SP` (Thumb-2 T3 encoding with Rm=SP). Renode's tlib
Cortex-M33 rejects it as UNDEFINSTR; the firmware's fault handler (`0x2882C`)
then `SYSRESETREQ`s. Verified empirically: in 50 ms, PC hits `0x266A4` **36×**
and the fault handler `0x2882C` **36×** (= 36 reboots). Real Cortex-M33 hardware
executes this encoding (equivalent to the 16-bit `MOV R0,SP`); it is the **only**
`MOV.W Rd,SP` in the whole image. This is an upstream-worthy tlib bug that
affects any MG21/MG24 firmware using that encoding — **not** a property of the
device.

The earlier "radio never leaves Off / clean boot / RX-never-enabled" conclusion
was an **artifact of this reset loop** (PC sampling kept landing on recurring
boot addresses, looking valid). Corrected below.

### After patching that one instruction (0x266A4 → 16-bit MOV+NOP)

The firmware's **own MAC brings the radio up to RxSearch by itself** — verified:
patched boot shows `RxWarm`/`RxSearch` transitions (`Off→RxWarm→RxSearch→
RxPoweringDown→Off`, ~every 25 ms for network/energy scan); un-patched shows
**0**. So RX opening is gated by the MAC **scan**, not by `networkState==JOINED`.

A **real** injected mfg-0x120B/0xF3 frame (via `radio.ReceiveFrame`, delivered
while in RxSearch — no forced PC) then propagates:

| Layer | Reached |
|---|---|
| PHY (sync 0xA7 matched, CRC, not dropped) | ✅ |
| FRC RXDONE → FrameControllerIRQ nvic@34 | ✅ |
| firmware MAC receive ISR (ACKs the frame: RxFrame→Rx2Tx) | ✅ |
| ZCL chain `sub_1CC4E→sub_C22C→…→sub_1662e` | ❌ (0 markers) |

The radio-injected frame reaches the MAC but not ZCL dispatch (no established
network → NWK/APS drops it, and no stable stack tick). To test the *next* layer
— whether the firmware's own ZCL dispatch actually routes such a frame to the
sink — a separate injection was done one layer up (below).

### ZCL-dispatch injection — closes the two false-positive axes (`zb06_dispatch_inject.resc`)

Function-level validation forces the sink's arguments, so it skips **gate
reachability** and **parameter fidelity** — the two axes where the corpus's
retracted findings actually failed (dead-code handler; wrong handler). To test
them directly, an **already-decrypted plaintext** mfg-0x120B/0xF3 ZCL frame is
injected at the firmware's ZCL command dispatcher (`sub_7D46`) with the register
contract `sub_C22C`/`sub_E376` would pass — then the firmware's **own code** runs
with **no forced PC** into the parser, gate, or sink. Result (reproduced):

```
MARK_7D46_ZCLDISP → MARK_7B5A → MARK_A33C_GATE → MARK_1662E_SINK   (natural order)
saved-LR slot @0x2000FFB4:  0xAABBCCDD  →  0x20006E01
```

- **Gate reachability ✅ NATURAL** — the firmware's `sub_7cc0` classified the
  bytes as a manufacturer-specific ZCL command (mfgCode 0x120B, cmdId 0xF3),
  `sub_7B5A` read the parsed struct and called the gate `sub_a33c`, which matched
  and tail-called the sink — none of it with a forced PC.
- **Parameter fidelity ✅** — `payload[0x0a]=40` was used **unclamped** as the
  write-loop bound, so records 34/35 overwrote the sink's saved return address
  with the attacker value via the natural path.

**Boundary (honest):** this bypasses NWK/APS decryption + the APS endpoint/cluster
affinity routing. Entry at the higher `sub_E376` parser was probed and passes
every ZCL field check, but returns undispatched at an **endpoint/cluster affinity
table search** (`0xe4c6`–`0xe502`, table @`0x000396D4`): the frame's APS
cluster/profile/endpoint must match a registered endpoint — an APS-routing
precondition **above** ZCL command dispatch. So this proves *"a frame that
reaches ZCL command dispatch is routed to the sink with attacker-controlled
params"*, **not** the full crypto-authenticated, APS-routed OTA path (which still
needs network-key membership + a matching endpoint).

### Honest exploitability verdict (all Zigbee findings)

The sink is real (function-level proven) and a real received frame reaches the
firmware's MAC layer, but the ZCL dispatch chain is **not** reached, for two
compounding reasons:
1. **Un-commissioned device** — this ELF has no NVM3/token storage and no
   network key/parent, so NWK/APS discards the frame before ZCL dispatch. This
   is the documented exploitability boundary: **the attacker needs network-key
   membership** (every ZB report says so).
2. **No stable main loop** — NWK/APS/ZCL dispatch is deferred to the stack tick,
   which this emulation can't sustain: after the `0x266A4` fix the next fault is
   a call through the absent Gecko bootloader vector table (`BX 0` →
   INVSTATE @`0x2AD46`), plus the unemulated Secure-Element mailbox
   (`0x40094000`–`0x40096000`). Fully closing the gap needs the bootloader image
   + SE-mailbox emulation, then commissioning (or plaintext injection at the
   `sub_C22C` input queue, which would only prove parser reachability, not the
   crypto-authenticated path).

The Matter findings (MT-xx) carry the analogous precondition of an established
CASE fabric session.

**What was forced vs natural:** forced = the one-instruction emulator fix and
holding the RxSearch window for injection timing. Natural = radio bring-up,
RxSearch entry, PHY frame acceptance, and the MAC ISR/ACK.

Harness: `zb06_ota_joined.resc`. Chains: `OTA_REACHABILITY_CHAINS.md`.

## Real SDK firmware (full Zigbee stack, for a future end-to-end OTA test)

To get past the "un-provisioned application ELF" limitation, the complete
Silicon Labs **Zigbee 4.0 Light** SDK project (part `EFR32MG21A020F1024IM32`,
board brd4180a) was **built from source** with the system `arm-none-eabi-gcc
9.3.1` → `z4light.elf` (`__Vectors=0x4000`). This firmware has a CLI
(`network-creator`/`network-steering`) and runs the full stack, so it *can*
form/join a network and open RX for real — the vehicle for a genuine end-to-end
OTA PoC once the radio model supports RAIL bring-up.

Build notes (`z4light_boot.resc` header has the full recipe): the shipped stack
libs (`libzigbee-*.a`, `librail_*.a`) are **fat LTO objects** built with a much
newer GCC (LTO bytecode v8926), so `-flto` must be disabled (GCC 9 links their
real machine code fine); and binutils-2.34 rejects the `(READONLY)` linker-script
attribute (removed). No newer GCC is required just to build/link.

Boot status on the MG21 platform: runs real reset/CRT/CMU code, passes HFXO
clock init (after the HFXO fix below), reaches RAIL radio init (~`0x24dce`) and
busy-waits on an FRC DMA-completion handshake the radio model does not yet drive.
Reaching the CLI needs radio-model RAIL bring-up (open-ended); tracked separately.

## Emulator findings (real Renode/tlib gaps surfaced by real firmware)

Driving real firmware surfaced three genuine emulator gaps (distinct from the
vulns; all upstream-worthy):

1. **tlib Cortex-M33 rejected `MOV.W Rd,SP` (Thumb-2 T3)** — FIXED at source.
   Encoding `EA4F 000D` at `0x266A4` in the HS1SA image was treated as
   UNDEFINSTR; real HW executes it. Caused a ~1.4 ms reset loop (36 reboots /
   50 ms). Root cause: the data-proc shifted-register wide-shift decode excluded
   `rm==0xd` (SP) because those encodings are ARMv8.1-M **MVE** long-shifts —
   but MVE only exists on M55/M85, not M33. Fixed in
   `tlib/arch/arm/translate.c` by gating the exclusion on `ENABLE_ARCH_MVE`
   (commit on the Chapoly1305/tlib fork). Verified: fault drops 36×→2× (the 2
   are the downstream bootloader/SE issues below), and the **unpatched**
   firmware's own MAC now opens RX (RxWarm/RxSearch transitions appear). The
   per-image patch in `zb06_ota_joined.resc` is no longer required.
2. **`SiLabs_HFXO_2` never released `FSMLOCK` on MG21** — FIXED. MG21 has no HFXO
   `MANUALOVERRIDE` command; its `CMU_HFXOInit()` sets `DISONDEMAND` and waits for
   `STATUS.FSMLOCK` to clear, which the model only did in the (MG22-only)
   MANUALOVERRIDE path → infinite spin. Fixed with an opt-in constructor flag
   `releaseFsmLockOnDisableOnDemand` (default false → MG22 byte-identical),
   enabled from `efr32xG21.repl`. Committed to renode-infrastructure.
3. **Radio model doesn't drive RAIL bring-up handshakes** (FRC DMA-completion
   flag, synth lock, RAC transitions, sequencer image) — blocks full-stack
   firmware boot. Open-ended; not addressed here.

## Files

- `boot_mg21.resc`, `boot_mg24.resc` — generic boot harnesses (`$bin`, `$vtor`).
- `zb0{2,3,4,5,6}_validate.resc`, `mt0{1,2,3,4,5,8}_validate.resc` — per-device
  validation harnesses. `mt08_validateA.resc` drives the real MT-08 handler
  end-to-end (in addition to the direct-sink `mt08_validate.resc`).
- `ZB-06_VALIDATION.md` — detailed writeup of the ZB-06 methodology.
- `zb06_ota.resc` — first over-the-air reachability probe (injects a real frame,
  no forced PC; superseded by the joined variant below).
- `zb06_ota_joined.resc` — OTA probe with the `0x266A4` emulator patch; the
  firmware's own MAC opens RX and a real frame reaches the MAC layer.
- `z4light_boot.resc` — boots the from-source-built full-stack Zigbee firmware
  (`z4light.elf`) with build recipe in its header.
- `OTA_REACHABILITY_CHAINS.md` — source→sink chains + the network-membership
  precondition for all four Zigbee findings.
- Firmware ELFs (`*.elf`) are **git-ignored** (not redistributed).

## Reproduce

```
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/<device>_validate.resc; quit"
```
