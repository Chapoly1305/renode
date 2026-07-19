# Renode × Stock SiLabs Matter — BLE Commissioning + Thread Operational — HANDOFF

**Date:** 2026-07-19  ·  **Author of prior work:** Renode/Matter emulation effort (macOS/Apple-M4)
**Next environment:** native **x86-64 Linux** (see §9 — the architecture gets much simpler there)

---

## 0. TL;DR — status at a glance

Goal: commission an **unmodified (stock) SiLabs EFR32MG24 Matter firmware** inside Renode over BLE, get it
onto a **Thread** network, and drive it operationally from **chip-tool** — with **no host radio hardware**.

| Capability | State |
|---|---|
| BLE (CHIPoBLE) commissioning of stock firmware: PASE → creds → CASE | ✅ **works E2E** |
| 802.15.4 PHY bridge to a host OpenThread air (both directions) | ✅ **works, validated** |
| Stock device **attaches to a real Thread network** (`ThreadNetworkEnable` succeeds) | ✅ **works** |
| **IP-over-Thread** datapath (device answers ICMPv6 ping over emulated radio) | ✅ **works** |
| chip-tool **operational** CASE + CommissioningComplete + cluster (`onoff`) | ⏳ **remaining** (needs otbr) |

Everything on the **device is 100% stock**. All work is **emulator-side (Renode C#)** + **host-side tooling**.
The only remaining piece is standing up an **OpenThread Border Router (otbr)** so chip-tool can reach the
device over real UDP-over-Thread. That's pure host-side plumbing — the emulator side is done.

---

## 1. Goal & hard constraints

- **Objective:** run stock SiLabs Matter firmware in Renode; commission over BLE; operate on Thread; interact
  with chip-tool operationally (cluster commands).
- **HARD CONSTRAINT — the device firmware is NOT modified.** No recompile, no GN/compile flags, no source
  edits. The platform must eventually run a **real stripped production binary**. All changes are:
  - **emulator-side:** Renode C# peripheral models (this is the real work), and
  - **host-side:** chip-tool / OpenThread / otbr tooling (modifying host tools is allowed).
- **No host radio hardware / no OS Bluetooth / no host 802.15.4.** Must run in CI / containers / any host.
- A prior "fake transport" approach that **modified + recompiled the device firmware** is **RETIRED** (kept
  only as reference for the host-side socket transport shape). Authoritative scope doc:
  `sl-renode-matter/matter/STOCK_FIRMWARE_REQUIREMENTS.md`.

---

## 2. The compliant architecture

```
                    ┌───────────────────────── Renode (one machine, virtual clock) ─────────────────────────┐
  host chip-tool    │  STOCK firmware: real sl_bt (BLE) + real OpenThread + RAIL 802.15.4                    │
  (fake-BLE xport)  │        │                                   │                                           │
        │  TCP 3500 │        │ CHIPoBLE GATT (real)              │ 802.15.4 PHY (real RAIL)                  │
        └───────────┼──▶ BleCentralBridge  ◀──BLE medium──▶  sysbus.radio  ◀──15.4 medium──▶ Ieee802154HostBridge
                    │   (in-emulation BLE central)                                    │  (in-emulation 15.4 node)│
                    └─────────────────────────────────────────────────────────────────┼────────────────────────┘
                                                                                        │ UDP multicast 224.0.0.116:9000
                                                                                        │ (OpenThread "simulation" air)
                                                                        ┌───────────────┴───────────────┐
                                                                        │  ot-rcp ◀── otbr-agent (BR)    │  ← REMAINING
                                                                        │  SRP server + mDNS + IPv6 route│
                                                                        └───────────────┬───────────────┘
                                                                        chip-tool (real operational UDP) ─┘
```

Key idea: two small **in-emulation IRadio peripherals** sit on Renode's wireless medium next to the stock
device's real radio and bridge its air interface out to software:
- **`BleCentralBridge`** = a BLE *central* that connects to the device's real GATT and relays CHIPoBLE C1/C2
  to a host chip-tool over a socket → BLE commissioning.
- **`Ieee802154HostBridge`** = an 802.15.4 node that relays the device's real RAIL frames to a host
  OpenThread "simulation" UDP air → Thread. A host otbr on that air gives operational reachability.

Both share the emulator's virtual clock, so timing is deterministic (given the CPU-speed fix in §7).

---

## 3. What works today — proven milestones + evidence

1. **BLE commissioning of stock firmware (E2E).** `chip-tool pairing ble-thread` → BTP handshake → PASE
   (SPAKE2+) → "Secure Pairing Success" → Attestation → CSR → AddNOC → CASE → `ThreadNetworkSetup`. Real
   `sl_bt` GATT + emulated SE crypto. Passcode **20202021**, discriminator **3840**.
2. **802.15.4 bridge both directions.** device→host: stock OpenThread TX's an MLE Parent Request on ch15/PAN
   0x1234; bridge auto-detects the 802.15.4 SFD (`0xA7`), extracts the MPDU, recomputes the CRC-16/KERMIT FCS,
   forwards to the OT-sim UDP air. host→device: injects frames back via `HandleTimeDomainEvent`.
3. **Thread attach (`ThreadNetworkEnable` SUCCEEDS).** With a host `ot-cli-ftd` leader on the air, chip-tool
   logs *"Successfully finished commissioning step 'ThreadNetworkEnable'"* and the leader's neighbor table
   shows the stock device as a live Thread **child/router** (e.g. RLOC16 `0xf403`).
4. **IP-over-Thread datapath.** From the leader, `ping <device mesh-local RLOC>` returns real ICMPv6 echo
   replies through the bridge (`16 bytes ... icmp_seq=11..15 hlim=64 time=22-38ms`). Proves 6LoWPAN/IPv6 +
   MAC-ACK + real-time timing all work end-to-end.

---

## 4. Repository layout & deliverables

### 4.1 Renode (the emulator-side code) — SUBMODULE
- Parent repo: `sl-renode` (branch `silabs/release/26q1`). Submodule: **`src/Infrastructure`**.
- Work branch in the submodule: **`renode/stock-firmware-ble-central`** — 3 commits on the 26q1 base:
  - `0d3d7dd37` BleCentralBridge + `SiLabs_xG24_LPW.cs` PROTIMER range-fix + retained `SiLabs_SecureElement.cs`
  - `5b69bff1f` `SiLabs_SEMAILBOX_1.cs` TXINT flow-control + `SiLabs_SYSRTC_1.cs` wraparound compare-limit
  - `bc2ff8131` `Ieee802154HostBridge`
- New/changed files (all under `src/Infrastructure/src/Emulator/Peripherals/Peripherals/`):
  - `Wireless/BleCentralBridge.cs`         — in-emulation BLE central (CHIPoBLE commissioning)
  - `Wireless/Ieee802154HostBridge.cs`     — in-emulation 802.15.4 ↔ OT-sim UDP bridge
  - `Wireless/SiLabs_xG24_LPW.cs`          — radio model (PROTIMER range-fix; the radio path itself is stock)
  - `Miscellaneous/SiLabs/SiLabs_SecureElement.cs` — SE crypto model (HMAC/SHA/ECDSA/AES-CCM) — REQUIRED
  - `Miscellaneous/SiLabs/SiLabs_SEMAILBOX_1.cs`   — SE mailbox TXINT flow-control fix
  - `Timers/SiLabs_SYSRTC_1.cs`            — SYSRTC compare-limit wraparound fix
- **NOTE:** the parent repo's submodule pointer must reference this branch/commit for the code to travel.

### 4.2 connectedhomeip (host tooling — chip-tool + OpenThread + otbr sources)
- Repo: `connectedhomeip`, branch **`fake-ble-transport`** @ `185dcb9d6f28d00467b345c8e3d901f8730c26cb`.
- Host-side fake transports (compliant — host tooling only, device is stock):
  - `src/platform/Linux/ble/FakeBleTransport.{h,cpp}` — host BLE fake transport (socket → BleCentralBridge)
  - `src/transport/raw/FakeOperational.{h,cpp}`       — host fake operational transport (RETIRE for otbr path)
  - `src/platform/silabs/efr32/FakeBLETransport.cpp`  — **DEVICE-side fake (RETIRED — NOT used for stock)**
- OpenThread: `third_party/openthread/repo`  ·  otbr: `third_party/ot-br-posix/repo`.

### 4.3 Stock firmware (the emulation target — DO NOT rebuild/modify)
- `sl-renode/matter/brd2601b-matter-silabs-lighting-example.out` (stock; `nm` shows `sl_bt`/`BLEManagerImpl`,
  **no** `FakeBLE*`/`radio_fake`). Board brd2601b / efr32xG24 (Cortex-M33 + M0+ radio sequencer).

### 4.4 This handoff package (`matter/renode-thread/`)
- `scenarios/e2e-15.4.resc`      — Renode scenario: stock fw + both bridges + `PerformanceInMips=39`
- `scenarios/phase3-bridges.repl`— the two bridge peripherals (bleCentral + ieee802154)
- `scripts/commission.sh`        — (runs in the chip-tool container) relay + `pairing ble-thread`
- `scripts/ot_leader.sh`         — drive an `ot-cli-ftd` sim node as a Thread leader with the dataset
- `scripts/ot_sim_inject.py`     — inject a synthetic frame onto the OT-sim air (host→device test)
- `host/loopback_relay.py`       — TCP relay so a containerized chip-tool reaches the host BleCentralBridge

---

## 5. Build everything

### 5.1 Renode (.NET + native `tlib`)
On **Linux** build the full emulator (native translator included) — Renode officially supports Linux:
```
cd sl-renode
git -C src/Infrastructure checkout renode/stock-firmware-ble-central
./build.sh -c Release            # produces output/bin/Release with native libs (tlib etc.)
```
Iterating on just the C# after a full build:
```
dotnet build src/Infrastructure/src/Infrastructure_NET.csproj -c Release
cp src/Infrastructure/src/bin/Release/net8.0/Infrastructure.dll output/bin/Release/Infrastructure.dll
```

### 5.2 OpenThread simulation binaries (real-time UDP air; NO virtual-time)
```
cd connectedhomeip/third_party/openthread/repo
# ot-cli-ftd (test leader / node)
cmake -GNinja -B build/ot-cli -DOT_PLATFORM=simulation -DOT_FTD=ON -DOT_APP_CLI=ON \
      -DOT_APP_NCP=OFF -DOT_APP_RCP=OFF -DOT_TCP=OFF -DOT_CLI_TCP_ENABLE=OFF
ninja -C build/ot-cli ot-cli-ftd                       # → build/ot-cli/examples/apps/cli/ot-cli-ftd
# ot-rcp (radio for otbr)
cmake -GNinja -B build/ot-rcp -DOT_PLATFORM=simulation -DOT_RCP=ON -DOT_APP_RCP=ON \
      -DOT_FTD=OFF -DOT_MTD=OFF -DOT_APP_CLI=OFF -DOT_APP_NCP=OFF -DOT_TCP=OFF -DOT_CLI_TCP_ENABLE=OFF
ninja -C build/ot-rcp                                   # → build/ot-rcp/examples/apps/ncp/ot-rcp
```
(`-DOT_TCP=OFF -DOT_CLI_TCP_ENABLE=OFF` avoids a CLI TCP-example link error.)

### 5.3 otbr-agent
```
cd connectedhomeip/third_party/ot-br-posix/repo
cmake -GNinja -B build -DBUILD_TESTING=OFF -DOTBR_MDNS=openthread \
      -DOTBR_BORDER_AGENT=ON -DOTBR_BORDER_ROUTING=ON -DOTBR_DBUS=OFF -DOTBR_WEB=OFF -DOTBR_REST=OFF
ninja -C build otbr-agent           # → build/src/agent/otbr-agent  (+ .../src/posix/ot-ctl)
```
`OTBR_MDNS=openthread` uses OT's built-in mDNS → **no avahi-daemon/dbus needed**. `BUILD_TESTING=OFF`
avoids a GTest dependency.

### 5.4 chip-tool — TWO variants
- **Commissioning (fake BLE, used today):** GN args
  `chip_enable_fake_ble_transport=true  chip_enable_fake_operational_transport=true`
  (built at `/tmp/out/chip-tool-fake-ble` in the container).
- **Operational path (Phase 3.5 — BUILD THIS):** fake BLE **but real operational transport**:
  `chip_enable_fake_ble_transport=true  chip_enable_fake_operational_transport=false`
  so commissioning still runs over the emulated BLE while CASE/clusters use **real UDP-over-Thread via otbr**.
  Build (in connectedhomeip):
  ```
  ./scripts/examples/gn_build_example.sh examples/chip-tool out/chip-tool-op \
     'chip_enable_fake_ble_transport=true chip_enable_fake_operational_transport=false'
  ```

---

## 6. Run the proven flows

### 6.1 Boot + BLE commissioning + Thread attach + ping (what works today)
```
# 1) Renode (headless, telnet monitor on 3456), with both bridges + the CPU-speed fix
./renode --disable-xwt --port 3456 matter/renode-thread/scenarios/e2e-15.4.resc

# 2) a host OpenThread leader on the OT-sim air (channel 15 / PAN 0x1234 from the dataset)
bash matter/renode-thread/scripts/ot_leader.sh 1 160          # node 1, run 160s

# 3) commissioning (in the chip-tool container, or natively on Linux — see §9)
#    relay (loopback_relay.py) + `chip-tool pairing ble-thread 1 hex:<DATASET> 20202021 3840 --bypass-attestation-verifier true`
bash matter/renode-thread/scripts/commission.sh              # widen timeout for a bigger attach window

# 4) confirm: leader neighbor table shows the device; ping it (in-window, before failsafe expiry)
#    echo "ping fd61:f77b:d3df:233e:0:ff:fe00:<RLOC16> 8 12 1" > /tmp/otin1   (see ot_leader.sh FIFO)
```
Operational **DATASET** (channel 15, PAN 0x1234, key 00112233…ff, extpan 1111111122222222, name OpenThreadDemo):
```
0e080000000000010000000300000f35060004001fffe0020811111111222222220708fd61f77bd3df233e051000112233445566778899aabbccddeeff030e4f70656e54687265616444656d6f010212340410445f2b5ca6f2a93a55ce570a70efeecb0c0402a0fff8
```

---

## 7. Critical fixes & gotchas (hard-won — read before touching anything)

1. **`PerformanceInMips` = the real-time fix.** Renode defaults CPU to **100 MIPS** (`BaseCPU.cs`), but the
   platform clocks the M33 at **39 MHz**, so "real-time" demanded 2.5× too much host throughput → **0.68×**.
   Setting `cpu PerformanceInMips 39` + `seqcpu PerformanceInMips 39` → **~1.0×** (throttled, with headroom).
   This is what makes wall-clock host-OpenThread MAC/MLE timing (ACK ~864µs, MLE ~1s) line up so the device
   attaches. It's already in `e2e-15.4.resc`; consider baking it into `platforms/cpus/silabs/efr32s2/efr32xG24.repl`.
2. **`InterferenceQueue.Add(...)` BEFORE `FrameSent`** (both bridges). The receiver drops frames whose sender
   isn't registered there ("TX was aborted", `SiLabs_xG24_LPW.cs`). Remove shortly after.
3. **Channel numbering / medium delivery.** The medium delivers A→B only on **exact `Channel` match**, and
   the model's **sync-word check** rejects cross-PHY frames (so BLE↔15.4 crosstalk on one shared medium is
   harmless). BLE `Channel` = RF index (adv 37/38/39 = 0/12/39). 802.15.4 `Channel` = the CHSP value = **15**
   for ch15. PHY select = `MODEM.CTRL4.VTDEMODEN` (0=`Phy_802154`, 1=`Phy_BLE`).
