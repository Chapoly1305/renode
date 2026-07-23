#!/usr/bin/env bash
# TOPOLOGY-FLIP operational E2E (runs INSIDE `unshare --user --map-root-user --net --mount --fork`):
#   Renode device = becomes its OWN Thread LEADER (its reliable self-partition behavior, weight 64).
#   otbr-agent    = joins the DEVICE's partition as ROUTER + Border Router (low leaderweight 1 -> loses
#                   the leader election to the device, merges in) -> provides OMR + SRP + mDNS.
# Rationale: the stock device receives Child ID Responses but never completes attach as a CHILD (device-side
# MLE receive rejection we can't see/patch). It DOES reliably become a leader, and its Matter operational-
# advertising logic works. So flip roles: device leads, robust otbr does the joining (receive-side attach
# burden on otbr, not the device). Once otbr is a router+BR in the device's partition, the device gets the
# OMR prefix (SLAAC) + otbr's SRP server in its netdata -> registers _matter via SRP -> chip-tool discovers.
set -u
exec >/tmp/e2e_flip.log 2>&1
mount -t tmpfs tmpfs /run 2>/dev/null

R=/home/chen/aliro-renode
REPO_OT=/home/chen/matter-renode-work/connectedhomeip/third_party/ot-br-posix/repo
OT_RCP=$REPO_OT/third_party/openthread/repo/build/simulation/examples/apps/ncp/ot-rcp
OTBR_AGENT=$REPO_OT/build/otbr/src/agent/otbr-agent
OT_CTL=$REPO_OT/build/otbr/third_party/openthread/repo/src/posix/ot-ctl
CHIP=/home/chen/matter-renode-work/connectedhomeip/out/chip-tool-op/chip-tool
DATASET=0e080000000000010000000300000f35060004001fffe0020811111111222222220708fd61f77bd3df233e051000112233445566778899aabbccddeeff030e4f70656e54687265616444656d6f010212340410445f2b5ca6f2a93a55ce570a70efeecb0c0402a0fff8
RUN=/tmp/otbr-run; rm -rf "$RUN"; mkdir -p "$RUN"
export DBUS_SYSTEM_BUS_ADDRESS=unix:path=$RUN/dbus.sock
PLOG=/tmp/pairing-flip.log; RLOG=/tmp/renode-15.4.log; ALOG=$RUN/otbr-agent.log

AGENT_PID=""; RENODE_PID=""; SAMPLER_PID=""
cleanup(){ kill -9 $AGENT_PID $RENODE_PID $SAMPLER_PID 2>/dev/null; }
trap cleanup EXIT

