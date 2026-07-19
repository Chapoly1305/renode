# Matter commissioning against an emulated SiLabs target (fake CHIPoBLE transport)

Drive a host `chip-tool` through **CHIPoBLE commissioning** of a SiLabs Matter lighting-app running
inside Renode — **no BLE radio/PHY emulation involved**. A software "fake transport" in the firmware
carries CHIPoBLE frames over a UART that Renode bridges to a TCP socket; a `chip-tool` built with the
matching host-side fake transport connects to that socket and commissions the device.

```
  chip-tool (fake transport)  --TCP-->  Renode CreateServerSocketTerminal  <-->  EUSART1  <-->  firmware FakeBLETransport task
        (host, Linux)                     (server, port 3500)                              (CHIPoBLE state machine)
```

## Scope

- **Phase 1 (this deliverable): commissioning only.** The fake transport carries CHIPoBLE frames
  (`CONNECT/DISCONNECT/WRITE_REQUEST(C1)/SUBSCRIBE/UNSUBSCRIBE/INDICATION(C2)`) — i.e. the **PASE
  pipe** plus the commissioning-cluster exchanges that ride the PASE session, including Thread
  operational-dataset delivery. It does **not** carry operational Matter traffic (CASE + clusters),
  which normally travels over UDP/IP on the Thread network. Verification therefore covers
  commissioning up to the **Thread operational handoff**.
- **Phase 2 (follow-on, not built): Thread operational.** Real cluster commands (`onoff toggle`) need
  an IP/Thread path to the device — an emulated Thread Border-Router node + host TAP bridge (or a
  socket 802.15.4 bridge). See "Phase 2" at the bottom.

## Files

| File | Purpose |
| --- | --- |
| `scripts/complex/silabs/matter-fake-transport.resc` | Reusable Renode scenario: boot firmware, expose the fake-transport socket on EUSART1, run in real time. Interactive / macOS-friendly. |
| `tests/platforms/SiLabs/matter_commissioning.py` | pyrenode3 harness (Linux CI): boots the device, launches `chip-tool`, asserts commissioning engages and reaches the Thread handoff. |
| `matter/fake_transport_probe.py` | Standalone smoke test: speaks the raw fake-transport frame protocol to confirm the socket↔EUSART1 bridge without a full `chip-tool`. Runs anywhere (incl. macOS). |

## Prerequisites

> The connectedhomeip fake-transport source (device + host) plus the two fixes made during bring-up
> are captured as an applyable patch in [`patches/`](patches/README.md) — apply it to a clean
> connectedhomeip checkout before the builds below.

### 1. Build the device firmware (fake transport enabled)

The stock `.out` images are **not** usable — they use the real `sl_bt` GATT stack. Build with the flag
(existing `declare_args()` in `src/platform/silabs/efr32/BUILD.gn`, default `false`):

```bash
cd /Volumes/Tools/connectedhomeip
./scripts/run_in_build_env.sh \
  './scripts/examples/gn_silabs_example.sh examples/lighting-app/silabs \
      out/lighting-fake-ble BRD2601B chip_enable_fake_ble_transport=true'
# => out/lighting-fake-ble/thread/BRD2601B/matter-silabs-lighting-example.out
```

### 2. Build the host `chip-tool` (fake transport enabled) — Linux only

The host-side fake transport lives in `src/platform/Linux/ble/FakeBleTransport.cpp` and is gated by
`chip_enable_fake_ble_transport` in `src/platform/Linux/BUILD.gn`. It is **Linux-only** (the Darwin
build uses CoreBluetooth), so end-to-end commissioning must run on Linux:

```bash
cd connectedhomeip
./scripts/examples/gn_build_example.sh examples/chip-tool out/chip-tool-fake-ble \
    chip_enable_fake_ble_transport=true
# => out/chip-tool-fake-ble/chip-tool
```

## Run

### A. Interactive / smoke test (works on macOS)

```bash
# 1. Start Renode with the scenario (override $bin if your .out is elsewhere):
./renode scripts/complex/silabs/matter-fake-transport.resc
#    Watch for: "FakeBLETransport: listening on EUSART1 for CHIPoBLE frames"

# 2. In another terminal, probe the transport (no chip-tool needed):
python3 matter/fake_transport_probe.py --port 3500
#    Expect the Renode console to log "FakeBLETransport: CONNECT" then "FakeBLETransport: SUBSCRIBE".
```

This confirms the emulator side: socket↔EUSART1 bridge + firmware FakeBLETransport task are live.

### B. Full commissioning (Linux)

```bash
CHIP_TOOL=connectedhomeip/out/chip-tool-fake-ble/chip-tool \
MATTER_DATASET=<hex-thread-operational-dataset> \
python3 tests/platforms/SiLabs/matter_commissioning.py \
    --board brd2601b --uart eusart0 \
    --elf connectedhomeip/out/lighting-fake-ble/thread/BRD2601B/matter-silabs-lighting-example.out
```

The harness boots the device, launches `chip-tool pairing ble-thread ...` (with `CHIP_FAKE_BLE_PORT`
set), and asserts on **`chip-tool`'s own stdout** — the host `FakeBleTransport` connection markers,
PASE progress, and Thread-dataset delivery. (This firmware logs via SEGGER RTT, not a UART, so
device-side ChipLog output is not observable from Renode; `--uart` only satisfies the tester
argument.)

