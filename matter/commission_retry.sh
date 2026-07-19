#!/usr/bin/env bash
#
# Host-side reliable driver for the Renode fake-transport Matter commissioning demo.
#
# Runs the full flow end to end: fresh Renode (fake BLE + fake Thread radio + fake operational
# transport firmware) <-> Docker chip-tool. Because the emulation runs in REAL TIME, the crypto-heavy
# CHIPoBLE data phase (DAC/PAI cert reads) is timing-sensitive and any single attempt may time out;
# each attempt is independent, so we retry a fresh Renode until commissioning completes (typically
# succeeds within a handful of tries). A successful run performs, over the fake transports:
#   PASE -> attestation -> CSR -> AddNOC -> CASE(BLE) -> Thread provisioning -> ThreadNetworkEnable
#   (device becomes Leader) -> operational CASE over FAKE-OP -> CommissioningComplete (errorCode=0).
#
# Prereqs (see matter/HANDOFF.md):
#   - Renode built with the crypto-fixed Infrastructure.dll.
#   - Firmware built with: chip_enable_fake_ble_transport=true chip_enable_fake_thread_radio=true
#                          chip_enable_fake_operational_transport=true
#   - Docker container `matter-chiptool` with /tmp/out/chip-tool-fake-ble/chip-tool (built with
#     chip_enable_fake_ble_transport=true chip_enable_fake_operational_transport=true),
#     /tmp/loopback_relay.py and /tmp/commission.sh present.
#
# Usage:  matter/commission_retry.sh [max_attempts]
set -u

SL_RENODE="${SL_RENODE:-/Volumes/Tools/toolchains/sl-renode}"
RESC="${RESC:-/Volumes/Tools/toolchains/sl-renode-matter/scripts/complex/silabs/matter-fake-transport.resc}"
CONTAINER="${CONTAINER:-matter-chiptool}"
MAX="${1:-10}"

renode_restart() {
  pkill -f 'Renode.dll .*matter-fake-transport' 2>/dev/null
  sleep 3
  ( cd "$SL_RENODE" && nohup ./renode --disable-xwt --port 3456 -e "include @${RESC}" >/tmp/renode-fakeradio.log 2>&1 & )
  # wait (up to ~25s) for the fake-transport socket to come up
  for _ in $(seq 1 25); do
    if lsof -nP -iTCP:3500 -sTCP:LISTEN >/dev/null 2>&1; then sleep 3; return 0; fi
    sleep 1
  done
  echo "renode socket :3500 did not come up" >&2
  return 1
}

for attempt in $(seq 1 "$MAX"); do
  echo "===== attempt ${attempt}/${MAX} ====="
  renode_restart || continue
  docker exec "$CONTAINER" bash /tmp/commission.sh >/tmp/commission_out.txt 2>&1
  ec=$(docker exec "$CONTAINER" sh -c "grep -oE 'CHIP_TOOL_EXIT=[0-9]+' /tmp/pairing.log | tail -1" 2>/dev/null)
  last=$(docker exec "$CONTAINER" sh -c "grep -oE \"finished commissioning step '[A-Za-z]+'|Commissioning complete for node ID [x0-9]+: success\" /tmp/pairing.log | tail -1" 2>/dev/null)
  echo "  -> ${ec}   (${last})"
  if [ "$ec" = "CHIP_TOOL_EXIT=0" ]; then
    echo "===== COMMISSIONING SUCCEEDED on attempt ${attempt} ====="
    docker exec "$CONTAINER" sh -c "grep -E 'FAKE-OP|CommissioningComplete response, errorCode=0|Commissioning complete for node' /tmp/pairing.log | tail -8" 2>/dev/null
    exit 0
  fi
done

echo "===== did not complete within ${MAX} attempts (real-time timing flakiness; re-run) ====="
exit 1
