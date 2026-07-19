# Matter fake-transport commissioning — status & handoff

_Last updated: 2026-07-19_

Goal: drive a host `chip-tool` to commission a SiLabs Matter lighting-app running in
Renode over a software **fake CHIPoBLE transport** (BLE/802.15.4 PHY bypassed), completing
PASE and beyond.

## TL;DR status

- **Transport: works end-to-end.** Real `chip-tool` (Docker) ↔ emulated device: BLE connect,
  full BTP handshake, and the entire SPAKE2+ PASE message exchange
  (`PBKDFParamRequest→Response`, `Pake1→Pake2`) all flow bidirectionally.
- **One blocker remains — and it is in the Renode Secure-Engine model, not in Matter and not
  in the credentials.** PASE fails at `Pake2` with `[SC] Failed to verify peer's MAC`
  (`CHIP Error 0x000000AC`).
- **Root cause (proven): Renode's `semailbox` does not implement HMAC.** During PASE the log
  shows exactly once per attempt:
  `[ERROR] semailbox: ProcessCommand(): Command ID 0x302 not handled!`
  SE command `0x302` = `HashHmac` — it is *declared* in the `CommandId` enum of
  `SiLabs_SecureElement.cs` but has **no `case` in the dispatch switch**. The device's
  SPAKE2+ key-confirmation MAC (and HKDF) are HMAC-SHA256 on the Secure Engine, so they come
  back wrong → `cB` mismatches → chip-tool's verification fails.

## Why credentials are NOT the problem (this closes the old NVM3 rabbit hole)

- The three PASE getters the device uses (`Storage` vtable, resolved by disassembly) return
  **compiled test constants**, not NVM3: `GetSpake2pVerifier`→ standard base64 verifier,
  `GetSpake2pSalt`→ `"SPAKE2P Key Salt"`, `GetSpake2pIterationCount`→ 1000. The NVM3 reads
  happen but their results are discarded. **NVM3 provisioning is irrelevant here.**
- chip-tool's own `--trace_decode` confirms the device advertised the exact standard values:
  `Iteration Count = 1000`, `Salt (16) = 5350414B453250204B65792053616C74` ("SPAKE2P Key Salt").
  With passcode 20202021 these match the stored verifier by construction. Credentials are
  provably correct; the MAC still fails → the failure is device-side crypto computation.

## The fix (next step to "finish the emulation")

Implement `CommandId.HashHmac` (0x302) in
`src/Infrastructure/.../Miscellaneous/SiLabs/SiLabs_SecureElement.cs`:
add a `case CommandId.HashHmac:` calling a new `HandleHashHmacCommand(...)` that does
HMAC over the SE DMA descriptors (key descriptor + message descriptor → MAC output),
using BouncyCastle `HMac(new Sha256Digest())` (mirror `HandleAesCmacCommand` for the
descriptor/DMA plumbing and `HandleHashCommand` for the SHA-mode selection). Then rebuild
the Renode `Infrastructure` and re-run the repro below; PASE should clear the MAC step.
The SE fix lives in the Renode repo (this is `src/Infrastructure`, a submodule).

## Reproduction (verified working harness)

Renode (host, macOS, real-time), from the Renode root:
```
./renode --disable-xwt --port 3456 \
  -e "include @<worktree>/scripts/complex/silabs/matter-fake-transport.resc"
```
- Loads `matter-silabs-lighting-example.out`, exposes socket `*:3500` ↔ EUSART1.
- Firmware: `chip_enable_fake_ble_transport=true` build (see below).

Docker (`matter-chiptool`, chip-build:200, amd64):
```
# relay: chip-tool hardcodes 127.0.0.1 -> forward to the host's Renode
python3 /tmp/loopback_relay.py 3500 host.docker.internal 3500 &   # -> 192.168.65.254:3500 (IPv4)
CHIP_FAKE_BLE_PORT=3500 /tmp/out/chip-tool-fake-ble/chip-tool \
  pairing ble-thread 1 hex:<dataset> 20202021 3840 --bypass-attestation-verifier 1
```
Watch for the transition PAST `Failed to verify peer's MAC` — that is the success signal for
the SE fix. (Discovery is bypassed by the fake transport, so the discriminator is cosmetic;
only the passcode matters.)

### Pairing code vs PIN vs discriminator
- Manual code `34970112332` = passcode **20202021** + discriminator **3840** (matches this firmware).
- Manual code `32000638873` = passcode 63688230 + discriminator 3328 (a *different* device).
- Equivalent forms: `... 20202021 3840`  or  `pairing code <node> hex:<ds> 34970112332`.

## Pinned Matter base (per request: master is fine)

- connectedhomeip fork branch: **`chapoly1305/fake-ble-transport` @ `e2ef82f369`**
  (base `master` @ `6137e726` = `v1.4.2.0-3211-g6137e72682`).
- Self-contained diff also captured at `matter/patches/connectedhomeip-fake-ble-transport.patch`.
- Firmware build recipe:
  `./scripts/run_in_build_env.sh './scripts/examples/gn_silabs_example.sh
   examples/lighting-app/silabs out/lighting-fake-ble BRD2601B chip_enable_fake_ble_transport=true'`

## Debug/cleanup owed before upstreaming

- `src/ble/BLEEndPoint.cpp`: BTP endpoint debug `#define` (revert).
- The `ProvisionStorageDefault.cpp` getters force compiled test creds unconditionally — fine
  for emulation, but gate/revert for production.
- `examples/all-clusters-app/linux/*` shell-command changes in the working tree are a
  SEPARATE experiment and are intentionally NOT part of the `fake-ble-transport` branch.