echo "### 0) clean"
rm -f "$HOME"/.matter* /tmp/chip_* "$R"/tmp/*.flash /tmp/*.flash 2>/dev/null

echo "### 1) net plumbing"
ip link set lo up; ip link set lo multicast on
ip link add infra0 type dummy 2>/dev/null; ip link set infra0 up; ip link set infra0 multicast on
ip -6 addr add fd00:abcd::1/64 dev infra0 nodad 2>/dev/null
sysctl -w net.ipv6.conf.all.forwarding=1 >/dev/null 2>&1

echo "### 2) private dbus"
cat > "$RUN/dbus.conf" <<EOF
<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-BUS Bus Configuration 1.0//EN" "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>system</type><listen>unix:path=$RUN/dbus.sock</listen>
<policy context="default"><allow user="*"/><allow own_prefix="io.openthread"/>
<allow send_destination="*"/><allow receive_sender="*"/><allow eavesdrop="true"/></policy></busconfig>
EOF
dbus-daemon --config-file="$RUN/dbus.conf" --fork; sleep 0.5

echo "### 3) otbr-agent (ot-rcp node 3) with LOW leaderweight -> will JOIN the device's partition"
"$OTBR_AGENT" -I wpan0 -B infra0 -d7 -v -s --vendor-name Renode --model-name RenodeSim \
    --data-path "$RUN" "spinel+hdlc+forkpty://$OT_RCP?forkpty-arg=3" > "$ALOG" 2>&1 &
AGENT_PID=$!
sleep 4
"$OT_CTL" dataset set active "$DATASET" >/dev/null 2>&1
"$OT_CTL" leaderweight 1 >/dev/null 2>&1        # lose leader election to the device (weight 64)
"$OT_CTL" routerselectionjitter 1 >/dev/null 2>&1  # upgrade to router fast once merged into the device
"$OT_CTL" ifconfig up >/dev/null 2>&1
# DETERMINISTIC CONVERGENCE: do NOT thread-start otbr yet. Start it only AFTER the device has commissioned
# to ThreadNetworkEnable and self-partitioned to LEADER, so otbr's AnyPartition attach joins the device's
# (only) partition every time — no leader-election race.
echo "    otbr initial: state=$("$OT_CTL" state 2>/dev/null|head -1|tr -d '[:space:]') weight=$("$OT_CTL" leaderweight 2>/dev/null|head -1|tr -d '[:space:]') (thread NOT started yet)"

# live sampler: watch otbr MERGE into the device's partition + the device register SRP.
LIVE=/tmp/flip-live.log; : > "$LIVE"
( while true; do
    ts=$(printf '%(%H:%M:%S)T' -1)
    st=$("$OT_CTL" state 2>/dev/null|head -1|tr -d '[:space:]')
    pid=$("$OT_CTL" partitionid 2>/dev/null|head -1|tr -d '[:space:]')
    ldr=$("$OT_CTL" leader 2>/dev/null | grep -iE "rloc|weight" | tr '\n' ' ')
    s=$("$OT_CTL" srp server service 2>/dev/null | grep -icE "instance|_matter|\._udp|\._tcp")
    h=$("$OT_CTL" srp server host 2>/dev/null | grep -icE "\.default|fd[0-9a-f]{2}:")
    omr=$("$OT_CTL" netdata show 2>/dev/null | grep -oE "fd[0-9a-f:]+/64 pa[a-z]*" | grep -viE "^fd61:f77b:d3df" | head -1)
    echo "[$ts] otbr_state=$st partition=$pid OMR=${omr:-none} srp_svc=$s srp_host=$h | $ldr" >> "$LIVE"
    [ "$s" -gt 0 ] && echo "  *** DEVICE REGISTERED ITS MATTER SRP SERVICE @ $ts ***" >> "$LIVE"
    sleep 3
  done ) &
SAMPLER_PID=$!

echo "### 4+5) DETERMINISTIC commissioning: device commissions -> self-partitions to LEADER -> THEN otbr joins"
cd "$R"
PAIR_RC=1; commissioned=0; conv=0
ML="fd61:f77b:d3df:233e:0:ff:fe00"   # mesh-local prefix (RLOC-based addrs = <ML>:<rloc16>)
for attempt in $(seq 1 12); do
  echo "  --- attempt $attempt: fresh Renode boot ---"
  pkill -9 -f "[R]enode.dll" 2>/dev/null; sleep 2
  "$OT_CTL" thread stop >/dev/null 2>&1   # reset otbr to detached for a clean deterministic re-join
  rm -f "$HOME"/.matter* /tmp/chip_* 2>/dev/null
  : > "$RLOG"
  setsid bash -c "timeout 900 ./renode --disable-xwt --hide-log --port 3456 matter/renode-thread/scenarios/e2e-15.4.resc >/tmp/renode-stdout.log 2>&1" &
  RENODE_PID=$!
  gatt=0
  for i in $(seq 1 90); do grep -q "CHIPoBLE GATT ready" "$RLOG" 2>/dev/null && { gatt=1; break; }; sleep 1; done
  echo "      GATT ready=$gatt"
  [ "$gatt" = 1 ] || { echo "      no GATT this boot; retry"; continue; }
  sleep 3
  # background chip-tool (full commissioning incl operational CASE over UDP-over-Thread)
  # --timeout 700: chip-tool's per-command wait defaults to 120s (PairingCommand::GetWaitDuration),
  # which aborts operational discovery/CASE long before otbr converges. Extend it so a single good
  # BLE attempt has ample time to converge (OMR+SRP) and complete the operational CASE handshake.
  CHIP_FAKE_BLE_PORT=3500 timeout 740 "$CHIP" pairing ble-thread 1 "hex:$DATASET" 20202021 3840 \
      --timeout 700 --bypass-attestation-verifier true > "$PLOG" 2>&1 &
  CHIP_PID=$!
  # wait for the device to reach ThreadNetworkEnable (it self-partitions to LEADER right after)
  tne=0
  for i in $(seq 1 90); do
    grep -q "finished commissioning step 'ThreadNetworkEnable'" "$PLOG" 2>/dev/null && { tne=1; break; }
    kill -0 "$CHIP_PID" 2>/dev/null || break
    sleep 1
  done
  if [ "$tne" != 1 ]; then
    echo "      attempt $attempt: pre-TNE BLE failure: $(grep -aoE 'connect\(\) failed[^\"]*|receive window closed|CHIP Error 0x[0-9A-F]+' "$PLOG" 2>/dev/null | tail -1); retry"
    kill -9 "$CHIP_PID" 2>/dev/null; wait "$CHIP_PID" 2>/dev/null; continue
  fi
  echo "      TNE reached; +16s for device to self-partition to LEADER, then otbr thread start (deterministic join)"
  sleep 16
  # DETERMINISTIC CONVERGENCE via BOUNCE-RETRY. otbr's auto border-routing OMR publication is flaky and,
  # more fundamentally, its initial attach frequently MISSES the device's MLE advertisements over the
  # emulated 15.4 bridge -> it forms its own singleton leader partition, from which the automatic
  # BetterPartition merge is unreliable. So (re)attach otbr (thread stop/start) until it MERGES into the
  # device's partition (state child/router); each re-attach is a fresh Parent Request that usually
  # catches the device. Then MANUALLY publish a fixed OMR prefix into netdata (only valid post-merge).
  # PERF: a successful MLE attach completes within a few seconds; when it fails it fails all
  # rounds (device-state dependent, not per-round luck). So poll fast (1s) with a short ~12s
  # cap and fewer rounds -> a failed attempt costs ~75s instead of ~210s, with negligible
  # success-rate loss (merges land in the first 1-2 rounds when they land at all).
  merged=0
  for round in $(seq 1 6); do
    "$OT_CTL" thread stop >/dev/null 2>&1; sleep 1; "$OT_CTL" thread start >/dev/null 2>&1
    for i in $(seq 1 12); do   # ~12s per round, poll every 1s
      st=$("$OT_CTL" state 2>/dev/null | head -1 | tr -d '[:space:]')
      case "$st" in child|router) merged=1; break;; esac
      sleep 1
    done
    [ "$merged" = 1 ] && break
    echo "      merge round $round: state=$st (own leader/detached), re-attaching otbr ..."
  done
  pid=$("$OT_CTL" partitionid 2>/dev/null | head -1 | tr -d '[:space:]')
  echo "      otbr merge result: state=$("$OT_CTL" state 2>/dev/null|head -1|tr -d '[:space:]') partition=$pid merged=$merged"
  if [ "$merged" != 1 ]; then
    echo "      otbr never merged into the device's partition after 6 rounds; abandoning for fresh boot"
    kill -9 "$CHIP_PID" 2>/dev/null; wait "$CHIP_PID" 2>/dev/null; continue
  fi
  echo "      MERGED. Publishing fixed OMR fd0d:b0b0:cafe:1::/64 + SRP server into the device's partition netdata"
  "$OT_CTL" srp server enable >/dev/null 2>&1   # ensure SRP server + its netdata DNS/SRP entry are advertised
  "$OT_CTL" prefix add fd0d:b0b0:cafe:1::/64 paos med >/dev/null 2>&1
  "$OT_CTL" netdata register >/dev/null 2>&1
  echo "      waiting for device to SLAAC the OMR + register _matter SRP -> operational CASE -> CommissioningComplete"
  conv_logged=0; waited=0
  while kill -0 "$CHIP_PID" 2>/dev/null; do
    if grep -q "Device commissioning completed with success" "$PLOG" 2>/dev/null; then commissioned=1; break; fi
    if [ "$conv_logged" = 0 ]; then
      s=$("$OT_CTL" srp server service 2>/dev/null | grep -icE "instance|_matter|\._udp|\._tcp")
      if [ "$s" -gt 0 ]; then
        conv_logged=1; conv=1
        DEVOMR=$("$OT_CTL" srp server host 2>/dev/null | grep -aoE "fd[0-9a-f:]+" | grep -aviE "^fd61:f77b:d3df" | head -1)
        NDOMR=$("$OT_CTL" netdata show 2>/dev/null | grep -oE "fd[0-9a-f:]+/64 pa[a-z]*" | grep -viE "^fd61:f77b:d3df" | head -1)
        echo "      >>> CONVERGED @ $(printf '%(%H:%M:%S)T' -1): device registered _matter SRP. devOMR=$DEVOMR netdataOMR=$NDOMR"
        echo "          chip-tool now performs operational CASE Sigma1/2/3 over UDP-over-Thread ..."
      elif [ "$waited" -ge 30 ]; then
        echo "      NOT converged within ~90s (no device SRP registration); abandoning attempt for a fresh boot"
        kill -9 "$CHIP_PID" 2>/dev/null
        break
      fi
    fi
    waited=$((waited+1)); sleep 3
  done
  wait "$CHIP_PID" 2>/dev/null; PAIR_RC=$?
  grep -q "Device commissioning completed with success" "$PLOG" 2>/dev/null && commissioned=1
  if [ "$commissioned" = 1 ]; then echo "      *** FULL COMMISSIONING SUCCESS -> CommissioningComplete (attempt $attempt) ***"; break; fi
  echo "      attempt $attempt: no success (converged=$conv_logged rc=$PAIR_RC); retry"
  kill -9 "$CHIP_PID" 2>/dev/null; wait "$CHIP_PID" 2>/dev/null
done
echo "    commissioning: full_success=$commissioned final_rc=$PAIR_RC"

# operational cluster command if fully commissioned
CLOG=/tmp/cluster-flip.log; : > "$CLOG"
if [ "$commissioned" = 1 ]; then
  echo "### 6) operational cluster command: onoff toggle (endpoint 1) over UDP-over-Thread"
  CHIP_FAKE_BLE_PORT=3500 timeout 60 "$CHIP" onoff toggle 1 1 > "$CLOG" 2>&1
  echo "    onoff toggle exit=$?"
fi

echo "### 7) post-commission observation (otbr partition/SRP) up to 6 min or until SRP registers"
for i in $(seq 1 120); do
  s=$("$OT_CTL" srp server service 2>/dev/null | grep -icE "instance|_matter|\._udp|\._tcp")
  [ "$s" -gt 0 ] && { echo "    >>> DEVICE SRP REGISTERED at poll $i <<<"; break; }
  sleep 3
done

kill -9 $SAMPLER_PID 2>/dev/null
echo
echo "############ RESULTS ############"
for k in "CASE establishment successful" "finished commissioning step 'ThreadNetworkEnable'" \
         "Operational discovery" "Commissioning complete" \
         "Device commissioning completed with success" "CHIP Error 0x00000032"; do
  echo "  [$(grep -c "$k" "$PLOG" 2>/dev/null)] $k"
done
echo "---- otbr final state ----"; "$OT_CTL" state 2>/dev/null | head -1
echo "---- otbr router table (device as leader?) ----"; "$OT_CTL" router table 2>/dev/null | tail -6
echo "---- otbr netdata (OMR + SRP service present?) ----"; "$OT_CTL" netdata show 2>/dev/null | head -25
echo "---- otbr SRP hosts ----"; "$OT_CTL" srp server host 2>/dev/null
echo "---- otbr SRP services ----"; "$OT_CTL" srp server service 2>/dev/null
echo "---- cluster cmd ----"; grep -iE "onoff|success|Timeout|Error|response" "$CLOG" 2>/dev/null | tail -6
echo "---- flip-live timeline (last 30) ----"; tail -30 "$LIVE" 2>/dev/null
echo "### flip e2e done"
