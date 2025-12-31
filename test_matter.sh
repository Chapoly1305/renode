#!/bin/bash
# Test Matter lighting firmware with BLE bridge

set -e

# Clean up any existing processes
pkill -9 -f "ble_bridge.py" 2>/dev/null || true
pkill -9 -f "renode" 2>/dev/null || true
sleep 1

echo "Starting BLE bridge in dry-run mode..."
python3 ble_bridge.py --dry-run 2>&1 | tee /tmp/bridge_matter.log &
BRIDGE_PID=$!

sleep 3
echo "Bridge PID: $BRIDGE_PID"

echo ""
echo "Starting Renode with Matter firmware..."
timeout 15 ./renode --console --disable-xwt matter_ble_bridge.resc -e "start" 2>&1 | tee /tmp/renode_matter.log &
RENODE_PID=$!

echo "Renode PID: $RENODE_PID"
echo ""
echo "Waiting 10 seconds for BLE advertisements..."
sleep 10

echo ""
echo "Stopping processes..."
kill $BRIDGE_PID 2>/dev/null || true
kill $RENODE_PID 2>/dev/null || true
sleep 1

echo ""
echo "========================================"
echo "RESULTS"
echo "========================================"

if grep -q "RX ADV" /tmp/bridge_matter.log; then
    echo "✓ SUCCESS: BLE Advertisements captured!"
    echo ""
    echo "Advertisement details:"
    grep "RX ADV" /tmp/bridge_matter.log | head -5
    echo ""
    echo "Advertising data:"
    grep "DRY-RUN.*Set advertising" /tmp/bridge_matter.log | head -1
else
    echo "✗ FAILED: No advertisements captured"
    echo ""
    echo "Bridge output:"
    tail -20 /tmp/bridge_matter.log
fi

echo ""
echo "Full logs:"
echo "  Bridge: /tmp/bridge_matter.log"
echo "  Renode: /tmp/renode_matter.log"
