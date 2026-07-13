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

All 11 firmware images boot cleanly (30M–50M instructions, no faults) on these platforms.

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

## Files

- `boot_mg21.resc`, `boot_mg24.resc` — generic boot harnesses (`$bin`, `$vtor`).
- `zb0{2,3,4,5,6}_validate.resc`, `mt0{1,2,3,4,5,8}_validate.resc` — per-device
  validation harnesses. `mt08_validateA.resc` drives the real MT-08 handler
  end-to-end (in addition to the direct-sink `mt08_validate.resc`).
- `ZB-06_VALIDATION.md` — detailed writeup of the ZB-06 methodology.
- Firmware ELFs (`*.elf`) are **git-ignored** (not redistributed).

## Reproduce

```
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/<device>_validate.resc; quit"
```
