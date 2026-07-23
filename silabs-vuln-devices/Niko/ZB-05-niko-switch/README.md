# ZB-05 — Niko switch ×2 (stack → saved-LR → full RCE)

| | |
|---|---|
| Finding | ZB-05 |
| Vendor / model | Niko switch (×2 variant) |
| Chip / board | EFR32MG21 (Cortex-M33) · `brd4180a.repl` |
| ELF | `zb05_niko.elf` (present in this folder; git-ignored — also in `scripts/silabs-vuln/`) |
| Class | stack overflow → saved return-address overwrite (full 4-byte PC control) |
| Sink | `sub_D290` (cluster `0xFC00` Write-Attribute), memmove body `sub_D540` |
| Gate | `sub_6482` (ZCL cmd handler) → `6406` → `62C8` → `6A46` veneer → `sub_D290` → case `0x107` → `sub_D540` |
| Highest result | **full working RCE** (shellcode executes) |

## The bug

The `0xFC00` Write-Attribute path reaches an unclamped `memmove` (`sub_D540`)
whose attacker-controlled length overflows a stack buffer and overwrites the
sink's saved return address — full 4-byte control.

## What was validated

| Level | Script | Result |
|---|---|---|
| function-level | `zb05_validate.resc` | saved-LR `0x2000FFFC` → `0x20005001` (full control) |
| dispatch injection | `zb05_dispatch_inject.resc` | plaintext 0xFC00 WriteAttr frame at `sub_6482`; firmware's own chain (6482→6406→62C8→6A46→D290→case 0x107→D540) routes to the sink; saved-LR `0x2000FFCC` → `0x20005001`, epilogue `pop {..,pc}` executed, no forced PC below entry |
| **full RCE** | `zb05_rce.resc` | in-frame shellcode executes; magic `0x20017000` `0xDEADBEEF`→`0xC0DE1337` |

### RCE

The injected frame carries the 20-byte Cortex-M shellcode; the firmware's own
overflow copies it to `0x2000FF92` and overwrites the saved LR with `0x2000FF93`
(entry|Thumb bit); the genuine epilogue `pop` @`0xd6d2` loads PC from the
corrupted slot and the shellcode runs. (Shellcode starts at dst+2 because the
record[3] declared-length byte is the first copied byte at dst[0].)

## Reproduce

```
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/<script>.resc"
```

| Script | Success signal |
|---|---|
| `zb05_validate.resc` | `>>> AFTER … saved-LR … = 0x20005001` |
| `zb05_dispatch_inject.resc` | natural marker chain to `D540`; saved-LR `0x2000FFCC` = `0x20005001` |
| `zb05_rce.resc` | magic `0x20017000` = `0xC0DE1337` |

## Forced vs natural

ZB-05 needs **no** gate scaffolding — it is a static, direct call chain (this
corrects the report's earlier "RAM indirect dispatch" note). The single injection
entry (`sub_6482`) bypasses NWK/APS crypto = joined-device model; the whole chain
below the entry, including the epilogue that reaches the shellcode, runs on the
firmware's own code with no forced PC. Shellcode is carried in the frame.

## Files
- `scripts/zb05_validate.resc` — function-level sink.
- `scripts/zb05_dispatch_inject.resc` — dispatch-injection reachability.
- `scripts/zb05_rce.resc` — full RCE (shellcode exec).
- ELF `zb05_niko.elf` — present in this folder; git-ignored (not committed). Also in `scripts/silabs-vuln/`.
- OTA `switchx2-113B0006.ota` — native Zigbee OTA distribution image (git-ignored);
  the ELF above is its extracted executable. Source: `zigbeeFirmware.zip` (koenkk
  mirror; same image also in `vuln-reports.zip`). Validated-ELF sha256 `c3215f42…`
  — byte-identical to the corpus copy.
