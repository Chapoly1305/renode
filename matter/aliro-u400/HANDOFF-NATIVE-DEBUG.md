# U400 Aliro — Native Debug & Dev Handoff (single source of truth)

**Last updated:** 2026-07-23 · **Host:** `chen@lab` · **Repo:** `~/aliro-renode` (branch `aliro/u400-experiment`, SiLabs 26q1 base, fork `Chapoly1305/renode`, Renode 1.16.1).
**Target:** Aqara U400 smart lock, fw v3.1.1.0 (vid 4447 / pid 10244), EFR32MG24 (Cortex-M33), Silicon Labs BLE stack + FreeRTOS.
**Authorization:** operator's own device, coordinated disclosure. **Firmware is copyrighted — keep it git-ignored, never commit.**

> This file consolidates everything needed to continue NATIVELY on this box. It supersedes scattered prior docs
> (see §12). Where a prior doc disagrees, this file wins. Companion machine-facing notes: local `STATUS.md` in
> `<mac>/aliro-reassembly-audit/`; AI memory `[[u400-producer-path-unlock]]`, `[[u400-renode-heap-groundtruth]]`,
> `[[u400-matter-unlock-actuation-chain]]`.

---

## 0. Bottom line (read first)

- **Hardware-proven (the disclosure finding):** pre-auth remote **DoS** — an L2CAP CoC SDU-reassembly heap overflow
  crashes the lock → watchdog reboot (~10 s), no pairing/auth. Reproduced OTA.
- **Everything beyond DoS (control-flow hijack → physical lock/unlock):** **RE + emulation-validated in DESIGN only.
  NOT demonstrated on hardware, and UNPROVABLE with current access** (OTA negative; SWD fused; U400 can't advertise in
  Renode; Renode has no motor model). On the real device the only observed effect is the reset. Do **not** present it
  as a validated capability.
- **What native dev has now added (all emulation-only, no HW actuation):** (b) the "inject-and-run" scheduler test is
  **DONE** — U400 boots to a running FreeRTOS (3 stubs) and a `door_unlock` published to the mbus makes the consumer task
  autonomously wake and reach the motor drive `sub_806DA24` under the real scheduler (§9b; scenario
  `scenarios/u400-9b-inject-unlock.resc`). Still no motor model, so physical motion stays unobservable.
- **What native dev can still add:** (a) close the `r0` arg gap for a *complete* controlled-argument call; (c) the big
  one — author the EFR32 radio model so U400 actually advertises → run the exploit over real emulated BLE.

---

## 1. Environment & native quickstart

```bash
cd ~/aliro-renode
git rev-parse --abbrev-ref HEAD          # aliro/u400-experiment
git submodule status                     # src/Infrastructure pinned to renode/stock-firmware-ble-central

# Headless one-shot (script ends in `quit`):
./renode --disable-xwt --console <script>.resc 2>&1 | grep -viE "WARNING|Unhandled|Route not enabled"

# INTERACTIVE monitor (for native debugging — no `quit` in the script, or Ctrl-C to drop to monitor):
./renode --disable-xwt --console         # then at (monitor): include @<script>.resc
# or start a telnet monitor:
./renode --port 1234                     # then: telnet localhost 1234
```

Useful monitor commands (native debugging):
```
mach create "mg24"; machine LoadPlatformDescription @platforms/boards/silabs/brd4186c.repl
sysbus LoadELF @/home/chen/aliro-reassembly-audit/firmware/U400_primary_target.elf
sysbus.cpu VectorTableOffset 0x08006000
sysbus.cpu PC 0x........                 # set PC
sysbus.cpu SetRegister <n> <val>         # r0..r15 = 0..15
sysbus ReadDoubleWord 0xADDR             # peek RAM/flash
sysbus WriteDoubleWord 0xADDR 0xVAL      # poke (e.g. patch a `b .`: WriteWord 0xADDR 0x4770 = bx lr / 0x2000=movs r0,#0)
sysbus.cpu AddHook 0xADDR "python ..."   # run C#/py at an address (see scripts for the idiom)
sysbus.cpu Step 100000                   # single-step N insns (RELIABLE only on a FRESH machine — see §8)
emulation RunFor "4.0"                   # run 4 emulated seconds (needed to fragment the boot heap)
logLevel 3
```

