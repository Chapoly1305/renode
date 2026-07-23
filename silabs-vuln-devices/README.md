# Silicon Labs EFR32 — per-device emulation-validation dossiers (Zigbee)

One folder per **vendor**, one sub-folder per **device/finding**. Each device
folder holds a `README.md` (the device dossier: bug, what was validated, exact
reproduction + expected output, limitations) and a `scripts/` sub-folder with the
Renode `.resc` scripts that produce the results.

This tree covers the **5 Zigbee findings** (ZB-02/03/04/05/06). The Matter (MG24)
findings are out of scope here; their scripts still live in `scripts/silabs-vuln/`
(`mt0*_validate.resc`) and their rows remain in the master report.

These are **derived** dossiers. The canonical scripts live in
`scripts/silabs-vuln/`; the copies here are for self-contained per-device reading
and are byte-identical. The master cross-device report is
[`scripts/silabs-vuln/VALIDATION_REPORT.md`](../scripts/silabs-vuln/VALIDATION_REPORT.md).

**Firmware is git-ignored and NOT redistributed.** A copy of each device's `*.elf`
is kept in its folder for self-contained local reference; the 5 Zigbee devices also
carry their **native `.ota` distribution image** (the `.elf` is its extracted
executable). Every validated ELF is byte-identical (sha256-verified) to its entry
in the upstream firmware corpus `zigbeeFirmware.zip` (koenkk/zigpy OTA mirrors);
per-device provenance + hashes are in each dossier. Note: none of these devices
ships as a raw `.bin` — the `.bin` files in the corpus belong to other (Tuya)
devices. Scripts load images from the canonical `@scripts/silabs-vuln/<name>.elf`
path, so run **from the repo root**:

```
./renode --console --disable-xwt --plain -e "include @scripts/silabs-vuln/<script>.resc"
```

(The copied scripts use repo-root `@…` paths, so they run unchanged regardless of
which device folder you read them from.)

## Index

| Vendor | Finding | Device | Chip | Class | Highest result demonstrated |
|---|---|---|---|---|---|
| **Aqara** | [ZB-02](Aqara/ZB-02-aqara-feeder/) | pet feeder | MG21 | heap OOB write | write primitive + allocator-misdirection demo |
| **Aqara** | [ZB-04](Aqara/ZB-04-aqara-n0agl1/) | switch n0agl1 | MG13 | constrained stack overflow | **DoS proven** (fault vector taken) |
| **Innr** | [ZB-03](Innr/ZB-03-innr-plug/) | plug | MG21 | stack → saved-LR | **full RCE** (shellcode exec) |
| **Niko** | [ZB-05](Niko/ZB-05-niko-switch/) | switch ×2 | MG21 | stack → saved-LR | **full RCE** (shellcode exec) |
| **Heiman** | [ZB-06](Heiman/ZB-06-heiman-hs1sa/) | HS1SA smoke | MG21 | stack → saved-LR + PC hijack | **full RCE** + APS-route + real-radio-to-MAC |

## Validation levels (what each term means in the dossiers)

- **function-level** — the sink is entered directly with the exact register/stack
  contract its gate uses; proves the memory-safety primitive on real firmware
  bytes. Does not prove gate reachability.
- **dispatch injection** — an already-decrypted plaintext frame is injected at the
  firmware's ZCL/APS dispatch entry; the firmware's **own** code routes it through
  the gate to the sink with **no forced PC** below the entry. Closes the
  *gate-reachability* and *parameter-fidelity* false-positive axes. (All 5 Zigbee.)
- **full RCE** — the injected frame carries Cortex-M shellcode; the firmware's own
  overflow copies it in and overwrites the saved LR, and the genuine epilogue
  `pop {..,pc}` executes it (writes magic `0xC0DE1337` to `0x20017000`).
- **DoS proven** — the corrupted return address is popped and the CPU is shown to
  take a fault via the live vector table.

The one layer NOT crossed dynamically for any Zigbee device is **NWK/APS
decryption + network-key membership** — which is exactly the documented
exploitability precondition ("requires network-key membership"). Everything after
decryption is shown reachable by the firmware's own code.