4. **802.15.4 framing.** On-air = `[syncWord(N)][PHR=len][MPDU incl FCS]`; SFD sync = **`0xA7`** (N=1). Model
   mocks the FCS as `0x0000` and only gates RX on the sync word. OpenThread **verifies CRC-16/KERMIT**, so the
   bridge recomputes a real FCS device→host and keeps the host's FCS host→device.
5. **Fail-safe window.** After `ThreadNetworkEnable` the device is on Thread only for the ArmFailSafe window
   (~60s). If `CommissioningComplete` never arrives (CASE fails w/o otbr), the device **reverts and leaves the
   mesh**. Any ping/operational test must run in-window. **otbr fixes this permanently** (CASE→Complete
   disarms the failsafe).
6. **No re-commission in one Renode session.** `BleCentralBridge` doesn't reset connection/GATT state on a
   2nd chip-tool session → BLE PASE times out. Restart Renode per commissioning. *TODO: reset on host-socket
   disconnect.*
7. **`./renode` spawns `dotnet` as a CHILD.** `kill`-ing the wrapper leaves a stray that steals CPU and holds
   ports (9000-9002, 3500, 3456). Always `pkill -9 -f Renode.dll`. Strays were the cause of earlier slowness.

---

## 8. Remaining work — Phase 3.5 (operational path)

