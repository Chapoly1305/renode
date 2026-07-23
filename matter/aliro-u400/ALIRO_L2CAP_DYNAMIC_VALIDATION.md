# Aliro L2CAP Reassembly Heap Overflow — Dynamic Validation (Renode)

**Date:** 2026-07-18 · **Target:** Aqara Smart Lock **U400** (vid 4447 / pid 10244, fw v3110),
`firmware/U400_primary_target.elf`, EFR32MG24 (Cortex-M33). **Emulator:** Renode 1.16.0
(SiliconLabsSoftware fork) on board `brd4186c` (EFR32MG24 / xG24).

> **Superseded entry point:** this report injects at the reassembly handler `sub_801d96c`. The
> higher-fidelity **B+** run (`ALIRO_REALDISPATCH_VALIDATION.md`) delivers a complete BGAPI event to the
> firmware's real top-level callback `sub_805df38` and lets the firmware do the id-demux + find-transfer
> + reassembly itself. The overflow finding here is unchanged; prefer B+ for the strongest dynamic proof.

## Result — CONFIRMED

Approach **(B)** from `HANDOFF.md §7`: inject at the firmware's **own** L2CAP CoC reassembly path
and let its unmodified code run — with **no forced PC below the injection entry** — to prove the
SDK transfer manager does **not** clamp `offset+len` before the copy. It fired:

```
MARK_RX_HANDLER_ENTRY sub_801d96c (channel_data)
MARK_ON_DATA_RECEIVED  offset=0 len=198 data=0x20031006      <- frag_len computed UNCLAMPED
MARK_MEMCPY  dest=0x20032000 src=0x20031006 len=198  (buffer declared sdu_size=4)
MARK_COPY_DONE
firmware-set sdu_size (channel+0xe) = 0x0004                 <- read from the wire by the firmware
```

Reassembly-buffer sentinel readout (buffer pre-filled `0xA5A5A5A5`, copy writes `0x41`):

| Offset in buffer | Before | After | Meaning |
|---|---|---|---|
| `B+0x00` | A5A5A5A5 | **41414141** | inside the declared 4-byte SDU (legitimate) |
| `B+0x04` | A5A5A5A5 | **41414141** | **first word PAST `sdu_size=4` — OOB write** |
| `B+0xC0` (192 B) | A5A5A5A5 | **41414141** | 188 B deep into the OOB region |
| `B+0xC8` (200 B) | A5A5A5A5 | A5A5A5A5 | untouched — copy stopped exactly at 198 |

The firmware wrote **198 bytes into a buffer whose declared SDU size is 4** → a **194-byte linear
heap out-of-bounds write** with fully attacker-controlled content, offset (paced by fragments), and
length. CWE-787, pre-auth, remote over BLE. The overflow occurs *before* the manager's post-copy
`sdu_pointer + frag_len > sdu_size` check (see below), which is therefore useless as a guard.

## The receive chain that was exercised (all real firmware code)

```
sli_bt_l2cap_transfer_on_bt_event (0x801de58)   [BGAPI dispatcher; channel_data id 0x034300a0]
  -> sub_801d96c(payload)                        [the channel_data reassembly handler]
       transfer = find(conn,cid)                 sub_80d6038, list @*0x20013878
       FIRST fragment (sdu_pointer==0):
         channel.sdu_size (+0xe) = payload[4..5]  = 4     <-- attacker-declared, from the wire
         frag_len = payload[3](data.len) - 2      = 198   <-- NOT clamped to sdu_size
       on_data_received(transfer, offset=0, frag_len=198, data)   [vtable[1] = 0x805e7b0]
         buf sized to sdu_size(=4)
         memcpy(buf + offset, data, frag_len=198)          <-- OVERFLOW, no offset+len check
       if (sdu_pointer + frag_len > sdu_size) goto error   <-- runs AFTER on_data_received returns
```

