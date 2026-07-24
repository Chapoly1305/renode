# Aqara U400 Security Assessment — Merged Authoritative Status

**Last updated:** 2026-07-23 · **Target:** Aqara U400 smart lock, fw v3.1.1.0 (vid 4447 / pid 10244), EFR32MG24 / Silicon Labs BLE.
**Authorization:** all testing on the operator's own device, coordinated disclosure.

> This file is the SINGLE source of truth. It supersedes the older scattered docs (see §4). Where a prior
> doc disagrees with this file, this file wins.

---

## TL;DR — the bottom line

- **Hardware-proven (the finding to disclose):** a **pre-authentication remote Denial-of-Service** — an L2CAP CoC
  SDU-reassembly heap overflow that crashes the lock → watchdog reboot (~10 s), no pairing/auth. Reproduced OTA.
- **Everything beyond DoS (control-flow hijack → physical lock/unlock):** **reverse-engineered and emulation-validated
  in design only. NOT demonstrated on hardware, and UNPROVABLE with the access we have.** On the real device the
  only observed effect is the reset. Do not present it as a validated capability.

---

## 1. Hardware-proven finding — pre-auth remote DoS (CWE-787)

- **Root cause:** `aliro_ble_l2cap_on_data_received` (0x0805E7B0) copies a received fragment into the reassembly
  buffer **before** checking `offset+len ≤ sdu_size` — the guard at 0x0801D9C8 runs *after* the `memcpy` @0x0805E838.
  Declare a small `sdu_size`, send a larger fragment → linear heap OOB write, attacker-controlled. Pre-auth (RX
  transfer registered on ACL connect; CoC accepted after a plaintext version echo, no encryption/bonding).
- **Impact:** crash → watchdog reboot (~10 s unavailable); repeat = sustained DoS.
- **Deliverable (DoS-only, final):** `U400_L2CAP_DoS_report.md` and the bundle at `~/Downloads/AqaraU400Reporting/`.
  The report already scopes it correctly: *"no claim of code execution or lock actuation."*
- **PoC:** `poc/aliro_l2cap_overflow_poc.py` (nRF hardware trigger) and bundle `scripts/u400_l2cap_dos_1dongle.py`
  (single-dongle, crash-oracle). OTA-confirmed reset repeatedly.

## 2. The exploit primitive — fully characterized (RE + emulation)

The overflow yields an **arbitrary heap write + control-flow hijack**, and the geometry is now ground-truthed:

- **Two-connection groom:** conn-A's reassembly buffer `B1` overflows *upward* into conn-B's transfer descriptor `D_B`,
  which is allocated **immediately above** `B1`. Confirmed on the real fragmented boot heap in Renode across
  `sdu_size ∈ {0x30,0x40,0x60,0x80}` — the groom holds for every size.
- **Deterministic offset:** the `blx` callable pointer `D_B+0x08` sits at **`B1 + sdu_size + 0x10`**. The 8-byte heap
  chunk header the overflow must rebuild is `01 00 08 00 09 00 09 00` (the `CHUNK` constant in the PoC).
- **Dispatch (`sub_801D96C`, blx @0x0801D9BE):** `r4 = descriptor_base + 0x04`; it does `blx *(*(D_B+0x08)+4)`. At the
  call: `r0 = D_B+0x04` (mapped heap → an empty/short string → post-dispatch derefs are SAFE), `r1 = *(D_B+0x20)+*(D_B+0x24)`.
- **Fidelity:** the *actual* PoC overflow bytes, fed through the real `memcpy` + real dispatch (no cheating), reconstruct
  `D_B` exactly and land `blx` in the producer with the right `r0/r1`. The overflow layout is byte-for-byte correct.

## 3. Producer-path actuation — CORRECT model, NOT demonstrated on hardware

The architecturally-correct way to drive the motor (validated in emulation, unprovable on HW):

- Hijack `blx → sub_8072174` (the **pid_motor producer/router**) with a forged command struct at scratch `S=0x20024100`:
  `{+0x00 source=0, +0x04 name_ptr, +0x08 len=8, +0x0C speed=0xC8, +0x10 wait=0}`.
  Canonical name strings: **unlock = 0x081505AC** (→ publishes `door_unlock` 0x0814F9C8), **lock = 0x08150584** (→ `door_lock` 0x0814F9BC).
- The producer publishes to the "pid_motor" mbus topic + signals a semaphore → the **prio-0x1F consumer `sub_80718FC`**
  drives the actuator: **`sub_80714F4` (unlock)** / **`sub_807168C` (lock)** → drive **`sub_806DA24`**. Guards:
  unlock `sub_8070818` ("Already Unlocked, Exit", 0=proceed); lock `sub_80713CC` (nonzero=proceed).
- **Emulation (idealized direct-drive + hand-invoked consumer) reaches `sub_806DA24`.** But this is a unit-test of
  hand-stitched function calls, NOT an autonomous BLE-driven run — it does **not** exercise the real scheduler or guards.
- **PoC:** `poc/aliro_unlock_producer.py` (`--action unlock|lock`, idle-gated, re-arming, endless mode). On hardware:
  clean landings produce **no motion** (only reset when it faults). Whole model is research, not a demonstrated capability.

## 4. Superseded / corrected claims — DO NOT reuse