Goal: chip-tool reaches the device over **real UDP-over-Thread** for CASE → CommissioningComplete → clusters.
Needs a **border router** (SRP + mDNS + IPv6 routing) on the same OT-sim air:

- **3.5c — otbr on the air.** Run `otbr-agent` driving the sim RCP:
  ```
  otbr-agent -I wpan0 -B <backbone-if> \
    'spinel+hdlc+forkpty://<path>/ot-rcp?forkpty-arg=1'
  # then form the network with the DATASET via ot-ctl:
  ot-ctl dataset set active <DATASET> ; ot-ctl ifconfig up ; ot-ctl thread start
  ```
  Requires **`/dev/net/tun` + `CAP_NET_ADMIN`** (otbr creates the `wpan0` TUN). *Verify the forkpty'd RCP
  lands on the **real-time** multicast air (not virtual-time) — the posix spinel path has a `virtualTimeInit`
  call; confirm it's a no-op for the non-virtual-time build.*
- **3.5d — chip-tool operational.** Use the fake-BLE **+ real-operational** chip-tool (§5.4). Full
  `pairing ble-thread`: BLE PASE→creds→ThreadNetworkEnable→device attaches to otbr→device SRP-registers→
  chip-tool discovers `_matter._tcp` via otbr mDNS→**CASE over UDP-over-Thread**→**CommissioningComplete**.
