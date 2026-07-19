#!/usr/bin/env bash
# Native-Linux: commission the stock device over emulated BLE, wait for it to attach to the host
# Thread leader, then ping its mesh-local RLOC address from the leader and check for ICMPv6 replies.
# Proves the IP-over-Thread datapath end to end. Blocks until done; prints a summary.
set -u
R=/home/chen/matter-renode-work/sl-renode
CHIP=/home/chen/matter-renode-work/connectedhomeip/out/chip-tool-op/chip-tool
OT_CLI_FTD=${OT_CLI_FTD:-/home/chen/matter-renode-work/connectedhomeip/third_party/openthread/repo/build/ot-cli/examples/apps/cli/ot-cli-ftd}
OTLEADER="$R/matter/renode-thread/scripts/ot_leader.sh"
DATASET="0e080000000000010000000300000f35060004001fffe0020811111111222222220708fd61f77bd3df233e051000112233445566778899aabbccddeeff030e4f70656e54687265616444656d6f010212340410445f2b5ca6f2a93a55ce570a70efeecb0c0402a0fff8"
MLPREFIX="fd61:f77b:d3df:233e"   # mesh-local prefix from the dataset
RLOG=/tmp/renode-15.4.log; LEADLOG=/tmp/ot-node1.log; FIFO=/tmp/otin1
cd "$R"

echo "### cleanup"
for p in Renode.dll ot-cli-ftd out/chip-tool-op; do pkill -9 -f "$p" 2>/dev/null; done
rm -f "$HOME"/.matter* /tmp/chip_* tmp/*.flash /tmp/*.flash 2>/dev/null
sleep 2

echo "### 1) Renode (stock fw + bridges)"
setsid bash -c "timeout 260 ./renode --disable-xwt --hide-log --port 3456 matter/renode-thread/scenarios/e2e-15.4.resc >/tmp/renode-stdout.log 2>&1" &
for i in $(seq 1 45); do grep -q "CHIPoBLE GATT ready" "$RLOG" 2>/dev/null && break; sleep 1; done
echo "    GATT ready: $(grep -c 'CHIPoBLE GATT ready' "$RLOG")"

echo "### 2) host Thread leader (ot-cli-ftd node 1), FIFO=$FIFO"
setsid bash -c "OT_CLI_FTD=$OT_CLI_FTD bash '$OTLEADER' 1 220 >/tmp/ot_leader_driver.log 2>&1" &
sleep 6

echo "### 3) commission over emulated BLE (background; keeps the failsafe/session alive)"
CHIP_FAKE_BLE_PORT=3500 timeout 120 "$CHIP" pairing ble-thread 1 "hex:$DATASET" 20202021 3840 \
    --bypass-attestation-verifier true >/tmp/pairing-native.log 2>&1 &

echo "### 4) wait for the device to attach (child table entry)"
DEVRLOC=""
for i in $(seq 1 75); do
    DEVRLOC=$(grep -oE "0x[0-9a-f]{4}" "$LEADLOG" 2>/dev/null | grep -vE "00$" | sort -u | head -1)
    [ -n "$DEVRLOC" ] && break
    sleep 2
done
if [ -z "$DEVRLOC" ]; then echo "    FAILED: device never appeared in leader table"; else echo "    device attached, RLOC16=$DEVRLOC"; fi

echo "### 5) ping the device's mesh-local RLOC from the leader"
if [ -n "$DEVRLOC" ]; then
    ADDR="${MLPREFIX}:0:ff:fe00:${DEVRLOC#0x}"
    echo "    ping $ADDR"
    # give the datapath a moment, then send ICMPv6 echoes via the leader CLI FIFO
    sleep 2
    for rep in 1 2 3; do echo "ping $ADDR 16 6 1" > "$FIFO"; sleep 9; done
fi

echo "### 6) results"
echo "---- commissioning ----"
echo "  CASE: $(grep -c 'CASE establishment successful' /tmp/pairing-native.log)  ThreadNetworkEnable: $(grep -c "finished commissioning step 'ThreadNetworkEnable'" /tmp/pairing-native.log)"
echo "---- attach (last child table) ----"
tac "$LEADLOG" 2>/dev/null | awk '/child table/{print;exit}{print}' | tac | grep -E "child table|0x[0-9a-f]{4}" | head -3
echo "---- PING replies (ICMPv6 echo) ----"
grep -E "bytes from|icmp_seq|Received .*bytes" "$LEADLOG" 2>/dev/null | tail -20
echo "  ping-reply lines: $(grep -cE "bytes from|icmp_seq" "$LEADLOG")"

echo "### cleanup"
for p in Renode.dll ot-cli-ftd out/chip-tool-op; do pkill -9 -f "$p" 2>/dev/null; done
echo "### done"
