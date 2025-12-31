#!/bin/bash
# Test BLE bridge with Matter firmware

echo "Starting Python bridge..."
python3 -u ble_bridge.py --dry-run > /tmp/bridge.log 2>&1 &
BR_PID=$!

sleep 2

echo "Starting Renode with Matter firmware..."
timeout 25 ./renode --console --disable-xwt matter_ble_bridge.resc -e "emulation RunFor @20; start" > /tmp/renode.log 2>&1

sleep 2

echo "Stopping bridge..."
kill $BR_PID 2>/dev/null

echo ""
echo "=== Results ==="
if grep -q "RX ADV" /tmp/bridge.log; then
    echo "✓ BLE Advertisements captured!"
    grep "RX ADV" /tmp/bridge.log | head -3
    echo ""
    grep "DRY-RUN.*Set advertising" /tmp/bridge.log | head -1
else
    echo "✗ No advertisements captured"
    echo "Bridge log:"
    tail -10 /tmp/bridge.log
fi
