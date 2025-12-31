#!/bin/bash
set -e

# Simple BLE bridge test script
echo "=== BLE Bridge Test ==="

# Cleanup
pkill -9 -f "ble_bridge.py" 2>/dev/null || true
pkill -9 -f "renode" 2>/dev/null || true
sleep 1

# Start bridge
echo "Starting bridge..."
python3 -u ble_bridge.py --dry-run > /tmp/bridge.log 2>&1 &
BRIDGE_PID=$!
sleep 3

# Run Renode
echo "Running Renode (20 seconds)..."
timeout 25 ./renode --console --disable-xwt matter_ble_bridge.resc \
  -e "emulation RunFor @20; start" > /tmp/renode.log 2>&1 || true

# Cleanup
kill $BRIDGE_PID 2>/dev/null || true

# Results
echo ""
echo "=== Results ==="
ADV_COUNT=$(grep -c "RX ADV" /tmp/bridge.log || echo "0")
if [ "$ADV_COUNT" -gt 0 ]; then
    echo "✓ SUCCESS: Captured $ADV_COUNT advertisements"
    echo ""
    echo "Sample advertisements:"
    grep "RX ADV" /tmp/bridge.log | head -5
    echo ""
    grep "DRY-RUN.*Set advertising" /tmp/bridge.log | head -1
else
    echo "✗ FAILED: No advertisements captured"
    echo "Bridge log:"
    tail -10 /tmp/bridge.log
fi
