# U400 natural-boot bring-up — findings (toward pairing + Thread)

Goal: boot **stock Aqara U400** firmware in Renode far enough to complete Matter
commissioning (CHIPoBLE) + Thread onboarding, mirroring the stock SiLabs Matter E2E.

## What is PROVEN working
- Firmware **boots into RTOS multitasking** — PC roams widely (`0x8128xxx` memcpy,
  `0x80d3xxx` l2cap, `0x8018xxx` timer mgr), not stuck. This is already **past** the
  `aliro-reassembly-audit` HANDOFF's documented "natural boot not achievable" ceiling.
- Crypto subsystem alive: `semailbox` serves `Random` (RNG) requests.
- `chip::Platform::MemoryInit()` completed (`*0x200216ec == 1`) — CHIP heap is up.

## The boot wall sequence (each bypass reveals the next)
1. `0x808105c` — `b .` spin after `bl sub_80e3d60` (radio-PA-config) returns failure.
   Driven by an **86×/60s periodic retry** (radio init keeps failing & retrying).
2. Forcing `sub_80e3d60 -> return 0` (patch `0x80e3d60 = 2000 4770`) **stops the retry
   (86→1)** and advances boot to:
3. `0x80fd44e/45a/468` — cluster of `cbz r0,+4; b .` subsystem-init asserts.
4. `0x803e736/738/748/758` — more assert traps.
5. `0x8032xxx` (`sub_803266c`) — a **RAC/RAIL radio state-machine poll loop**
   (`tst` on radio status bits 0x200/0x400/0x80/0x40, writes `[r0+0x34]`,`[r0+0x3c]`),
   spinning because the modeled radio never reaches the polled state.

NOTE: NOPing genuine `0xE7FE` self-spins is safe; NOPing arbitrary back-edge branches
(the poll loops) **corrupts control flow and core-dumps** (e.g. `0x80d4904`). Do NOT
blindly NOP poll loops.

## Root-cause chain of the first wall (fully traced, read-only)
```
0x8081056 bl sub_80e3d60           radio-PA-config
  -> sub_80cec04(ctx,cfg,...)
     -> sub_80d2f6c(...)
        -> sub_8017240 -> handle (OK, returns 0)
        -> reads *(handle+0x24)  (PA-config object ptr)
           == 0 -> fallback sub_80d2be4:
              *(handle+0x24) = sub_801671c()          // = (*0x20001530)()  fn-ptr
                 -> sub_80e3710 -> sub_808775c -> sub_80ce9ac
                    -> chip::Platform::MemoryAlloc (guarded by MemoryInit flag @0x200216ec)
```
Function-ptr slots are all populated at runtime (alloc `*0x20001530=0x080E3710`,
lock/unlock `*0x2000153c/38`). So the machinery is initialized; the config still fails —
consistent with the radio config depending on **RF calibration data the model returns as 0**.

## Emulator gap inventory (unmodeled reads/regs U400 touches; stock Matter fw does NOT)
- `deviceInformation` unmodeled reads: **0x0, 0x248, 0x24C, 0x260** (RF cal tokens; near
  the modeled ThermistorCalibration=0x25C / FenotchCalibration=0x264).
- `radio` unmodeled write bits: 0x8,0x14,0x20,0x48,0x4C,0x50,0x54,0x6C,0x74,0x84,0x100
  (mostly benign — model warns but accepts).
- `DCDC` (0x50094xxx): partially modeled (a PythonPeripheral at 0x50094028 only).
- `SMU_S` (0x4400Axxx / 0x44008xxx): **not modeled** (non-existing-peripheral reads=0).

## Why this is the real blocker (not asserts)
The stock SiLabs Matter lighting-example uses **compile-time PA curves**
(`RAIL_InitTxPowerCurvesAlt(&RAIL_TxPowerCurvesDcdc)`, a const flash table) and a subset
of RAC/RAIL the model implements — so it advertises. **U400 drives runtime RF calibration
+ a fuller RAC/RAIL state machine** that this Renode fork's EFR32 model doesn't implement.
No amount of firmware-side NOP/hook makes the *modeled radio actually transmit* U400's
BLE/15.4 frames; the model must be extended. The BleCentralBridge + Ieee802154HostBridge
(and the radio TX/RX the stock fw exercised) DO work — the gap is only U400's radio *init*.

## ★ PIVOTAL DISCOVERY — the walls are DOOR-LOCK HARDWARE, not (just) radio
Wall #5's poll loop `sub_803266c` drives base addresses **0x5B000000 = I2C0_NS** and
**0x50068000 = I2C1_NS** — it is an **I2C transfer driver** (polls I2C IF flags at `+0x3c`,
writes TXDATA at `+0x34`, scatter-gathers a ptr/len list through a 0→8 transfer state
machine). Renode only **Tag**s I2C0/I2C1 (no model) → status reads return 0 → "transfer
complete" never asserts → the driver spins forever.

