# Matter fake-transport commissioning — status & handoff

> ⛔ **RETIRED (2026-07-19).** The real requirement is to commission the device firmware **exactly as
> shipped — no recompile, no compile flags, no source edits (stripped binary).** The entire fake-transport
> approach documented below required *modifying and recompiling the device*, so it does NOT meet the
> constraint and is retired (kept for reference only). The one piece that carries forward is the
> emulator-side Renode Secure-Engine model. For the correct, compliant scope and the per-firmware inputs a
> user must provide, see **`STOCK_FIRMWARE_REQUIREMENTS.md`**.

_Last updated: 2026-07-19_

Goal: drive a host `chip-tool` to (1) **commission** a SiLabs Matter lighting-app running in Renode over
a software **fake CHIPoBLE transport** (BLE PHY bypassed), (2) get the device **operating on a Thread
network**, and (3) **interact with it operationally** from chip-tool (CASE + cluster commands).

## TL;DR — ALL THREE GOALS WORK (end-to-end, verified)

A full `chip-tool pairing ble-thread` run reaches `CHIP_TOOL_EXIT=0` (commissioning COMPLETE). Verified
from the chip-tool log:

```
OperationalSessionSetup[1:...]: Updating device address to FAKE-OP        <- discovery short-circuit
Msg TX ... [FAKE-OP] --- Type 0000:30 (SecureChannel:CASE_Sigma1) (B:196) <- operational CASE over the pipe
Received Sigma2 msg (B:747)                                               <- device replied (747B)
Msg TX ... [FAKE-OP] --- Type 0000:32 (SecureChannel:CASE_Sigma3) (B:594)
OperationalSessionSetup[1:...]: State change 4 --> 5                      <- operational CASE established
Received CommissioningComplete response, errorCode=0                      <- CommissioningComplete OK
Commissioning complete for node ID 0x0000000000000001: success
```

1. **BLE commissioning** — PASE → attestation → CSR → AddNOC → CASE(BLE) → Thread provisioning. ✅
2. **Operating on Thread** — `ThreadNetworkEnable` returns `networkingStatus=0`; the device self-promotes
   to **Leader** on its own single-node Thread network (fake 802.15.4 radio). ✅
3. **Operational chip-tool interaction** — operational CASE (Sigma1/2/3) + `CommissioningComplete` run
   over a **fake operational transport** (there is no other path: no border router / SRP server / DNS-SD
   on this single-node device). ✅

## The three subsystems that made it work

### A. Renode Secure-Engine crypto (in the sl-renode repo)
`src/Infrastructure/.../Miscellaneous/SiLabs/SiLabs_SecureElement.cs` — HMAC (0x302), streaming SHA-256
fix, ECDSA sign/verify (0x600/0x601), multipart AES-CCM (0x405/0x406), AES-CCM empty-payload, try/catch.
Saved as `matter/patches/renode-secure-element-crypto.patch`. Build the C# only:
`dotnet build src/Infrastructure/src/Infrastructure_NET.csproj -c Release` then
`cp src/Infrastructure/src/bin/Release/net8.0/Infrastructure.dll output/bin/Release/Infrastructure.dll`.

### B. connectedhomeip firmware + host (branch `fake-ble-transport`)
- Fake CHIPoBLE transport (device `FakeBLETransport.cpp` on EUSART1 ↔ host `FakeBleTransport.cpp` socket).
- **Fake Thread radio** (`chip_enable_fake_thread_radio`): `radio_fake.cpp` null radio swapped for the real
  RAIL `radio.cpp` in `third_party/openthread/platforms/efr32/BUILD.gn` — device times out MLE and becomes
  Leader. (`radio_fake.cpp` is in the `matter_support` submodule.)
- **Non-blocking fake-BLE read**: use `UARTDRV_Receive` (async) not `UARTDRV_ReceiveB` (busy-spin) so the
  FakeBLE task yields and OpenThread gets CPU to attach.
