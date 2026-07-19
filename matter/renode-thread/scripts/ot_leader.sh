#!/usr/bin/env bash
# Run an ot-cli-ftd (simulation) node as a Thread leader with the commissioning dataset,
# on the 224.0.0.116:9000 OT-sim air that Ieee802154HostBridge relays the device onto.
# Periodically dumps state + child/neighbor tables so we can watch the Renode device attach.
NODE=${1:-1}
DUR=${2:-90}
# Path to the simulation ot-cli-ftd (see HANDOFF.md §5.2). Override with OT_CLI_FTD=/path ./ot_leader.sh
BIN=${OT_CLI_FTD:-$HOME/connectedhomeip/third_party/openthread/repo/build/ot-cli/examples/apps/cli/ot-cli-ftd}
DS="0e080000000000010000000300000f35060004001fffe0020811111111222222220708fd61f77bd3df233e051000112233445566778899aabbccddeeff030e4f70656e54687265616444656d6f010212340410445f2b5ca6f2a93a55ce570a70efeecb0c0402a0fff8"
LOG=/tmp/ot-node${NODE}.log
FIFO=/tmp/otin${NODE}
: > "$LOG"
rm -f "$FIFO"; mkfifo "$FIFO"
"$BIN" "$NODE" < "$FIFO" > "$LOG" 2>&1 &
OTPID=$!
exec 3>"$FIFO"           # unblocks the CLI's read-open; keeps stdin open for the whole run
sleep 1.5
echo "dataset set active $DS" >&3; sleep 0.6
echo "ifconfig up"            >&3; sleep 0.6
echo "thread start"           >&3; sleep 1.0
echo "=== leader started (node $NODE) ===" >> "$LOG"
END=$((SECONDS + DUR))
while [ $SECONDS -lt $END ]; do
  { echo "state"; echo "child table"; echo "neighbor table"; echo "router table"; } >&3
  sleep 3
done
echo "quit" >&3 2>/dev/null
exec 3>&-
kill -9 $OTPID 2>/dev/null; rm -f "$FIFO"