## Time synchronization

A real `chip-tool` measures MRP/PASE retransmit timeouts on a wall clock; Renode runs virtual time.
Two supported modes:

- **Real-time (default in the `.resc`)**: `SetGlobalAdvanceImmediately false` makes virtual time track
  wall time, so timeouts line up naturally. No shim needed; **works on macOS**. Requires the emulator
  to keep up with real time (it does for this firmware on a modern host).
- **Virtual-time slaving (CI / `.py` harness)**: `test_lib.create_emulation` runs
  `SetAdvanceImmediately(true)` and `launch_host_process` runs `chip-tool` under
  `LD_PRELOAD=tools/external_control_client/lib/librenode_api.so` + `RENODE_PORT`, so `chip-tool`'s
  `clock_gettime`/`gettimeofday` return Renode virtual time. Note the shim overrides those calls only
  (not `poll`/`select`), so this is Linux-only and best paired with the emulator free-running.

## Running chip-tool from Docker (Linux controller, Renode on the host)

The fake-transport `chip-tool` is Linux-only, so on macOS run it in a container while Renode runs on
the host. `FakeBleTransport.cpp` hard-connects to `127.0.0.1:$CHIP_FAKE_BLE_PORT` inside its own
netns, so the container must reach Renode's socket over loopback:

- **Linux host:** run the container with `--network host` — `127.0.0.1:3500` is shared, no relay.
- **macOS/Windows host:** run a loopback relay inside the container forwarding
  `127.0.0.1:3500 → host.docker.internal:3500`.

Renode stays in **real-time mode** (the `.resc` default) so no `librenode_api.so` time shim is needed.

**One-command helper** (does all of the below): with Renode already running,
`matter/docker/run-chip-tool.sh <thread-dataset-hex>` creates the container, builds chip-tool if
needed, starts `matter/docker/loopback_relay.py`, and runs `pairing ble-thread`.

> **Apple Silicon note:** `chip-build:200` is an **amd64-only** image, so it runs under Docker
> Desktop's Rosetta emulation on M-series Macs — the first chip-tool build is noticeably slower than
> native. It only needs to be built once (persisted in the container).

```bash
# On the host: start Renode with the scenario (exposes tcp/3500).
./renode scripts/complex/silabs/matter-fake-transport.resc

# In a container (connectedhomeip mounted). Tag matches this checkout's chip-build.
docker run -it --rm \
    -v /Volumes/Tools/connectedhomeip:/workspace -w /workspace \
    ghcr.io/project-chip/chip-build:200 bash

#   --- inside the container ---
# 1. Build chip-tool with the fake transport (first time; ~20-40 min):
./scripts/examples/gn_build_example.sh examples/chip-tool out/chip-tool-fake-ble \
    chip_enable_fake_ble_transport=true
# 2. macOS only: relay container-loopback to the host's Renode:
socat TCP-LISTEN:3500,fork,reuseaddr TCP:host.docker.internal:3500 &   # or the python relay below
# 3. Commission:
CHIP_FAKE_BLE_PORT=3500 out/chip-tool-fake-ble/chip-tool \
    pairing ble-thread 1 hex:<thread-dataset> 20202021 3840 --bypass-attestation-verifier true
```

If `socat` isn't in the image, use the shipped python relay instead (runs anywhere python3 is):

```bash
docker cp matter/docker/loopback_relay.py <container>:/tmp/
docker exec <container> bash -lc 'python3 /tmp/loopback_relay.py 3500 host.docker.internal 3500 &'
```

## Troubleshooting

- **No `FakeBLETransport: listening ...`** — firmware wasn't built with
  `chip_enable_fake_ble_transport=true`, or `$bin`/`--elf` points at a stock image.
- **`chip-tool` connects but PASE stalls** — check time mode (above); on Linux ensure
  `librenode_api.so` is built (`tools/external_control_client/lib`) and picked up via `LD_PRELOAD`.
- **Dropped/garbled frames** — the socket bridge is byte-transparent only in binary mode; the scenario
  uses `CreateServerSocketTerminal ... false`. UART-full drops are logged by the EUSART model
  (`SiLabs_EUSART_2.cs`).
- **No device-side logs in Renode** — expected: this firmware logs via SEGGER RTT, which Renode does
  not capture. Assertions use `chip-tool` stdout instead. For device-side visibility, rebuild the
  firmware with a UART iostream logging backend, or attach a debugger.
- **Confirming the bridge without chip-tool** — enable EUSART1 tracing in Renode
  (`logLevel 0 sysbus.eusart1`) and run `matter/fake_transport_probe.py`; RXDATA reads on EUSART1
  after the probe sends CONNECT confirm the firmware's FakeBLE task is consuming frames.

## Phase 2 — Thread operational (follow-on)

To exercise real operational cluster commands, add an IP/Thread path so `chip-tool` can reach the
device after commissioning: an emulated Thread Border-Router node bridged to the host (TAP), or a
socket-based 802.15.4 bridge to a host OpenThread border router. This reintroduces radio/medium work
intentionally out of Phase 1's scope.
