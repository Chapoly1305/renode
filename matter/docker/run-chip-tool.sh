#!/usr/bin/env bash
#
# Run a Linux fake-transport chip-tool (in Docker) against a Renode fake-transport socket on the host.
#
# Topology (macOS/Windows host): chip-tool -> 127.0.0.1:$PORT -> loopback_relay.py -> host.docker.internal:$PORT -> Renode
# On a Linux host you can instead run the container with `--network host` and skip the relay.
#
# Prereqs on the host: Docker running, and Renode already started with
#   ./renode scripts/complex/silabs/matter-fake-transport.resc
#
# Usage:
#   matter/docker/run-chip-tool.sh <thread-dataset-hex> [node-id] [pin] [discriminator]
#
# Env overrides: PORT (3500), CHIP_ROOT (connectedhomeip checkout), IMAGE, CONTAINER, NODE_ID, PIN, DISCRIMINATOR.
set -euo pipefail

PORT="${PORT:-3500}"
CHIP_ROOT="${CHIP_ROOT:-/Volumes/Tools/connectedhomeip}"
IMAGE="${IMAGE:-ghcr.io/project-chip/chip-build:200}"
CONTAINER="${CONTAINER:-matter-chiptool}"
DATASET="${1:?usage: run-chip-tool.sh <thread-dataset-hex> [node-id] [pin] [discriminator]}"
NODE_ID="${2:-${NODE_ID:-1}}"
PIN="${3:-${PIN:-20202021}}"
DISCRIMINATOR="${4:-${DISCRIMINATOR:-3840}}"
CHIP_TOOL_BIN="/tmp/out/chip-tool-fake-ble/chip-tool"
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 1. Ensure the build container exists (amd64 image; runs under emulation on Apple Silicon).
if ! docker ps -a --format '{{.Names}}' | grep -qx "$CONTAINER"; then
  echo ">> creating container $CONTAINER"
  docker run -d --name "$CONTAINER" --add-host host.docker.internal:host-gateway \
    -v "$CHIP_ROOT:/workspace" -w /workspace "$IMAGE" sleep infinity
fi
docker start "$CONTAINER" >/dev/null 2>&1 || true

# 2. Build chip-tool with the fake transport if not already built.
if ! docker exec "$CONTAINER" test -x "$CHIP_TOOL_BIN"; then
  echo ">> building chip-tool with chip_enable_fake_ble_transport=true (first time; slow under emulation)"
  docker exec "$CONTAINER" bash -lc '
    set -e; cd /workspace
    git config --global --add safe.directory /workspace
    export PW_ENVIRONMENT_ROOT=/tmp/pwenv
    source scripts/bootstrap.sh
    ./scripts/examples/gn_build_example.sh examples/chip-tool /tmp/out/chip-tool-fake-ble chip_enable_fake_ble_transport=true'
fi

# 3. Copy the loopback relay into the container and start it (127.0.0.1:$PORT -> host.docker.internal:$PORT).
docker cp "$HERE/loopback_relay.py" "$CONTAINER:/tmp/loopback_relay.py"
docker exec "$CONTAINER" bash -lc "pkill -f loopback_relay.py 2>/dev/null; \
  (python3 /tmp/loopback_relay.py $PORT host.docker.internal $PORT >/tmp/relay.log 2>&1 &) ; sleep 1; cat /tmp/relay.log"

# 4. Commission over the fake transport.
echo ">> commissioning node $NODE_ID (pin=$PIN disc=$DISCRIMINATOR) over fake transport on :$PORT"
docker exec -e CHIP_FAKE_BLE_PORT="$PORT" "$CONTAINER" \
  "$CHIP_TOOL_BIN" pairing ble-thread "$NODE_ID" "hex:$DATASET" "$PIN" "$DISCRIMINATOR" \
  --bypass-attestation-verifier true