- **Fake operational transport** (`chip_enable_fake_operational_transport`): `Transport::FakeOperational`
  (`src/transport/raw/FakeOperational.{h,cpp}`, PeerAddress `kFakeOperational`) carries operational CHIP
  messages over the SAME EUSART1/socket pipe as the BLE frames, multiplexed by frame type **0x10**.
  Registered in the device `ServerTransportMgr` and the host controller `DeviceTransportMgr`; chip-tool's
  operational discovery is short-circuited to the static `kFakeOperational` peer in
  `AddressResolve_DefaultImpl.cpp`. Gated by the global `CHIP_ENABLE_FAKE_OPERATIONAL_TRANSPORT` macro.

Commits: `e07567aa14` (fake radio + non-blocking read), `185dcb9d6f` (fake operational transport),
matter_support submodule `0629637a` (radio_fake.cpp). Earlier fake-BLE work: `82fa2adcea` and prior.

### C. Renode scenario + Docker harness (in the sl-renode-matter worktree)
- `scripts/complex/silabs/matter-fake-transport.resc` — loads the firmware, exposes `*:3500 ↔ EUSART1`,
  real-time mode, mutes the fake-radio warning flood.
- `matter/docker/loopback_relay.py` — resilient TCP relay (container `127.0.0.1:3500` → host Renode).
- `matter/commission_retry.sh` — host-side reliable driver (see reliability note below).

## Build recipe

```bash
# Firmware (from /Volumes/Tools/connectedhomeip):
./scripts/run_in_build_env.sh './scripts/examples/gn_silabs_example.sh \
  examples/lighting-app/silabs out/lighting-fake-ble BRD2601B \
  chip_enable_fake_ble_transport=true chip_enable_fake_thread_radio=true \
  chip_enable_fake_operational_transport=true'

# Host chip-tool (Linux, in the matter-chiptool container; source bind-mounted at /workspace):
./scripts/examples/gn_build_example.sh examples/chip-tool /tmp/out/chip-tool-fake-ble \
  chip_enable_fake_ble_transport=true chip_enable_fake_operational_transport=true
# 7.7GB container OOMs at full parallelism -> finish with: ninja -C /tmp/out/chip-tool-fake-ble -j2
```

## Reproduction

```bash
# Host (macOS, real-time), from the Renode root:
./renode --disable-xwt --port 3456 -e "include @<worktree>/scripts/complex/silabs/matter-fake-transport.resc"

# Reliable driver (restarts Renode + runs the container pairing until it completes):
<worktree>/matter/commission_retry.sh 15
```
Manual single attempt (container `matter-chiptool`): `bash /tmp/commission.sh` (starts the relay, then
`chip-tool pairing ble-thread 1 hex:<dataset> 20202021 3840 --bypass-attestation-verifier true`).

## ⚠ Reliability caveat (real-time emulation)

The emulation runs in REAL TIME, so the crypto-heavy CHIPoBLE data phase and the ~11s Thread attach are
timing-sensitive. A single attempt frequently times out at a random step (DAC/PAI cert read, or
ThreadNetworkEnable), and the success rate DEGRADES as the Mac warms/loads over a long session. Each
attempt is independent — **retry a fresh Renode** (`commission_retry.sh`). The robust fix (NOT yet built)
is **virtual-time mode**: slave chip-tool's clock to Renode's virtual time (`CreateExternalControlServer`
+ `librenode_api.so` LD_PRELOAD + `SetGlobalAdvanceImmediately true`) so chip-tool waits for the slow
device instead of timing out. Memory notes it's racy (the preload overrides only clock_gettime/
gettimeofday, not poll/select).

## Follow-ups
- **Virtual-time mode** for deterministic reliability (above).
- **Standalone operational commands** (`onoff toggle 1 1` as a separate chip-tool process, no BLE phase):
  the fake operational transport currently shares the pairing socket, so it's proven for CommissioningComplete
  WITHIN a `pairing` run. A standalone command needs the socket owned independently of the BLE connection
  (shared socket manager / reconnect on demand).
- Revert debug/dead code before any upstreaming (BLEEndPoint.cpp `#define`, ProvisionStorageDefault getters).

### Pairing code vs PIN vs discriminator
- Manual code `34970112332` = passcode **20202021** + discriminator **3840** (this firmware).
- Manual code `32000638873` = passcode 63688230 + discriminator 3328 (a *different* device).
- The fake transport bypasses discovery, so the discriminator is cosmetic; only the passcode matters.