Implication: **U400 is a physical door lock.** Its boot is gated on talking to the lock's
on-board hardware over I2C (motor controller, position/hall sensors, secure element,
likely the Aliro NFC reader), plus DCDC/SMU. None of that hardware exists in emulation.
The stock Matter *lighting* example boots because a lighting dev-board has almost no
peripherals; a lock is a full electromechanical device. So "extend the radio model" is
**necessary but far from sufficient** — reaching CHIPoBLE advertising would require
stubbing/modeling the entire door-lock peripheral suite the boot path touches (an
open-ended, product-specific effort, most of it unrelated to Matter/BLE), and feeding
plausible sensor responses the firmware won't reject.

Honest verdict: **natural boot → pairing → Thread for U400 is not practically reachable**
in this emulator without emulating the lock's physical hardware bus-by-bus. The
dispatch-injection approach (already validated, in scenarios/) remains the right tool for
the security goal — pairing was never a prerequisite for the vuln validation.

## Remaining work (option 1 — extend the EFR32 radio model) — NOTE: insufficient alone, see PIVOTAL DISCOVERY above
1. Model `deviceInformation` 0x248/0x24C/0x260 + any polled RF-cal tokens (plausible
   non-zero values) so radio-PA-config succeeds without the retry.
2. Implement/advance the RAC/RAIL state transitions `sub_803266c` polls (radio status
   bits) so the poll loops exit — reuse the state machine the stock path already drives.
3. Add an `SMU_S` stub (RAM/benign) at 0x44008000/0x4400A000; extend DCDC coverage.
4. Rebuild (`./build.sh --net --skip-fetch`); iterate boot → CHIPoBLE advertising.
5. Commission via BleCentralBridge + chip-tool; then Thread onboarding via the 15.4 host
   bridge (same harness as the stock E2E in matter/renode-thread/).

This is multi-day emulator engineering (no factory calibration values in hand; RAC/RAIL
semantics must be authored). The diagnosis above is the running start.

## Safe knobs discovered
- `sysbus WriteWord 0x80e3d60 0x2000` + `0x80e3d62 0x4770` — force radio-PA-config success
  (stops the 86× retry, advances boot). SAFE.
- NOP `0xE7FE` self-spin asserts as they appear. SAFE.
- Do NOT NOP poll-loop back-edges (crashes).

## Fuzzware attempt (automated rehosting) — 2026-07-21
Tried Fuzzware (installed: ~/.local/bin/fuzzware, venv at ~/matterSensorWorkspace/fuzzware/venv)
to auto-model peripherals and push boot forward. Setup in `fuzzware/`:
- Extracted flat binary (ELF has no sections): `U400.bin` @ 0x08006000, entry 0x08019d44, SP 0x20001200.
- Wrote `config.yml` (memory_map text/ram/mmio/nvic/devinfo + mmio_high for radio 0xA8000000).

Result: `fuzzware emu` aborts in the discovery-fork ("Could not retrieve the number of required
ticks") — the child dies during early startup BEFORE the first fuzzed MMIO read. A standalone
Unicorn probe (fuzzware venv unicorn, `fuzzware/uprobe.py`) localizes it: crash at **0x080cae12
`blx r3` in __libc_init_array with r3=0** — the C++ ctor table @0x20003288 is all-zeros, and the
firmware ENABLES THE FPU in SystemInit (0x8019da8: CPACR |= 0xF00000 @0x8019db0). Enabling VFP in
Unicorn lets it run 563k blocks but it still ends at the same init_array crash with the table
un-populated — i.e. the zero-returning MMIO in a naive probe derails the hardware-poll loops and
corrupts control flow before/around the .data copy.

CONCLUSION: Fuzzware *can* rehost U400, but — exactly like the stock SiLabs target already in
`~/matterSensorWorkspace/fuzzware/projects/matter-silabs-lighting-example` (which needed **~30
hand-written skip-handlers** for CMU/HFXO/DCDC/sysrtc/sleeptimer/power-mgr/uart/etc.) — a NEW
firmware needs its own per-target skip-handler set, added iteratively as early crashes surface.
That is standard Fuzzware target bring-up, not push-button. Its payoff is that once running it
AUTO-models the door-lock peripherals (I2C sensors/motor/SE) that stalled the Renode path — but
the output is **coverage fuzzing for the security goal, NOT live Matter pairing** (pairing needs a
live BLE/Thread peer + real crypto, which no single-firmware rehoster provides).

Stock target's handler methodology (template for U400): copy from
`projects/matter-silabs-lighting-example/config.yml` handlers block; find U400's analogous SDK
functions (same Simplicity SDK 2024.6.0 function set, different addresses) and add do_return/return_0x0.
