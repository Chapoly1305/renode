> ⚠️ CORRECTION (2026-07-23): the "corrupt transfer+0x04 vtable → PC=sub_80714f4" cold-call model here runs the actuator in the WRONG task context (no motion on hardware). Superseded by the producer-path (blx sub_8072174 → publish "door_unlock" → prio-0x1F consumer drives the motor). NO physical actuation was ever demonstrated on hardware; the DoS/reset is the only hardware-proven effect. See the local project STATUS.md for the merged, authoritative status.

# Aliro U400 — Unlock-Reachability PoC (overflow → PC → door-unlock routine)

**Date:** 2026-07-18 · **Target:** Aqara U400 fw v3110 · **Emulator:** Renode 1.16.0 / EFR32MG24
(brd4186c). Companion to `ALIRO_L2CAP_DYNAMIC_VALIDATION.md` (which proves the overflow itself).

## Purpose

For the vendor PoC, distinguish **exploitable RCE** from a mere DoS: (1) identify the routine that
physically unlocks the door, and (2) prove that the reassembly heap overflow can steer the CPU's
**PC** to that routine using the firmware's *own* code. This is control-flow-reachability evidence —
**not** a weaponized over-the-air exploit and **not** arbitrary shellcode.

## 1. The unlock function

| Addr | Role |
|---|---|
| **`sub_80714f4`** | **door-unlock actuation.** Logs `"[..]Door unlock start, velocity:%d, pull_spring_time_ms:%d"` (`0x8071514`), runs the PID motor drive (`sub_806da24`/`sub_80ed3ec`/`sub_807096c`/`sub_80ecd5a`/`sub_8012b70`), logs `"Door unlock %s"` (success/failed). Signature `sub_80714f4(velocity, mode)`. |
| `sub_80718fc` | wrapper that calls `sub_80714f4(var_20, var_1c)` (the `door_unlock` command). |

Natural invocation on a real device: a Matter/Aliro/BLE unlock request → `Matter_open_door_event` /
`door_motor_task` queue → `sub_80718fc` → `sub_80714f4`. (String evidence: `pid_motor_unlock`,
`user_door_motor_unlock_process`, `door_unlock`, `Matter_open_door_event`, `matter_open_door`.)

## 2. Control-flow-hijack mechanism (tied to the overflow)

The SDK transfer manager dispatches every received fragment to the data callback via an indirect
call through a **per-transfer callback-vtable pointer** held at `transfer+0x04`:

```
sub_801d96c:  ldr r3,[r4,#4]     ; r3 = transfer->vtable_ptr
              ldr r7,[r3,#4]     ; r7 = vtable[1]  (the data callback)
              blx r7             ; 0x801d9be  — indirect call
```

The reassembly buffer is a small `malloc(sdu_size)` chunk; the linear overflow writes past it into
adjacent heap. If the transfer descriptor (which holds `vtable_ptr` at `transfer+0x04`) follows the
buffer, the overflow overwrites `vtable_ptr` with an attacker value → the next `blx r7` transfers PC
to an attacker-chosen address. Set that to `sub_80714f4` ⇒ **PC → unlock**.

## 3. Result — CONFIRMED (two reproducible steps)

**Step 1 (`aliro_pc_hijack.resc`) — the overflow corrupts the vtable pointer:**
```
BEFORE: vtable ptr @transfer+4 = 0x20033000   (good table; [1]=on_data_received)
MARK_DISPATCH_BLX r7=0x805E7B1                 (frag dispatches to the real handler)
MARK_ON_DATA_RECEIVED offset=0 len=96
MARK_MEMCPY dest=0x20032000 len=96             (the real overflowing copy)
AFTER:  vtable ptr @transfer+4 = 0x20034000    (attacker table — OVERWRITTEN BY THE OVERFLOW)
```

**Step 2 (`aliro_pc_hijack2.resc`) — the corrupted pointer sends PC into the unlock routine:**
```
vtable ptr @transfer+4 = 0x20034000 ; VT_evil[1] = 0x080714F5 (sub_80714f4|thumb)
MARK_DISPATCH_BLX  r7(callback loaded from corrupted vtable)=0x80714F5
MARK_PC_AT_UNLOCK  ***PC reached sub_80714f4 (door-unlock actuation)*** via the firmware dispatch
MARK_UNLOCK_ACTUATION  reached the Door-unlock-start log site inside sub_80714f4
```

End-to-end: **reassembly overflow → overwrites `transfer->vtable_ptr` → firmware's own `blx r7`
→ PC = `sub_80714f4` (door unlock), which begins executing.** No forced PC at the control transfer.

## 4. Forced vs Natural (honest disclosure)

**NATURAL (real firmware, no forced PC below the handler entry):** the reassembly + overflowing
`memcpy` (step 1), and the vtable-dispatch `blx r7` that loads the corrupted pointer and jumps into
the unlock routine (step 2). The unlock routine then runs its own real code to the actuation log.

**FORCED (scaffolding, clearly labeled):**
- An opened-CoC-channel joined-device state is pre-seeded (descriptor/array/list), and each fragment
  is injected at the `sub_801d96c` handler entry (dispatch injection) — same model as the sibling
  `zb0*_dispatch_inject.resc` campaign.
- **Heap adjacency is modeled**: the descriptor is placed immediately after the reassembly buffer so
  the linear overflow reaches `transfer+0x04`. On a real commissioned device the exact heap layout
  (what chunk follows the `malloc(sdu_size)` buffer) is runtime-dependent — this is the same
  heap-grooming limitation noted for the ZB-02 finding, and is the remaining gap between
  "PC-reachability demonstrated in emulation" and "reliable groom on a fielded device."
- The `.data` callback tables are supplied (no full boot; boot wedges in radio init).
- Three debug loggers stubbed to `bx lr`.
- Steps are split into two runs because a fragment returning to the `b .` trap wedges single-stepping.

**Not done (out of scope / boundary):** no reliable over-the-air heap groom against a commissioned
device, no physical bolt actuation, no weaponized end-to-end exploit. This PoC establishes that the
memory-corruption is a **control-flow-hijack primitive that can reach the security-critical unlock
routine** (RCE ceiling is real, not merely DoS) — which is what the vendor report needs.

## 5. Reproduce
```
cd /home/chen/sl-renode
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/aliro_pc_hijack.resc"   # step 1
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/aliro_pc_hijack2.resc"  # step 2
```
Success: step 1 shows `vtable ptr … 0x20033000 -> 0x20034000`; step 2 shows `MARK_PC_AT_UNLOCK`.

## 6. Files
`aliro_pc_hijack.resc` / `aliro_pc_hijack2.resc`, payloads `aliro_pc_p1.bin`/`aliro_pc_p2.bin`,
vtables `aliro_pc_vtgood.bin` (good[1]=on_data_received) / `aliro_pc_fakevt.bin` (evil[1]=unlock),
logs `aliro_pc_hijack.step{1,2}.run.log`. Unlock-function RE in `analysis/FUNCTION_ANNOTATIONS.md`.
