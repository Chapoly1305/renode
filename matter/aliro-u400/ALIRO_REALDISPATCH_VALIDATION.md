# Aliro U400 — Max-Fidelity Dynamic Validation via the Firmware's Real Event Dispatch

**Date:** 2026-07-18 · **Emulator:** Renode 1.16.0 / EFR32MG24 (brd4186c) · Firmware `U400_primary_target.elf`.
Supersedes the entry point of `ALIRO_L2CAP_DYNAMIC_VALIDATION.md` (which injected one level lower, at
the reassembly handler). Here the event enters at the firmware's **top-level registered BGAPI event
callback** and the firmware demultiplexes and processes it itself.

## What this run proves

A complete BGAPI `l2cap_channel_data` event is handed to `sub_805df38` — the function the firmware
registers as its BLE event callback (`sub_805db80` @0x805dc30, `sub_805c39c(..., sub_805df38, ...)`).
From that entry, **only the firmware's own code runs** to the overflow:

```
sub_805df38(ctx, msg, ctx)                         top-level aliro event callback
  └─ sli_bt_l2cap_transfer_on_bt_event(msg)        sub_801de58  — masks id & 0xffff00f8
       id = 0x034300A0  ==  channel_data           real demux picks the channel_data case
       └─ sub_801d96c(msg+4, ...)                   real reassembly handler
            └─ find-transfer (RX list *0x20013878)  matches cid 0x0040 + conn 0x01
            └─ on_data_received  = real vtable[1]    *(0x2000282c) = 0x0805e7b1
                 └─ memcpy(buf+0, data, 198)         NO clamp  => OVERFLOW
```

### Evidence (`aliro_realdispatch.run.log`)
```
MARK_DISPATCH   sli_bt_l2cap_transfer_on_bt_event(msg) id=0x34300A0   <- firmware read the event id
MARK_REASSEMBLY channel_data handler sub_801d96c                       <- firmware demuxed to reassembly
MARK_ON_DATA_RECEIVED offset=0 len=198                                 <- real data callback via real vtable
MARK_MEMCPY dest=0x20032000 len=198 (no clamp)                         <- the overflowing copy
B[0..3]   = 0x41414141   (the 4 bytes the SDU-length field DECLARED)
B+0x04    = 0x41414141   (first byte PAST the declared 4-byte SDU — OOB)
B+0xC0    = 0x41414141   (still inside the 198-byte copy — OOB)
B+0xC8    = 0xA5A5A5A5   (past the 198-byte copy — sentinel intact, bound of the write)
```
Declared `sdu_size = 4`, 198-byte first fragment ⇒ **194-byte linear heap OOB write**, driven entirely
by the firmware's own id-demux + gate + find-transfer + reassembly + data callback. **No forced PC below
`sub_805df38`.**

(After the copy, the firmware's own post-copy bounds check `sdu_pointer+frag_len > sdu_size` fires — too
late — and its error/cleanup path runs into the uninitialised allocator and stops at `0x80d4904`; a
no-heap harness artifact, after the overflow, not a separate crash of interest.)

## Fidelity ladder — where this sits

| Level | Entry point | Firmware code exercised |
|---|---|---|
| approach B (`aliro_rx_inject.resc`) | `sub_801d96c` | reassembly + copy |
| hijack PoC (`aliro_pc_hijack*.resc`) | `sub_801d96c` | reassembly + copy + vtable dispatch |
| **this (`aliro_realdispatch_inject.resc`)** | **`sub_805df38`** | **top callback + SDK id-demux + init-gate + find-transfer + reassembly + real-vtable callback + copy** |

The real `.data` callback vtable is placed at its **real address 0x20002828** (values are the firmware
constants 0x0805e46d / 0x0805e7b1 / 0x0805eaf1 / 0x0805e41d), so the data callback is resolved exactly as
on hardware.

## The emulator ceiling (why this is the realistic maximum — reusable findings)

Investigated and confirmed empirically; these are the reasons a *fully natural* boot-to-OTA run is not
achievable in Renode and why event-boundary injection is the faithful method:

1. **No BLE radio/PHY model.** `brd4186c.repl` models only `sysbus.radio` (802.15.4-class); there is no
   RAIL/BLE-LL PHY. A real central (or the nRF PoC) cannot connect to the emulated firmware — there is no
   air interface.
2. **Radio init fatally traps.** The `sl_bt` bring-up calls radio-HAL functions that return failure with no
   hardware and land on bare `b .` spins (e.g. `sub_8081030`@0x808105c: `if (sub_80e3d60(...)!=0) b .`;
   `sub_80fd446`@0x80fd44e). These are multiple, independent choke points (317 bare `b .` in the image);
   past them the LL still blocks on radio IRQs that never fire. A natural boot cannot reach a live BLE
   stack / advertising / connection.
3. **A started machine can't be cleanly re-driven.** After `RunFor`+`pause`, neither `Step` nor a second
   `RunFor` executes a manually-set PC (Renode started-state behaviour). In-hook `self.PC=` on a running
   `b .` block does not divert reliably either. So post-boot register-level injection is not available.
4. **Loaded-state has no heap.** From the loaded-not-started image (the only state where `Step` injection
   works), the firmware allocator is uninitialised (`malloc(0x40)` → `0xffffffff` → the FreeRTOS
   `malloc failed` no-return). So a real-heap allocation for the reassembly buffer is not available without
   a boot that cannot complete.

Consequence: the transfer object + reassembly buffer are **pre-placed** (a joined-channel snapshot), the
FreeRTOS init-lock (`sub_801e0f8`) is stubbed to pass, and three debug loggers are stubbed — all clearly
labeled scaffolding. Everything **on the vulnerability path** (id-demux, find-transfer, reassembly, the
real-vtable data callback, the unclamped copy) is genuine firmware execution.

## Forced vs Natural

- **NATURAL:** `sub_805df38` → `sub_801de58` id-demux → `sub_801d96c` → real-vtable `on_data_received` →
  the overflowing `memcpy`, and the firmware's own post-copy (too-late) bounds check. No forced PC below
  the callback entry.
- **FORCED (labeled):** the delivered event bytes (no radio to originate them); the joined-channel
  transfer/descriptor/list/buffer snapshot; the stubbed init-lock + loggers; the pre-placed (not real-heap)
  reassembly buffer.
- **Complements the nRF PoC** (`../poc/`): the emulator proves the code path from the stack's event
  boundary down; the nRF hardware run proves the pre-auth air path from GAP-connect → CoC-open → the same
  event. Neither substitutes for the other in Renode.

## Reproduce
```
cd /home/chen/sl-renode
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/aliro_realdispatch_inject.resc"
```
Success = the four `MARK_*` lines in order and `B+0x04..B+0xC0 = 0x41414141` while `B+0xC8 = 0xA5A5A5A5`.

## Files
`aliro_realdispatch_inject.resc`, event `aliro_ev_chandata.bin`, real vtable `aliro_real_vtable.bin`,
descriptor `aliro_desc_zero.bin`, sentinel fill `aliro_fill_a5.bin`, log `aliro_realdispatch.run.log`.
Ceiling-probe harnesses: `aliro_realboot_diag.resc`, `aliro_heap_hooktest.resc`.
