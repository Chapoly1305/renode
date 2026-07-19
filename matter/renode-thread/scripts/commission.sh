#!/usr/bin/env bash
# Runs inside the container: start relay, then chip-tool pairing over the fake transport.
set +e
DATASET="0e080000000000010000000300000f35060004001fffe0020811111111222222220708fd61f77bd3df233e051000112233445566778899aabbccddeeff030e4f70656e54687265616444656d6f010212340410445f2b5ca6f2a93a55ce570a70efeecb0c0402a0fff8"
pkill -f loopback_relay.py 2>/dev/null; sleep 1
python3 /tmp/loopback_relay.py 3500 host.docker.internal 3500 >/tmp/relay.log 2>&1 &
sleep 2
echo "relay: $(cat /tmp/relay.log)"
CHIP_FAKE_BLE_PORT=3500 timeout 60 /tmp/out/chip-tool-fake-ble/chip-tool \
  pairing ble-thread 1 "hex:$DATASET" 20202021 3840 --bypass-attestation-verifier true >/tmp/pairing.log 2>&1
echo "CHIP_TOOL_EXIT=$?" >> /tmp/pairing.log
pkill -f loopback_relay.py 2>/dev/null