The only bounds test the transfer manager makes (`sdu_pointer + frag_len > sdu_size`, at
`0x801d9c8`) is evaluated **after** `on_data_received` has already performed the copy. The transmit
path (`vtable[0]` @0x805e46c) *does* bound-check before providing data ("Data overflow" log); the
receive path does not. **This is the answer to the open upstream-bounding question (HANDOFF §0.2):
there is no upstream pre-clamp.**

## Method

Emulation-based validation on the **real, unmodified firmware image**, modeled on the prior EFR32
`scripts/silabs-vuln/zb0*_dispatch_inject.resc` campaign (inject a crafted frame at the firmware's
dispatch entry; the firmware's own code runs to the sink with no forced PC below the entry).

Harness: `renode/aliro_rx_inject.resc` (run from the Renode root). It loads the ELF, pre-seeds an
opened-CoC-channel runtime state, and force-enters `sub_801d96c` with a crafted `channel_data`
payload: `conn=1, cid=0x40, data.len=200, declared sdu_size=4, 198 bytes of 'A'`.

### FORCED (scaffolding — models an opened Aliro CoC channel on a joined device; touches no sink logic)
- A transfer descriptor `D @0x20030000` (transfer = D+4) with cid/conn/max_sdu/max_pdu/credit and a
  reassembly buffer pointer, registered into the app array `0x2001b758[0]` and the SDK RX list
  `*0x20013878` — exactly the state the real `open`/`opened` handlers build after an L2CAP CoC opens.
- The callback vtable `V @0x20033000` (`V[1]=on_data_received|thumb`) — the `.data` copy that boot
  makes at RAM `0x20002828` (verified present after a real 0.4 s boot in `aliro_boot_sanity.resc`).
- Three debug-log helpers (`sub_8033598`, `sub_80336f0`, `sub_80cae30`) stubbed to `bx lr` — they
  spin without UART/RTOS init and touch no gate/sink logic (same technique as the zb campaign).
- Boot is skipped because the unmodified firmware wedges in radio/RAIL init (`b .` @0x808105c after
  `sub_80e3d60` fails — the Renode radio model does not drive the RAIL handshake); a `b .` spin is a
  degenerate CPU state that breaks single-stepping. The flash image itself is unmodified except the
  three logger `bx lr` stubs.

### NATURAL (firmware's own code, no forced PC below the `sub_801d96c` entry)
find-transfer, first-fragment detection, **reading `sdu_size` from the wire**, **computing the
unclamped `frag_len`**, the `on_data_received` call, the malloc-skip, the non-fatal
`offset<=sdu_size` guard, and the **overflowing `memcpy`**. The `MARK_*` addresses are all inside
`sub_801d96c` / `aliro_ble_l2cap_on_data_received` / the memcpy.

### Not dynamically exercised (proven statically instead)
The firmware's `malloc(sdu_size)` inside `on_data_received` is bypassed here by pre-seeding the
reassembly-buffer pointer (so the observation is deterministic). The `malloc(sdu_size=4)` → 4-byte
chunk is explicit in the HLIL/objdump (`0x805e862`→`0x805e868 bl malloc`, `str r0,[r4,#0x38]`); the
pre-seeded buffer models that chunk. Everything after the allocation (the unclamped 198-byte copy)
is the real firmware executing.

## Reproduce

```
cd /home/chen/sl-renode
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/aliro_rx_inject.resc"
```
Success signals: the four `MARK_*` lines above, and `B+0x04`/`B+0xC0` flip `0xA5A5A5A5 -> 0x41414141`
while `B+0xC8` stays `0xA5A5A5A5`. Boot sanity (real 0.4 s boot, confirms handler bytes + the RAM
vtable): `aliro_boot_sanity.resc`.

## Files (also mirrored in `/home/chen/sl-renode/scripts/silabs-vuln/`)
- `aliro_rx_inject.resc` — the approach-B dispatch-injection harness.
- `aliro_boot_sanity.resc` — real-boot sanity (handler bytes @0x805e7b0, vtable @0x2000282c).
- `aliro_payload.bin` / `aliro_sentinel.bin` / `aliro_zero.bin` — crafted fragment, buffer sentinel,
  descriptor zero-fill.
- `aliro_rx_inject.run.log` — captured evidence run.