- **3.5e — cluster command.** `chip-tool onoff toggle 1 1`; verify the stock device acts (LED GPIO / log).

Also open:
- Bake `PerformanceInMips=39` into the platform `.repl` (fidelity + real-time). *(shared-test impact — verify.)*
- `BleCentralBridge` re-commission reset (gotcha #6).
- The `Ieee802154HostBridge` uses native OT-sim **multicast** — on one Linux host this connects directly to
  otbr's RCP; across a macOS↔Docker boundary it would need a TCP relay (a `transport:tcp` mode on the bridge
  or a multicast↔TCP relay). On native Linux this is unnecessary (see §9).

---

## 9. Native x86-64 Linux plan (the target environment — big simplification)

Moving to a **native x86-64 Linux** host removes nearly every friction we hit on macOS:

- **No Rosetta / no x86-emulated Docker** → native speed; otbr's Thread MAC timing keeps up with the
  real-time emulated device (which was a risk in the x86-emulated container on the M4).
- **Everything on ONE host, ONE multicast air.** Renode + both bridges + `ot-rcp` + `otbr-agent` + chip-tool
  all on `127.0.0.1` / `lo`, sharing OT-sim multicast **224.0.0.116:9000**. `Ieee802154HostBridge`'s native
  multicast talks **directly** to otbr's RCP — **no frame relay needed**.
