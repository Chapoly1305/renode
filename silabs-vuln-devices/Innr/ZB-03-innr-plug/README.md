# ZB-03 — Innr plug (stack → saved-LR → full RCE)

| | |
|---|---|
| Finding | ZB-03 |
| Vendor / model | Innr plug |
| Chip / board | EFR32MG21 (Cortex-M33) · `brd4180a.repl` |
| ELF | `zb03_innr.elf` (present in this folder; git-ignored — also in `scripts/silabs-vuln/`) |
| Class | stack overflow → saved return-address overwrite (full 4-byte PC control) |
| Sink | `sub_22E06` (manufacturer command `0x8004`, case body `0x22E5C`) |
| Gate | `sub_C5F2` (ZCL cmd dispatch) → tries `sub_22BD8`, falls through → `sub_22E06` entry gate (table lookup + cmd 0x8004) |
| Highest result | **full working RCE** (shellcode executes) |

## The bug

The case-`0x8004` body reads count `a3[11]` **unclamped** and writes halfword
records via `strh.w r8,[r11,r7,lsl #1]` @`0x22ee2` into a stack buffer at SP_f+0x24.
Halfword index 56 lands on `sub_22E06`'s own saved LR, so count ≥ 58 overwrites the
return address with attacker bytes `a3[124..127]` — full 4-byte control.

## What was validated

| Level | Script | Result |
|---|---|---|
| function-level | `zb03_validate.resc` | saved-LR `0x20006FFC` → `0x20006E01` (full control) |
| dispatch injection | `zb03_dispatch_inject.resc` | plaintext mfg-0x8004 frame at `sub_C5F2`; firmware's own dispatch (C5F2→C64A→22E06 entry gate) routes to the sink; saved-LR `0x20006FBC` → `0x20006E01`, no forced PC below entry |
| **full RCE** | `zb03_rce.resc` | in-frame shellcode executes; magic `0x20017000` `0xDEADBEEF`→`0xC0DE1337` |

### RCE

The injected frame carries a 20-byte Cortex-M shellcode (`movw/movt r0=0xC0DE1337;
movw/movt r1=0x20017000; str r0,[r1]; b .`). The firmware's own overflow copies it
to `0x20006F4C` and overwrites the saved LR with `0x20006F4D` (entry|Thumb bit);
the genuine epilogue `pop` @`0x22fce` loads PC from the corrupted slot — no forced
PC after the injection entry — and the shellcode runs.

**ZB-03 caveat (documented, honest):** unlike ZB-05/06 which reach their epilogue
unconditionally, ZB-03's sink makes an indirect `blx entry[4]` (the registered
mfg-command handler) before its epilogue, so the natural return additionally
requires `entry[4]` seeded to a returning handler (realistic — a registered
handler returns to its dispatcher — but a heavier assumption than the other two).
`scripts/silabs-vuln/zb03_step0_diag.resc` proves that precondition in isolation.

## Reproduce

```
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/<script>.resc"
```

| Script | Success signal |
|---|---|
| `zb03_validate.resc` | `>>> AFTER … saved-LR … = 0x20006E01` |
| `zb03_dispatch_inject.resc` | `MARK_22E5C_GATE_OK` fires; saved-LR `0x20006FBC` = `0x20006E01` |
| `zb03_rce.resc` | magic `0x20017000` = `0xC0DE1337` |

The natural-epilogue precondition (`entry[4]` = a returning handler) is proven in
isolation by `scripts/silabs-vuln/zb03_step0_diag.resc`.

## Forced vs natural

`zb03_dispatch_inject.resc` pre-seeds the mfg-command table row + its lazy-init
flag (populated at endpoint registration on a real device). The single injection
entry (`sub_C5F2` with the stack's register contract) bypasses NWK/APS crypto =
joined-device model; everything below the entry — dispatch, entry gate, case
selection, the unclamped write loop, the epilogue — runs on the firmware's own
code with no forced PC. Shellcode is carried in the frame and written by the
firmware's own copy loop.

## Files
- `scripts/zb03_validate.resc` — function-level sink.
- `scripts/zb03_dispatch_inject.resc` — dispatch-injection reachability.
- `scripts/zb03_rce.resc` — full RCE (shellcode exec).
- Natural-epilogue-precondition diagnostic (`zb03_step0_diag.resc`) lives in `scripts/silabs-vuln/`.
- ELF `zb03_innr.elf` — present in this folder; git-ignored (not committed). Also in `scripts/silabs-vuln/`.
- OTA `1166-0313-31016610-upgradeMe.ota` — native Zigbee OTA distribution image
  (git-ignored); the ELF above is its extracted executable. Source:
  `zigbeeFirmware.zip` (koenkk mirror). Validated-ELF sha256 `a15a0f7a…` —
  byte-identical to the corpus copy.