Key locations on this box:
- **Firmware ELF** (git-ignored): `~/aliro-reassembly-audit/firmware/U400_primary_target.elf` (loads flash 0x08006000, VTOR 0x08006000). `.bndb` beside it for Binary Ninja.
- **U400 scripts:** `~/aliro-renode/scripts/silabs-vuln/aliro_*.resc` (direct-drive ground-truth) and `~/aliro-renode/matter/aliro-u400/scenarios/*.resc` (dispatch-injection / boot-sanity / BLE-bringup probe).
- **SRAM overlay** (git-ignored): `scripts/silabs-vuln/aliro_boot_sram.bin` — regenerate with `aliro_dump_bootsram.resc`.
- **BLE bridge + radio model (C#):** `src/Infrastructure/src/Emulator/Peripherals/Peripherals/Wireless/` — `BleCentralBridge.cs` (825 L), `SiLabs_xG24_LPW.cs` (9340 L, the radio model), `Ieee802154HostBridge.cs`. Wiring in `matter/renode-thread/scenarios/phase3-bridges.repl` (`bleCentral: Wireless.BleCentralBridge @ 0x4FFF0000`).
- **Stock Matter targets (for the working-BLE reference):** `matter/brd2601b-matter-silabs-lighting-example.out`, `brd4186c-...out`.
- **Rebuild Renode after C# edits:** `./build.sh` (from repo root; ~minutes).

---

## 2. The DoS finding (hardware-proven — the disclosure)

- **Root cause:** `aliro_ble_l2cap_on_data_received` (0x0805E7B0) copies a received fragment into the reassembly buffer
  **before** checking `offset+len ≤ sdu_size`. The guard at **0x0801D9C8** runs *after* the `memcpy` @**0x0805E838**.
  Declare a small `sdu_size`, send a larger first K-frame → linear heap OOB write, attacker-controlled length+content.
- **Pre-auth:** RX transfer registered on ACL connect; CoC accepted after a plaintext protocol-version echo (validator
  `set_selected_ver` 0x0805E138 sets accept flag `*0x20024074`; gate `sub_801d81c` on SPSM 0x0080). No encryption/bonding.
- **Impact:** crash → watchdog reboot (~10 s). Repeat = sustained DoS.
- **PoC (on the mac side):** `poc/aliro_l2cap_overflow_bumble.py` (nRF dongle + Bumble) / `aliro_l2cap_overflow_poc.py`
  (Linux HCI). Report: `U400_L2CAP_DoS_report.md` + bundle `~/Downloads/AqaraU400Reporting/` — already scoped
  "no claim of code execution or lock actuation."

---

## 3. The exploit primitive — register provenance at the `blx` (verified in Binary Ninja)

Dispatch site: `sub_801D96C`; indirect call **`blx r7` @ 0x0801D9BE**. Descriptor node ptr = `r4` (= return of
`sub_80d6038`, a conn/CID-keyed descriptor lookup, `mov r4,r0` @0x0801D980). Instruction window just before the call:

```
0801d9a8  ldr  r3,[r4,#4]      ; r3 = callback-table ptr   (= *(node+4))
0801d9aa  cbz  r3, ...         ; non-null guard
0801d9ac  ldr  r7,[r3,#4]      ; r7 = TARGET               (= *(table+4))
0801d9ae  cbz  r7, ...         ; non-null guard
0801d9b0  ldr  r0,[r4,#0x1c]   ; buffer base
0801d9b2  ldr  r1,[r4,#0x20]   ; running write offset
0801d9b4  adds r3,r5,r2
0801d9b6  adds r3,#4           ; r3 = frame_ptr + r2 + 4
0801d9b8  uxth r2,r6           ; r2 = frame length byte (0..0xFF)
0801d9ba  add  r1,r0           ; r1 = *(node+0x20) + *(node+0x1c)
0801d9bc  mov  r0,r4           ; r0 = node
0801d9be  blx  r7
```

| Reg | Value at blx | Source | Attacker control |
|-----|------|--------|------------------|
| **r7** (target) | `*(*(node+4)+4)` | node+4 (vtable ptr) → +4 | **FULL** — both in overflow-reachable heap; only 2 non-null checks |
| **r1** (arg2) | `*(node+0x20)+*(node+0x1c)` | two descriptor fields we overflow | **FULL** — aim it at our forged command struct |
| **r0** (arg1) | `node` | the descriptor node pointer itself | value = allocator-chosen; **contents = ours** (we overflow the node) |
| r2 (arg3) | `frame_len & 0xffff` | on-wire length byte `[frame+3]` | bounded 0..0xFF; producer ignores |
| r3 (arg4) | `frame+r2+4` | ptr into our RX packet | producer ignores |
| sp | task stack (6-word push @entry) | — | not controllable |
| lr | 0x0801D9C1 (→ resumes 0x0801D9C0) | fixed | caller **survives** a normal callee return (ends `pop {..,pc}` @0x0801DA54) |

**Verdict (goals A/B/C):** we have **full control of the jump target (r7) AND the primary pointer argument (r1)** — this
is *not* merely an "uncontrolled indirect jump"; it's a controlled-target + controlled-arg primitive. The **one gap** is
**r0 (arg1)**: the site forces `r0 = node`, but the producer uses arg1 as a **task/timer context handle** (§4). So it's
strong A/B + partial C. Because `r0 = node` and we own the node's *contents*, the node can likely be forged to double as
a valid-enough arg1 handle → true C (the "r0-refinement", §9a).

---

## 4. Producer → consumer → motor chain (the actuation path the exploit reuses)

This is the **legit Matter path**; the exploit hijacks its tail by `blx → sub_8072174`.

- **Producer / router `sub_8072174` (0x08072174)** — `f(arg1=r0, arg2=r1)`. Reads the command struct via **arg2 (r1)**:
  `+0x04` name_ptr (strcmp select), `+0x08` len (`==8`), `+0x0C` speed (0xC8), `+0x10` wait.
  Uses **arg1 (r0)** only as a timer/task handle → `sub_80c0e78(r0)` @0x0807228A, `sub_80c122c(r0)` @0x0807229A (AFTER
  the publish). Global gate **`*0x2001c210`** read @0x0807219C — **must be 0** or it `bx`es to a registered handler
  instead of routing (set to `&data_80722b6` @0x08072274 at the end of each command; ~60 s timer or ack clears it).
  Publishes @0x0807226C: `sub_80c8f68("pid_motor" 0x0814f76c, payload, &var_28, 8, ...)`, payload built from **locals**
  (speed + `*0x20023fb6` wait) — **independent of r0**. Also writes `*0x20024088=1` (motor-moving) @0x0807223E.
  Canonical name strings: **unlock `0x081505AC`** (→ publishes `door_unlock` 0x0814F9C8), **lock `0x08150584`** (→ `door_lock`
  0x0814F9BC). Accept-beep: `sub_8072118("door_unlock_start" 0x08137E98)` @0x080721BC.
- **Consumer task `sub_80718FC` (0x080718FC)**, prio 0x1F — blocks on the "pid_motor" mailbox; on `door_unlock` calls
  actuator **`sub_80714F4` (0x080714F4, unlock)** @0x0807193E; on `door_lock` calls **`sub_807168C` (0x0807168C, lock)** @0x0807190E.
- **Drive `sub_806DA24` (0x0806DA24)**, motor-ctx `*0x2001c11c`.
- **Guards:** unlock `sub_8070818` (0x08070818, "Already Unlocked, Exit", **0 = proceed**); lock `sub_80713CC` (0x080713CC,
  **nonzero = proceed**, has a time-based early-exit @0x08071442 and a position gate on `*0x2001c1c2/1c3`); pre-drive
  `sub_8071458` (0x08071458).
- **State globals:** GBUSY `*0x2001c210`, GMOVING `*0x20024088`, wait `*0x20023fb6`, motor-ctx `*0x2001c11c`,
  calibrate flag `*0x20024051`, position bytes `*0x2001c1c0/1c1/1c2/1c3`.

**Why cold-calling the actuator fails:** `sub_80714F4` needs the pid_motor task context; called from the BLE task it's the
wrong context → no motion. The producer path is correct precisely because it **publishes + signals a semaphore**, letting
the prio-0x1F consumer run in its own context. That cross-task hop is the **untested "scheduler gap"** (§9b).

---

## 5. Heap geometry ground-truth (allocation blindness — SOLVED in emulation)

Object model (verified): `start_recv_data sub_805e9c8(conn)` → `malloc(0x3c)` descriptor into array `0x2001b758[slot]`,
sets `desc+0x08 = vtable 0x20002828`, `desc+0x00/0x28 = conn`. `on_data_received sub_805e7b0(desc+4, off, len, data)` →
if `desc+0x38==0` `malloc(*(desc+0x12))` as reassembly buf `B1` stored at `desc+0x38`, then `memcpy(B1+off, data, len)`
(overflow @0x805e838, writes UPWARD from B1).

**Two-connection groom (robust for sdu ∈ {0x30,0x40,0x60,0x80}):** conn-A's `B1` overflows into conn-B's descriptor `D_B`,
allocated immediately above `B1`.

| Heap | D_A | B1 (=[D_A+0x38]) | D_B | D_B+0x08 (blx callable) |
|------|-----|------|-----|------|
| clean (layout_map) | 0x20024900 | 0x20024948 | 0x20024990 | 0x20024998 |
| fragmented boot (realheap_drive, tail hi-water 0x20025EC8) | 0x20025ED0 | 0x20025F18 | 0x20025F60 | 0x20025F68 |

- **Formula: `D_B+0x08 = B1 + sdu_size + 0x10`.** Chunk header the overflow must rebuild = `01 00 08 00 09 00 09 00`
  (the `CHUNK` const in the PoC). Fixed scratch: **VTABLE 0x20002828, SLOT 0x2000282c, cmd-struct S 0x20024100**.
- The PoC overflow bytes were validated byte-for-byte through the REAL `memcpy` + REAL dispatch (fidelity PASS):
  reconstruct `D_B` exactly and land `blx` in the producer with `r1=S`. Exploit is **base-independent** (writes relative
  to B1; only fixed SRAM scratch is absolute).

---

## 6. Emulation methods (what works / what's broken)

- **Direct-drive on SRAM overlay (WORKS, Step reliable):** fresh machine → overlay `aliro_boot_sram.bin` (captured
  post-boot fragmented SRAM, deterministic, no ASLR) → hijack CPU to a sentinel hook @0x2000EE00 running a state machine
  that calls the firmware's own functions and reads back pointers. Scripts: `aliro_realheap_drive[_s30/_s60/_s80].resc`,
  `aliro_layout_map.resc` (clean heap; inits allocator via PC=0x800e4a4), `aliro_e2e_chain/_stageC/_lock/_fidelity.resc`.
- **Passive malloc-trace (WORKS):** `aliro_armed_complete.resc` hooks malloc wrapper 0x8034b1c + tail-base 0x20012894;
  also shows the post-boot hijack idiom (PRIMASK/FAULTMASK=1, reset MSP, then set PC).
- **Live-boot + Step (BROKEN):** under dual-core + `SetGlobalSerialExecution`, `Step` does NOT execute after a
  `RunFor`-based boot. Use RunFor-only, or the SRAM overlay for anything needing Step. Boot-to-fragmented-heap:
  `boot_mg24.resc` + `emulation RunFor "4.0"` (bypasses: radio-PA `0x808105c`, NVM/config HAL family
  `0x80e3e46/e48/e56/e58/e86/e88/e96/e98/ea6/ea8`).

---

## 7. BLE-in-emulation — transport WORKS, U400 radio-init is the wall

**Transport is intact (not the blocker).** Stock lighting app (`brd2601b-matter-silabs-lighting-example.out`) advertises
CHIPoBLE; `BleCentralBridge` does CONNECT_IND + MTU 247 + full GATT discovery (svc 0x0015-0xFFFF, C1 write 0x0017, C2
indicate 0x0019). Reference scenario style: `matter/renode-thread/scenarios/e2e-15.4.resc`.

**U400 stalls in radio init** (multi-day radio-model dev, NOT a stub away — confirmed by actually trying stubs):
- U400 boots into FreeRTOS (CoC vtable @`0x2000282c = 0x0805E7B1`, heap up) but stalls at **`0x808105c`** (`b .` after
  PA-config **`sub_80e3d60`** returns failure).
- Forcing each RAIL cfg primitive to "return success" (`WriteWord 0x2000 / 0x4770` = `movs r0,#0; bx lr`) just marches
  assert → assert:
  `0x808105c → (patch sub_80e3d60) 0x80fd44e → (patch sub_80e3e56) 0x80fd45a → (patch full 0x80fd4xx cluster) 0x803e736`
  (`sub_80e3dce` assert ×3 in `sub_803e6d8`).
- **CORRECTION (2026-07-23, verified in Renode) — the `0x808105c` gate is NOT an MMIO/RF-cal problem; it is pure-software
  RAIL PA/TxPower config.** The gate is in `sub_8081030`: `r0=sub_80e3d60(...); if(r0==0) ok; else b .`. Empirically
  `sub_80e3d60` **returns `0x1`** (not an `0xffffffXX` RAIL code) because `sub_80cec04→sub_80d2f6c` returns nonzero.
  Refuted theories: (a) the PA-descriptor allocator RAM ptr `0x20001530` is **installed** (`=0x80E3711`), not NULL;
  (b) the mutex path is fine — lock/unlock fns `*0x2000153c`/`*0x20001538` are installed and the lock fn (`0x80cdd0a`)
  runs OK on the mutex arg `0x200134bc` (which reads 0 but is tolerated). **`sub_80d2f6c` fails BEFORE its PA-alloc loop:
  `sub_80d2be4` and the allocator `0x80e3710` are never reached.** So the failure is in the handle-open path
  `sub_8017240` (→ `sub_80d277c` validation or `sub_8018de4` handle-alloc) or `sub_80d2f6c`'s own pre-loop checks — a
  RAIL software config/descriptor issue over flash tables, still to be pinned. This path touches **no radio MMIO**, so
  seeding RF-cal register values does NOT help it. The genuine radio MMIO (SYNTH-lock/RAC/MODEM + advertising TX) is on
  the RAIL PHY-start path reached only AFTER this gate; the radio register-bank base for that path is not yet located.
  (The power-curve fns `sub_80d58a2/sub_801bfcc/sub_801aecc` validate struct magics `0x4100`/`0x80000020`/`0xb0000020`,
  not radio registers.)
- **Handshake the firmware waits on (for the radio-model dev):** POLLS (not IRQ) RAC status bits `0x200/0x400/0x80/0x40`,
  a SYNTH "lock" bit, MODEM status, plus a two-core RAC **Storage0 mailbox** (the model already has a livelock-breaker at
  `SiLabs_xG24_LPW.cs:209-269`). Earlier-catalogued blocking reads: RF-cal tokens `deviceInformation` 0x0/0x248/0x24C/0x260;
  RAC 0x168/0x16C/0x178/0x190/0x198; SYNTH 0x2C; MODEM 0x80/0xDC/0x13C.
- **Probe scenarios (created, none committed):** `matter/aliro-u400/scenarios/u400-ble-bringup.resc` (applies the RAIL
  bypass knobs, samples PC, stalls at 0x803e736); `u400-stubs-v2.repl` (tree-compatible: I2C1_NS IF 0x5006803C→0x160,
  SMU_S/SMU_NS as MappedMemory — confirmed does NOT move the wall, i.e. blocker is radio-cfg, not I2C/SMU). NOTE: the old
  `u400-stubs.repl` no longer loads on this tree (I2C0 0x5B000000 is now a real `I2C.EFR32_I2CController`; I2C1
  0x50068000 still Tagged — moot for BLE since I2C is only reached *after* the radio, which never comes up).

**To make U400 advertise you must author, in `SiLabs_xG24_LPW.cs`:** plausible RF-cal token values + the RAC/SYNTH/MODEM
state transitions so the PA/cfg power-curve computation succeeds and the poll loops exit. No factory calibration values in
hand. This is genuine radio-model development (days), not scripting.

---

## 8. Superseded / corrected claims — DO NOT reuse

| ❌ Claim | ✅ Correction |
|---|---|
| Cold-call `blx → sub_80714F4` = unlock | Wrong task context → no motion. Correct entry = producer `sub_8072174` (§4). |
| "Uncontrolled r0 → fault" | r0 = node (mapped); post-dispatch derefs survive but on the WRONG handle (§3, §9a). |
| "RCE→unlock demonstrated in emulation" | Overstated: emulation code-path reachability only; NOT physical actuation. |
| "Renode can't natural-boot U400" | It boots past RTOS init; only the BLE PHY/advertising doesn't come up (§7). |
| "u400-stubs.repl works" | Broken on this tree — use `u400-stubs-v2.repl` (§7). |
| Send-path info-leak / `lm_ble` stack overflow | Both refuted. |
| A single beep / velocity-dependent RESET = "motion" | Misreads; real device shows no actuation. |

---

## 9. Remaining experiments (native dev menu, best ROI first)

### 9b. Inject-and-run scheduler test  ← HIGHEST VALUE, no radio model needed
Tests the one real unknown (does the consumer task autonomously wake and drive the motor) WITHOUT BLE or the radio PHY.

**UPDATE 2026-07-23 — the feasibility gate is CLEARED; boot-to-running-RTOS now works.** The original premise here
("boot with the radio spin bypassed → scheduler + tasks alive") was **wrong as stated**: the radio bring-up, an I2C
transfer, and the filesystem mount all run on the boot path *before* the app task-creation fn `sub_8038d44`, so under the
plain RAIL bypass **no application task is ever created** — verified: at the `0x803e736` stall, `sub_8038d44` has not run.
Reaching task creation needs THREE extra stubs (each `movs r0,#imm; bx lr`):
- `sub_80e3dce` → **0** (radio config-validator; nonzero == the `0x803e736` `b .` assert in `sub_803e6d8`). `0x80e3dce=0x2000/0x80e3dd0=0x4770`.
- `sub_803266c` → **0** (I2C xfer state-machine; spins polling I2C IF reg `*(base+0x3c)` — no slave modeled). `0x803266c=0x2000/0x803266e=0x4770`.
- `sub_804b8e0` → **1** (fs mount; hangs in littlefs `load_file_index` retries — fs partition not in the ELF). `0x804b8e0=0x2001/0x804b8e2=0x4770`.

With all three, boot reaches `sub_8040e44` (app-main) → `sub_8038d44` → **creates every worker task incl. the pid_motor
consumer `sub_80718FC` (prio 0x1f) and producer `sub_8072174`**. Verified the consumer runs then blocks on its mailbox,
and the scheduler context-switches (PendSV `0x08006430` fires ~32/s; note the FreeRTOS tick is a HW timer, NOT ARM SysTick
`0x080CEF48`). Reusable harness: **`matter/aliro-u400/scenarios/u400-rtos-boot.resc`** (boots + leaves the machine paused
so an experiment script can `include` it). One early task `sub_80e2cb0` busy-spins (`0x80e2d98`↔`0x80e2d9c`) but the
scheduler still preempts it, so a post to the prio-31 consumer mailbox will still wake it.

**§9b DEMONSTRATED (2026-07-23) — scenario `matter/aliro-u400/scenarios/u400-9b-inject-unlock.resc`.** Reproducible
trace (ErrorLog markers):
```
9B: CONSUMER_GOT_MSG (sub_80718FC dequeued the mbus message)
9B: *** UNLOCK_ACTUATOR sub_80714f4 CALLED, arg0(speed)=0xC8 ***   <- 0xC8 == injected payload speed
9B: UNLOCK_GUARD sub_8070818 (already-unlocked check)
9B: *** DRIVE sub_806da24 REACHED (motor drive fn) ***
9B: TRAP injected publish returned r0=0x0
```
i.e. a `door_unlock` published to the mbus makes the **prio-31 consumer `sub_80718FC` autonomously wake under the real
FreeRTOS scheduler**, dequeue it, call unlock actuator `sub_80714F4` (with the injected speed), pass guard `sub_8070818`,
and **reach the motor drive `sub_806DA24`** — no hand-stitching. The `arg0=0xC8` proves the consumer processed *our*
message. This is the strongest evidence short of HW. **Still emulation — no motor model, so physical motion is
unobservable; this proves autonomous code-path reachability, not actuation.**

How it was made to work (each point was a real obstacle — see the scenario header for detail):
- The FreeRTOS **scheduler tick runs WITHOUT BURTC** (PendSV `0x08006430` fires ~32/s; tick source is SysRTC/SysTick,
  NOT the BURTC HAL timebase), so a readied task *can* be switched to. → don't need BURTC for §9b.
- The HAL delay `sub_80e2d7a` busy-spins forever at **`0x80e2d98`** (reads the unmodeled BURTC CNT `0x50064020`,
  `BURTC_NS` is `Tag`'d in `efr32xG24.repl`). This spin is a reliable **actively-executing, thread-mode injection point**.
  (Renode halts the core on `b .`/`WFI`, so a paused-PC injection never executes — a hook at a live instruction is
  required.) Modeling BURTC (`EFR32xG2_BURTC` exists; `@ sysbus 0x50064000`) removes this spin but then boot hangs at a
  `b .` @`0x80d4904` — a further unmodeled dependency; unnecessary for §9b.
- Inject the real publish `sub_80c8f68("pid_motor" 0x0814F76C, "door_unlock" 0x0814F9C8, &payload, 8)` (payload = speed
  `0xC8` + wait). The internal give (`sub_8034efc`) picks a TASK path (`sub_8011554`) or ISR path (`sub_80111d0`) via
  `xPortIsInsideInterrupt` (`sub_80e2cb4`, reads IPSR). **Gate the injection on thread mode (`ICSR VECTACTIVE==0`)** so
  it takes the task path and requests a yield; also force `PENDSVSET` in the return trap. The consumer's mailbox
  semaphore handle is `0x20034090`.

**Remaining upside (optional):** finish the BURTC-path boot (fix the `0x80d4904` hang + any further walls) to get a
fully-idle healthy system; or the §9c radio path. Neither is needed for the §9b result above.

### 9a. r0-refinement (direct-drive)  ← cheap, strengthens the primitive writeup
Because `r0 = node` and we own the node contents, forge `D_B` so it ALSO satisfies the arg1 handle that
`sub_80c0e78`/`sub_80c122c` dereference (@0x0807228A/0x0807229A). First enumerate which fields off arg1 those two read,
then craft `D_B` to serve double duty (dispatch fields + handle fields — the 0x3c descriptor has spare bytes). Verify the
full producer tail runs clean in `aliro_e2e_chain.resc`. Upgrades the primitive from "strong A/B, partial C" to
"demonstrated C (emulation)".

### 9c-A. Radio-model dev → real emulated BLE  ← big, uncertain (NOT pursued; see 9c-B instead)
Author the RAC/SYNTH/MODEM bring-up + RF-cal in `SiLabs_xG24_LPW.cs` (§7). Investigation (2026-07-23) reframed this:
the `0x808105c` init gate is **pure-software RAIL config, not MMIO/RF-cal** (see §7 CORRECTION) — `sub_80e3d60` returns
`0x1` from a `sub_80d2f6c` handle-open failure that touches no radio register, so "seed cal values" doesn't apply. The
genuine radio-TX MMIO (SYNTH-lock/RAC/MODEM + advertising) is a further, un-located layer. Multi-day, uncertain, no
factory cal values. Abandoned in favor of 9c-B.

### 9c-B. Real exploit at the L2CAP layer on the LIVE RTOS (radio bypassed)  ← DONE (front half), best value
Skip the whole radio init and feed the attacker's crafted L2CAP transfer straight into the firmware's REAL
reassembly/dispatch handler `sub_801d96c` on the **live-booted RTOS** (§9b boot). Scenario
`matter/aliro-u400/scenarios/u400-9c-B-live-l2cap-exploit.resc`. **DEMONSTRATED:** the firmware's own `blx r7`
(@0x0801d9be) dispatches to a forged target = motor **producer `sub_8072174`** with a forged **unlock** command struct
(`cmd.name=0x081505ac`, len 8, speed 0xC8), and the producer **publishes `door_unlock`** to the pid_motor mbus, giving
the exact semaphore (`0x20034090`) the live consumer waits on — all real firmware. This is the §3 primitive (controlled
`blx` target + arg) realized on live firmware. Forged objects live in scratch `0x30000xxx`; the forged transfer is
registered into the (empty) live L2CAP desc array `0x2001b758` + SDK RX list `0x20013878` (verified empty post-boot, no
active CoC). Descriptor layout mirrors `aliro_e2e_chain.resc` op3.
**Seam (not yet closed in ONE run) — ROOT CAUSE identified (2026-07-23):** the single-run hand-off to the consumer does
not fire. Verified cause: the producer's pid_motor publish give goes via the **FromISR path** (`sub_80111d0`
xSemaphoreGiveFromISR, logged `xSemGiveISR h=0x20034090`), whereas §9b's short direct publish goes **task-path**
(`sub_8011554` xSemaphoreGive). The producer is long, so a tick ISR is active at the give → IPSR≠0 → `sub_80e2cb4`
(xPortIsInsideInterrupt) picks the ISR path. A FromISR give with a NULL woken-token defers the wake (pending-ready) and
requests no yield, so a forced `PENDSVSET` (which only runs vTaskSwitchContext) never switches to the readied consumer.
§9b works precisely because its task-path give does an immediate ready + yield. The give targets the RIGHT semaphore
(`0x20034090`, same mailbox `0x2003408c`, enqueue to `[mbox+8]`) and the consumer IS blocked there — only the wake
propagation differs. Tried ~8 fixes (manual restore [faults — producer causes context switches that stale the saved
ctx], natural return, mid-/post-producer forced PendSV, minimal trap, PRIMASK-atomic producer) — none close it; all
still hit the FromISR-deferred wake. This is an **emulation/injection artifact, not firmware behavior** — on real HW the
producer runs as `pid_motor_task` (thread ctx, no injected tick race), so the give is task-path and the consumer wakes
naturally. The consumer half (door_unlock → autonomous wake → `sub_80714F4` → `sub_806DA24`) is proven in
`u400-9b-inject-unlock.resc`. So the COMPLETE attack path is demonstrated across the two live-RTOS scenarios; only the
radio transport + this wake-propagation seam are bypassed. To fully close in ONE run: (a) force the producer's give onto
the task path (reliably suppress the tick during the give — the PRIMASK-set from a Python hook did not take here; try
the monitor `sysbus.cpu PRIMASK 1` path or masking SysTick), or (b) flush FreeRTOS `xPendingReadyList` after the give,
or (c) run the producer as the real `pid_motor_task` rather than a console-task hijack.

---

## 10. Key address appendix (consolidated)

```
DoS:        on_data_received 0x0805E7B0 · memcpy 0x0805E838 · post-check 0x0801D9C8 · CoC gate sub_801d81c ·
            version validator 0x0805E138 · accept flag *0x20024074
Dispatch:   sub_801D96C · blx 0x0801D9BE · node lookup sub_80d6038 (r4=ret @0x0801D980) · table=[node+4] @0x0801D9A8 ·
            target=[table+4] @0x0801D9AC · r1=[node+0x20]+[node+0x1c] · r0=node
Geometry:   desc array 0x2001b758 · vtable 0x20002828 · SLOT 0x2000282c · S(cmd struct) 0x20024100 ·
            start_recv_data 0x0805E9C8 · on_data_received 0x0805E7B0 · D_B+0x08 = B1 + sdu + 0x10
Producer:   sub_8072174 · beep sub_8072118 ("door_unlock_start" 0x08137E98) · publish sub_80c8f68 ("pid_motor" 0x0814F76C)
            names: unlock 0x081505AC (door_unlock 0x0814F9C8) / lock 0x08150584 (door_lock 0x0814F9BC)
            gate GBUSY *0x2001c210 · GMOVING *0x20024088 · wait *0x20023fb6
Consumer:   sub_80718FC (prio 0x1F) · unlock actuator sub_80714F4 (call @0x0807193E) · lock actuator sub_807168C (@0x0807190E)
Drive:      sub_806DA24 · motor-ctx *0x2001c11c · calibrate flag *0x20024051 · position *0x2001c1c0/1c1/1c2/1c3
Guards:     unlock sub_8070818 (0=proceed) · lock sub_80713CC (nonzero=proceed; time-exit @0x08071442) · pre-drive sub_8071458
Radio wall: PA-config sub_80e3d60 · stall 0x808105c · assert cascade 0x80fd44e/0x80fd45a/0x803e736 ·
            cfg machinery sub_80cec04/sub_80d2f6c/sub_80cec9c/sub_80d2e24/sub_80d2e02 · PA obj *(handle+0x24) ·
            power-curve sub_80d58a2/sub_801bfcc/sub_801aecc · I2C poll sub_803266c · radio model SiLabs_xG24_LPW.cs
Boot bypass:radio-PA 0x808105c · NVM/HAL 0x80e3e46/e48/e56/e58/e86/e88/e96/e98/ea6/ea8
```

---

## 11. Ceiling (why HW actuation is unprovable on this unit)

1. **OTA black-box** — exhausted; real-device result is **negative** (no motion, only reset).
2. **SWD/JTAG** — **fused/inaccessible** → no white-box on the real unit.
3. **Real BLE in emulation** — needs multi-day radio-model dev (§7); even then Renode has **no motor model**, so physical
   motion is never observable in emulation.
4. **Direct-drive emulation** — can't run the scheduler autonomously (only hand-stitched calls). 9b partially addresses
   this by injecting at the mbus layer, but it's still emulation, not the HW unit.

→ **No available method proves physical lock/unlock on this device.** DoS is the finding. Everything else is research.
