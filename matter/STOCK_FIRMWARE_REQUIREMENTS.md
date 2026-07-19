# Commissioning STOCK (unmodified) SiLabs Matter firmware in Renode — requirements

_Authoritative scope note, 2026-07-19._

## Hard constraint (the actual requirement)

**The device firmware is used exactly as shipped: no recompile, no GN/compile flags, no source edits.
The binary is typically STRIPPED.** Therefore **all** work must be **emulator-side** (Renode peripheral
models) and **host-side** (bridges + tooling). Nothing may be added to, removed from, or reconfigured in
the device image.

## ⛔ Retired: the "fake transport" approach (do NOT use — it violates the constraint)

An earlier line of development got commissioning working end-to-end, but ONLY by **modifying and
recompiling the device**, which is not allowed. Every part of it is a device-side change and is hereby
retired:

| Retired artifact | Why non-compliant |
| --- | --- |
| `chip_enable_fake_ble_transport` + `src/platform/silabs/efr32/FakeBLETransport.cpp` | device recompile + new device source |
| `chip_enable_fake_thread_radio` + `matter_support/.../radio_fake.cpp` | device recompile + new device source |
| `chip_enable_fake_operational_transport` + `src/transport/raw/FakeOperational.*` | device recompile + new device source |
| Host `AddressResolve` short-circuit + `FakeBleTransport.cpp` operational hooks | only exist to serve the fake device transport |

That code remains on branch `chapoly1305/fake-ble-transport` (and in `patches/`) **for reference only** —
as a record of what NOT to do. Do not build against it for the real objective.

## ✅ Retained: the Renode Secure-Engine crypto model

`src/Infrastructure/.../Miscellaneous/SiLabs/SiLabs_SecureElement.cs` (HMAC / streaming SHA-256 /
ECDSA sign+verify / multipart AES-CCM, etc.; captured in `patches/renode-secure-element-crypto.patch`) is
**emulator-side** — it makes the *unmodified* firmware's SEMAILBOX calls compute correctly. It is fully
compliant and must be kept. Its command ABI is defined by the SiLabs SDK, so it carries across stock
images built on the same SDK; re-verify command IDs if the target uses a different SDK version.

## Correct architecture (what the emulator must provide instead)

The stock image drives the **real** `sl_bt` BLE stack and **real** RAIL 802.15.4 through the SoC radio
peripherals. To commission an unmodified image, Renode must emulate those faithfully and bridge the air
interface to the host `chip-tool`:

1. **CHIPoBLE (commissioning path).** Model the BLE radio (LPW) + PROTIMER well enough for the stock
   `sl_bt` stack to *advertise* and *accept a GATT connection*, then bridge the CHIPoBLE air interface out
   to the host so a real `chip-tool` connects.
   - **Known blocker:** a one-shot PROTIMER advertising bug in the BLE radio model
     (`SiLabs_xG24_LPW.cs`, ~line 1219) — advertising fires once and stops. Must be fixed first.
   - **Bridge options:** a virtual HCI/controller the host BLE stack attaches to, or a socket carrying
     BLE link-layer PDUs into a host shim. (This replaces the retired byte-pipe transport.)
2. **Thread / 802.15.4 (operational path).** Complete the RAIL 802.15.4 radio model (currently
   stubbed/prebuilt) and bridge it to a host Thread network (socket ↔ RCP ↔ `otbr`) so the device joins a
   real network and `chip-tool` reaches it operationally via a border router + DNS-SD. No device-side
   "null radio" — the stock stack must run against a working (emulated) radio.
3. **Secure element.** SEMAILBOX → `SiLabs_SecureElement` (retained, above).
4. **Supporting peripherals** the stock image touches: PROTIMER, RADIOAES, FRC, SYSRTC, DCDC, etc. —
   modeled to whatever fidelity the stack requires (today several are stubbed and emit warnings).
5. **Time sync.** Real-time is simplest but timing-flaky under host CPU contention; virtual-time
   (external-control server + `librenode_api.so` clock slaving) is the robust option.

## Per-firmware information the user must provide

1. **The stock firmware image** (ELF/`.s37`/`.hex`). Stripped is fine to *run* (`sysbus LoadELF` loads by
   address). For debugging, also provide the linker `.map` or an unstripped ELF (`sysbus LoadSymbolsFrom`),
   or accept locating the RTT console by scanning RAM for the ASCII magic `"SEGGER RTT\0\0"`.
2. **Exact SoC + board + radio variant** (e.g. EFR32MG21/22/24/26, brd####x) → the matching Renode `.repl`
   modeling every peripheral the image touches (BLE/LPW radio, RAIL, SEMAILBOX, PROTIMER, RADIOAES, FRC…).
3. **Gecko/Simplicity SDK + Matter stack version** the image was built against — peripheral register maps
   and the SEMAILBOX command ABI in the models must match.
4. **The device's real commissioning credentials.** Stock firmware reads its SPAKE2+ verifier / salt /
   iteration count / discriminator from the **factory NVM3 partition**, not test constants. So:
   (a) that factory/manufacturing partition must be present in the emulated flash image (either baked into
   the provided image or supplied separately + `LoadBinary` at the board's NVM3 base), and (b) the user
   must provide the matching **QR or manual pairing code** (passcode + discriminator) for that device.
5. **Network commissioning parameters** the operator will use (Thread operational dataset for
   `pairing ble-thread`, or Wi-Fi creds for `ble-wifi`).

## What a stripped binary specifically costs

- **Running is unaffected** — the firmware executes; commissioning needs no symbols.
- **Debugging/introspection** needs the `.map`/unstripped ELF (RTT address, PC→function mapping,
  peripheral-access tracing). Without it, RTT can still be found via the `"SEGGER RTT"` RAM magic, but
  symbol-based tracing is unavailable.

## Bottom line

Under "no device changes," this is fundamentally a **Renode radio-emulation + host-bridge** project, not a
firmware-shim project. The gating unknown is the BLE radio (PROTIMER advertising) for the commissioning
path; the 802.15.4/Thread radio + border-router bridge is the operational path. The SE crypto model is the
only piece of prior work that carries forward.
