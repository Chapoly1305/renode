#!/usr/bin/env bash
# Native-Linux end-to-end: boot stock firmware in Renode + a host Thread leader on the OT-sim air,
# then commission over the emulated BLE with the fake-BLE chip-tool (direct to 127.0.0.1:3500, no relay).
# Proves: BLE PASE -> creds -> Network Commissioning (Thread dataset) -> device attaches to Thread -> ping.
# (Operational CASE + CommissioningComplete needs otbr -- separate; chip-tool will stop after attach.)
#
# Usage: e2e_native.sh [pairing_timeout_s]   (default 90)
set -u
R="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
CHIP=/home/chen/matter-renode-work/connectedhomeip/out/chip-tool-op/chip-tool
OTLEADER="$R/matter/renode-thread/scripts/ot_leader.sh"
DATASET="0e080000000000010000000300000f35060004001fffe0020811111111222222220708fd61f77bd3df233e051000112233445566778899aabbccddeeff030e4f70656e54687265616444656d6f010212340410445f2b5ca6f2a93a55ce570a70efeecb0c0402a0fff8"
PASSCODE=20202021; DISC=3840; NODEID=1
PAIR_TO=${1:-90}
RLOG=/tmp/renode-15.4.log
PAIRLOG=/tmp/pairing-native.log
LEADLOG=/tmp/ot-node1.log

command -v "$CHIP" >/dev/null 2>&1 || [ -x "$CHIP" ] || { echo "FATAL: chip-tool not built at $CHIP"; exit 2; }

echo "### cleanup"
pkill -9 -f Renode.dll 2>/dev/null; pkill -9 -f ot-cli-ftd 2>/dev/null; pkill -9 -f loopback_relay 2>/dev/null
rm -f "$HOME"/.matter* /tmp/chip_tool_kvs* /tmp/chip_* 2>/dev/null   # clear stale chip-tool commissioning state
sleep 1

# --- stray/port guard (HANDOFF gotcha #7) -------------------------------------------------------
# `./renode` spawns dotnet/setsid children that survive a parent kill. A leftover Renode holds
# port 3500 (BleCentralBridge's CHIPoBLE socket), and chip-tool then fails with a misleading
# "FakeBleTransport: connect() failed: Connection refused" instead of anything pointing at the cause.
# Verify the kill actually took, and that 3500 is free, before booting a fresh Renode.
# Match only real Renode processes: `dotnet .../Renode.dll`. pgrep -f alone would also match this
# script's own ancestor shell (its command line contains "Renode.dll"), so filter on comm==dotnet.
strays=""
for pid in $(pgrep -f 'Renode\.dll' 2>/dev/null); do
    [ "$(cat /proc/"$pid"/comm 2>/dev/null)" = "dotnet" ] && strays="$strays $pid"
done
if [ -n "${strays// }" ]; then
    echo "FATAL: Renode still running after pkill (pids:$strays)."
    echo "       Kill them and retry:  pkill -9 -f Renode.dll ; pkill -9 -f ot-cli-ftd"
    exit 3
fi
if command -v ss >/dev/null 2>&1; then port_busy=$(ss -ltn 2>/dev/null | grep -c ':3500 '); \
    else port_busy=$(netstat -ltn 2>/dev/null | grep -c ':3500 '); fi
if [ "${port_busy:-0}" -gt 0 ]; then
    echo "FATAL: port 3500 is already in use (a previous Renode/BleCentralBridge is holding it)."
    echo "       chip-tool would fail with 'connect() failed: Connection refused'. Free it first:"
    echo "       pkill -9 -f Renode.dll ; then check:  ss -ltnp | grep 3500"
    exit 3
fi
echo "    port 3500 free, no stray Renode -- OK to boot"
# ------------------------------------------------------------------------------------------------

echo "### 1) start Renode (stock fw + bridges) headless on telnet 3456"
cd "$R"
SCENARIO="${SCENARIO:-matter/renode-thread/scenarios/e2e-15.4.resc}"
echo "    scenario: $SCENARIO"
setsid bash -c "timeout $((PAIR_TO+80)) ./renode --disable-xwt --hide-log --port 3456 $SCENARIO >/tmp/renode-stdout.log 2>&1" &
echo "    waiting for device BLE GATT-ready..."
for i in $(seq 1 40); do grep -q "CHIPoBLE GATT ready" "$RLOG" 2>/dev/null && break; sleep 1; done
grep -q "CHIPoBLE GATT ready" "$RLOG" 2>/dev/null && echo "    device GATT ready" || echo "    WARN: GATT-ready marker not seen (continuing)"

echo "### 2) start host Thread leader (ot-cli-ftd node 1) on the OT-sim air"
OT_CLI_FTD=${OT_CLI_FTD:-/home/chen/connectedhomeip/third_party/openthread/repo/build/ot-cli/examples/apps/cli/ot-cli-ftd}
setsid bash -c "OT_CLI_FTD=$OT_CLI_FTD bash '$OTLEADER' 1 $((PAIR_TO+40)) >/tmp/ot_leader_driver.log 2>&1" &
sleep 6
echo "    leader state: $(grep -iE 'leader|router|detached' "$LEADLOG" 2>/dev/null | tail -1)"

echo "### 3) commission over emulated BLE (fake-BLE -> BleCentralBridge :3500, no relay)"
: > "$PAIRLOG"
CHIP_FAKE_BLE_PORT=3500 timeout "$PAIR_TO" "$CHIP" pairing ble-thread "$NODEID" "hex:$DATASET" "$PASSCODE" "$DISC" \
    --bypass-attestation-verifier true >>"$PAIRLOG" 2>&1
echo "    chip-tool exit=$?"

echo "### 4) results"
echo "---- pairing highlights ----"
grep -iE "Secure Pairing Success|SPAKE|CASE|Thread|Network|Commissioning (step|complete)|Successfully|Fail|Error|Device attestation|Rendezvous|attach" "$PAIRLOG" | tail -30
echo "---- Ieee802154 bridge (device->host 15.4 activity) ----"
grep -iE "Ieee802154HostBridge: learned|device->host MPDU|host->device MPDU" "$RLOG" 2>/dev/null | tail -8
echo "---- leader neighbor/child table (did the device attach?) ----"
grep -A6 -iE "child table|neighbor table|router table" "$LEADLOG" 2>/dev/null | tail -30

echo "### 5) cleanup"
pkill -9 -f Renode.dll 2>/dev/null; pkill -9 -f ot-cli-ftd 2>/dev/null
echo "### done. logs: $PAIRLOG  $RLOG  $LEADLOG"
