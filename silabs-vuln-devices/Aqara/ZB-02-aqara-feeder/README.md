# ZB-02 — Aqara pet feeder (heap OOB write)

| | |
|---|---|
| Finding | ZB-02 |
| Vendor / model | Aqara pet feeder |
| Chip / board | EFR32MG21 (Cortex-M33) · `brd4180a.repl` |
| ELF | `zb02_aqara.elf` (present in this folder; git-ignored — also in `scripts/silabs-vuln/`) |
| Class | heap out-of-bounds write |
| Sink | `sub_14D08` (0xFCC0 MIoT segmented-reassembly handler) |
| Gate | `sub_14EB4` (emberCommandReceivedCallback): cluster `0xFCC0` + cmdId `2` + msg type `0xFFF1` → `sub_14DEC` → `sub_14D08` |
| Highest result | write primitive characterized + **real-allocator misdirection demonstrated** |

## The bug

`sub_14D08` reassembles a segmented MIoT payload. It computes the copy
destination as:

```
dest  = ctx[8]  + (uint16)((segment_index - 1) * 60)      ; segment_index = frame[2], one byte, UNCHECKED
count = frame_len - 3
memcpy(dest, frame+3, count)                              ; bl 0x3d856 @ 0x14d8c
```

There is no bound check of `segment_index` against the segment count. The
attacker therefore controls, per frame:

- **destination offset** — `segment_index` ∈ [0,255]: idx=1→+0, idx=2→+60, …
  idx=255→+15240, and **idx=0 wraps to +65476** (`(uint16)(-60)`);
- **data** — the payload bytes, copied verbatim;
- **length** — `frame_len - 3`;
- **repeatability** — it is a reassembly handler: many frames → many writes; the
  "segment already received" test only logs, the write still happens.

= a controlled-offset, controlled-data, repeatable relative write.

## What was validated

| Level | Script | Result |
|---|---|---|
| function-level | `zb02_validate.resc` | with idx=0, write lands at `0x20014FC4` (base+65476) = `0x41414141` — attacker offset proven |
| dispatch injection | `zb02_dispatch_inject.resc` | plaintext 0xFCC0 frame injected at `sub_14EB4`; firmware's own gate (clus 0xFCC0 + cmd2 + msg 0xFFF1) routes to `sub_14D08`; OOB write occurs, no forced PC below entry |
| exploitation mechanism | `zb02_heap_exploit.resc` | forged inline block header misdirects the **real** allocator `sub_1411c` |

### The target heap (reverse-engineered from `sub_140e0`/`sub_1411c`/`sub_141e4`)

Inline-boundary-tag first-fit pool: base `0x20005948`, size `0x2800` (10 KB).
4-byte block header = 15-bit size (bytes, incl. header) + bit15 in-use; data at
header+4; free-list walked by size with coalesce-on-alloc. `free` (`sub_141e4`)
bounds-checks the pointer, clears the in-use bit, updates the head to the lowest
free block — it does **not** coalesce (coalescing happens during alloc).

### Exploitation mechanism (demonstrated with real firmware code)

`zb02_heap_exploit.resc` seeds a format-correct pool with two adjacent in-use
blocks A,B, then forges B's inline header to a huge free size `0x7FF0` (what the
`sub_14D08` OOB would write one block downstream) and runs the genuine allocator
`sub_1411c` for a 204-byte request:

```
WALK: examining block r1=0x200059C8
SPLIT STORE: leftover-free-header VALUE=0x7F24 at addr 0x20005A94 (block 0x200059C8, reqsize 0xCC)
```

The allocator accepts the forged size and emits a leftover free block claiming
`0x7F24` = 32548 bytes at `0x20005A94` — i.e. a free block spanning
`0x20005A94..0x2000D9B8`, **~22 KB past the 10 KB pool end (`0x20008148`), into
arbitrary RAM**. The next allocation from it returns a pointer OUTSIDE the pool =
allocator-mediated write at an attacker-influenced address.

## Value verdict (honest)

- **DoS = certain floor** — any out-of-pool write corrupts heap structures /
  adjacent objects → crash.
- **RCE = real, well-understood path** via this classic allocator; the
  metadata-corruption mechanism is proven on real firmware code, **but the full
  groom→shellcode chain is not built**: the un-commissioned ELF never populates
  the live heap, so *what is naturally adjacent* on a real joined device is not
  observable in this emulation (same commissioning limit as the OTA path).

## Reproduce

```
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/<script>.resc"
```

| Script | Success signal |
|---|---|
| `zb02_validate.resc` | `>>> AFTER: OOB target 0x20014FC4 … = 0x41414141` |
| `zb02_dispatch_inject.resc` | gate `MARK_*` chain fires; saved slot overwritten |
| `zb02_heap_exploit.resc` | `SPLIT STORE: leftover-free-header VALUE=0x7F24 at addr 0x20005A94` |

Supporting diagnostics (pool empty at bare boot: head `[0x2000456c] = 0`) are in
`scripts/silabs-vuln/zb02_heap_probe.resc` / `zb02_heap_init.resc`.

## Forced vs natural

`zb02_dispatch_inject.resc` pre-seeds the reassembly state + default-open auth
flag and stubs a debug-UART logger that busy-waits in emulation (touches no
gate/sink logic) — all = modeling a joined device's runtime state, not code
bypass. `zb02_heap_exploit.resc` seeds a pool by hand because the bare boot leaves
the real pool empty; the allocator/free **code** it exercises is unmodified
firmware. Return sentinels use RAM `0x2000EE00` `b .`; the demo halts in the
split-store hook before a forced-context critical-section helper (`bl 0xa702`)
stalls.

## Files
- `scripts/zb02_validate.resc` — function-level sink.
- `scripts/zb02_dispatch_inject.resc` — dispatch-injection reachability.
- `scripts/zb02_heap_exploit.resc` — allocator-misdirection demo.
- Pool-characterization diagnostics (`zb02_heap_probe.resc`, `zb02_heap_init.resc`) live in `scripts/silabs-vuln/`.
- ELF `zb02_aqara.elf` — present in this folder; git-ignored (not committed). Also in `scripts/silabs-vuln/`.
- OTA `20221213122302_OTA_aqara.feeder.acn001_0.0.0_3833_20220914_03DFD1.ota` — native
  Zigbee OTA distribution image (git-ignored); the ELF above is its extracted
  executable. Source: `zigbeeFirmware.zip` (koenkk mirror). Validated-ELF
  sha256 `544ba4a9…` — byte-identical to the corpus copy.