| ❌ Claim (where it appears) | ✅ Correction |
|---|---|
| Cold-call `blx → sub_80714F4` = unlock (old `aqara-u400-preauth-unlock-chain` memory; prior `ALIRO_UNLOCK_REACHABILITY_POC.md`) | Wrong task context → no motion. Correct entry is the **producer** `sub_8072174` (§3). |
| "Uncontrolled `r0` → fault" | Refuted: `r0 = D_B+0x04` is mapped (empty string); post-dispatch derefs survive. |
| "RCE→unlock demonstrated in emulation" (prior `HANDOFF-ground-truth.md`) | Overstated: = emulation **code-path reachability** only, **not** physical actuation. |
| "Renode can't natural-boot U400" (old handoffs) | It **does** boot past RTOS init; only the **BLE PHY / advertising** doesn't come up (§5). |
| Send-path info-leak (`aliro_infoleak.py`, `aliro_txread*`) | Refuted — no bytes leaked. |
| `lm_ble` stack overflow | Refuted — reassembler clamps content_len ≤256 < buffer. |
| Velocity-dependent RESET / a single beep read as "motion" | Misreads. Real device shows **no** actuation. |

## 5. Why physical actuation is UNPROVABLE on this unit (the ceiling)

1. **OTA black-box** — exhausted; real-device result is **negative** (no motion, only reset). Can't observe the
   producer/consumer/guard state.
2. **SWD/JTAG** — **fused/inaccessible** → no white-box on the real unit.
3. **Real BLE in emulation** — **infeasible.** The U400 uses **runtime RF calibration**; Renode's `SiLabs_xG24_LPW`
   radio model returns the RF-cal tokens (`deviceInformation` 0x0/0x248/0x24C/0x260) as **0** → `sub_80e3d60` PA-config
   fails → RAC/SYNTH poll-loop spin (`0x8081056`→`0x8032xxx`) → **never advertises**. (The **stock** SiLabs Matter app
   *does* commission over BLE in the same rig because it uses **compile-time** PA curves — that is the "BLE + chip-tool
   commissioning worked" memory; it was the stock app, not the U400.) Fixing = open-ended emulator C# work, and **even
   then Renode has no motor model**, so physical motion is never observable in emulation.
4. **Direct-drive emulation** — structurally can't run the scheduler; only validates hand-stitched calls.

→ **No available method can prove physical lock/unlock on this device.** The DoS is the finding.

## 6. Canonical artifact locations (post-cleanup)

**Local** (`/Volumes/Workspace/EclipseFuzz/aliro-reassembly-audit/`):
- `STATUS.md` (this file) · `U400_L2CAP_DoS_report.md` (disclosure source)
- `poc/`: `aliro_ble.py` (transport), `aliro_unlock_producer.py` (producer-path research PoC),
  `aliro_l2cap_overflow_poc.py` (nRF DoS trigger), `README.md`, `OTA_TEST_2026-07-22.md`, `evidence/`
- `analysis/FUNCTION_ANNOTATIONS.md` · `firmware/U400_primary_target.elf(.bndb)` (+ `aliro_elfs/` corpus of *other* devices)
- Disclosure bundle (DoS-only, final): `~/Downloads/AqaraU400Reporting/`

**Remote** (`ssh chen@lab`) — SINGLE consolidated Renode env (2026-07-23): **`~/aliro-renode`**, git branch **`aliro/u400-consolidated`** (SiLabs 26q1 base, fork `Chapoly1305/renode`, Renode 1.16.1). Run: `cd ~/aliro-renode && ./renode --disable-xwt --console <script>.resc`.
- U400 ground-truth scripts: `scripts/silabs-vuln/` — `aliro_layout_map`, `aliro_realheap_drive[_s30/_s60/_s80]`, `aliro_armed_complete`, `aliro_e2e_chain`/`_stageC`/`_lock`/`_fidelity`, `boot_mg24`, `_armed_patches`, `aliro_dump_bootsram`, `obj1_addrs`.
- Bridge/BLE/Thread env (stock apps only — U400 can't advertise, §5): `matter/aliro-u400/scenarios/u400-in-threadenv.resc` (brd2601b + phase3-bridges + BleCentralBridge), `matter/renode-thread/scenarios/*`.
- Prior-team validation docs (authoritative, annotated with correction headers): `matter/aliro-u400/*.md`.
- **Firmware git-ignored** (copyrighted), loaded from `~/aliro-reassembly-audit/firmware/U400_primary_target.elf`. SRAM overlay `scripts/silabs-vuln/aliro_boot_sram.bin` also git-ignored — regenerate via `aliro_dump_bootsram.resc` (relative in-repo path).
- Retired 25q4 checkout `~/sl-renode.RETIRED-25q4` (staged for deletion pending confirm); full 25q4 history at `~/sl-renode-25q4-history.bundle` (6.2 MB, verified). Geometry re-validated identical on 26q1 (D_A=0x20025ED0, B1=0x20025F18, D_B=0x20025F60, gap 0x50).

## 7. Memory (AI-facing) cross-reference

`[[u400-producer-path-unlock]]` (actuation record + final disposition) · `[[u400-renode-heap-groundtruth]]` (geometry + BLE-emulation ceiling) ·
`[[u400-matter-unlock-actuation-chain]]` (legit Matter chain whose tail the exploit reuses) · DoS findings under `aliro-finding07-kframe-confirmed-dos` et al.
Deleted as superseded: `aqara-u400-preauth-unlock-chain` (old cold-call model).
