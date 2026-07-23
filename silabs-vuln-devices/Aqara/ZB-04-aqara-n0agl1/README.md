# ZB-04 — Aqara switch n0agl1 (constrained stack overflow → DoS)

| | |
|---|---|
| Finding | ZB-04 |
| Vendor / model | Aqara switch n0agl1 |
| Chip / board | **EFR32MG13 (Series 1, Cortex-M4)** · `brd4162a.repl` (the only non-MG21 Zigbee finding) |
| ELF | `zb04_aqara.elf` (present in this folder; git-ignored — also in `scripts/silabs-vuln/`) |
| Class | constrained stack overflow (saved-LR low byte only) |
| Sink | `sub_5014` (0xFCC0 mfg-specific handler) |
| Gate | `sub_B9DC`: clusterId switch → `0xFCC0` compare → flag gate `*0x200059f4==1` → tail-call `sub_5014` |
| Highest result | **DoS proven** (corrupted return address → fault vector taken) |

## The bug

`sub_5014`'s first operation is an unclamped `memmove` into a 64-byte stack
buffer:

```
count = (uint16)(bufLen - 5)      ; bufLen attacker-controlled, capped at 82 by the receive path
dest  = SP + 0x10                 ; 64-byte buffer
src   = struct[0x8] + 5
memmove(dest, src, count)         ; bl sub_C5F0 @ 0x502a  — NO clamp
```

Prologue `push {r4,r5,r6,lr}` + `sub sp,#0x50` puts saved R4/R5/R6 at buf[64/68/72]
and **saved LR at buf[76]**. The 82-byte reception cap bounds `count` to 77, so
the write reaches buf[0..76] — fully overwriting saved R4/R5/R6 and **only the low
byte of the saved return address**. That single controllable byte is enough to
make the return address invalid, but **not** enough for arbitrary-PC control — so
this is a constrained CFH/DoS, exactly as the report classifies it, not RCE.

## What was validated

| Level | Script | Result |
|---|---|---|
| function-level | `zb04_validate.resc` | saved R4/R5/R6 = `0xAAAAAAAA`; saved LR `0xDEADBEEF`→`0xDEADBEAA` (low byte only) |
| dispatch injection | `zb04_dispatch_inject.resc` | plaintext 0xFCC0 frame at `sub_B9DC`; firmware's own clusterId==0xFCC0 gate + flag gate route to `sub_5014`; same constrained overwrite, no forced PC below entry |
| **DoS** | `zb04_dos.resc` | corrupted return address popped → CPU vectors through the live fault table to a controlled trap stub |

### DoS proof (decisive, not inferred)

After the natural overflow corrupts the saved return address, `sub_5014`'s own
epilogue `pop {r4,r5,r6,pc}` @`0x5066` loads PC = `0xDEADBEAA` (bit0=0 →
UsageFault INVSTATE; address unmapped → BusFault). The proof redirects the four
programmable fault vectors to a RAM trap stub `b .` @`0x2000EE00` and observes:

```
MARK_5066_EPILOGUE  pop will load PC = [SP+0xC] = 0xDEADBEAA
>>> FAULT VECTOR TAKEN: CPU vectored to trap stub @0x2000EE00
FINAL PC = 0x2000EE00
```

`0x2000EE00` is reachable **only** via ARM exception entry — so PC landing there
is unambiguous proof the corrupted return faults. i.e. one on-network 0xFCC0 frame
crashes the CPU into its fault handler = reliable DoS.

**Key gotcha:** the firmware relocates `SCB->VTOR` from the ELF's `0x4000` to
`0x4200` during boot (see `scripts/silabs-vuln/zb04_dos_diag.resc`), so the LIVE fault vectors are at
`0x420C/0x4210/0x4214/0x4218` (HardFault vec `0x420C = 0xE41D`). Patching the ELF
reset table `0x400C` has no effect — the fault would take the firmware's real
handler (`~0xE436`) instead.

## Reproduce

```
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/<script>.resc"
```

| Script | Success signal |
|---|---|
| `zb04_validate.resc` | `>>> saved LR @0x2000EFFC … = 0xDEADBEAA` |
| `zb04_dispatch_inject.resc` | `MARK_BA8A_FCC0_MATCH` + `MARK_5014_SINK_ENTRY` fire; saved LR = `0xDEADBEAA` |
| `zb04_dos.resc` | `FAULT VECTOR TAKEN …` + `FINAL PC = 0x2000EE00` |

The live-VTOR diagnostic (`SCB->VTOR = 0x00004200`; `0x420C = 0xE41D`) is
`scripts/silabs-vuln/zb04_dos_diag.resc`.

## Forced vs natural

`zb04_dispatch_inject.resc`/`zb04_dos.resc` pre-seed the mfg-command flag
`*0x200059f4=1` and stub the endpoint/cluster-affinity lookup `sub_1bff6` (empty
on a non-joined device — models a joined device that registered cluster 0xFCC0);
the clusterId==0xFCC0 compare, the flag gate, the sink, the memmove, and the
epilogue all run naturally. **MG13 notes:** MappedMemory writes bypass Renode
watchpoints (observe by reading memory at the right PC); `RunFor` stalls on this
image so scripts use bounded `Step`; `IsHalted`-in-hook is not honored mid-`Step`.

## Files
- `scripts/zb04_validate.resc` — function-level sink.
- `scripts/zb04_dispatch_inject.resc` — dispatch-injection reachability.
- `scripts/zb04_dos.resc` — DoS proof (fault vector taken).
- Live-VTOR diagnostic (`zb04_dos_diag.resc`) lives in `scripts/silabs-vuln/`.
- ELF `zb04_aqara.elf` — present in this folder; git-ignored (not committed). Also in `scripts/silabs-vuln/`.
- OTA `20220121143603_lumi.switch.n0agl1_0.0.0.0023_20211230_0717A1.ota` — native
  Zigbee OTA distribution image (git-ignored); the ELF above is its extracted
  executable. Source: `zigbeeFirmware.zip` (koenkk mirror). **Version note:** this
  is the `0.0.0.0023` (2022) build that was validated (sha256 `b420fd9b…`); the
  corpus also carries a newer `0.0.0.0030` (2024) build which is a *different*
  binary (`b0f14526…`) — not the one these results are for.