- **otbr runs natively** (or a privileged container with `--network host`), with `/dev/net/tun` +
  `CAP_NET_ADMIN`, creating `wpan0`. chip-tool + otbr on the same host → **mDNS/SRP discovery works locally**.
- **BLE relay optional.** If chip-tool runs on the same host as Renode, it connects **directly** to
  `BleCentralBridge`'s `127.0.0.1:3500` — no `loopback_relay.py`. (Keep the relay only if chip-tool is in a
  separate netns/container.)

**Suggested bring-up order on Linux:**
1. Build Renode (`./build.sh -c Release`), stock fw in place, run `e2e-15.4.resc` (keep `PerformanceInMips=39`).
2. Build the OT-sim binaries (§5.2) + otbr (§5.3). Sanity: `ot-cli-ftd` leader + a 2nd `ot-cli-ftd` node
   attach on the local multicast air (all native).
3. Re-verify BLE commissioning → Thread attach → ping (§6) with everything native — should be faster/cleaner.
4. Bring up `otbr-agent` on the air (§8, 3.5c). Confirm the device attaches to otbr and SRP-registers
   (`ot-ctl srp server host`, `ot-ctl netdata show`).
5. Build the fake-BLE+real-op chip-tool (§5.4); run full `pairing ble-thread`; watch for CASE +
   **CommissioningComplete**.
6. `chip-tool onoff toggle 1 1`.

Watch-outs on Linux: keep the OT-sim build **non-virtual-time** (default); ensure the multicast interface is
`lo`/`127.0.0.1` for all OT-sim participants (env/`gLocalInterface`); give otbr the TUN capability; if using
a container for otbr, `--network host --cap-add=NET_ADMIN --device=/dev/net/tun`.

---

## 10. File manifest (this package)

```
matter/renode-thread/
  HANDOFF.md                     ← this file
  scenarios/e2e-15.4.resc        ← Renode scenario (stock fw + bridges + PerformanceInMips=39)
  scenarios/phase3-bridges.repl  ← the two bridge peripherals
  scripts/commission.sh          ← container-side: relay + chip-tool pairing ble-thread
  scripts/ot_leader.sh           ← host OpenThread leader driver (FIFO-injectable)
  scripts/ot_sim_inject.py       ← host→device OT-sim frame injector (bridge test)
  host/loopback_relay.py         ← TCP relay (container chip-tool ↔ host BleCentralBridge:3500)
```

Renode code lives in the **`src/Infrastructure` submodule**, branch `renode/stock-firmware-ble-central`
(commits in §4.1). Memory/notes: the assistant's project memory `matter-stock-firmware-renode.md` has the
blow-by-blow investigation log if deeper detail is needed.
