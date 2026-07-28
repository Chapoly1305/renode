#!/usr/bin/env bash
# Created: 2026-07-27
# Purpose: launch the docker/Dockerfile image with the prebuilt host trees bind-mounted at the
#          same absolute paths e2e_native.sh / ot_leader.sh hardcode, so no script edits are
#          needed inside the container. Uses --network host so the loopback TCP ports (3456
#          Renode monitor, 3500 fake-BLE) and the 224.0.0.116:9000 OT-sim UDP multicast behave
#          identically to running directly on the host.
# Retention: PERMANENT (companion to docker/Dockerfile)
set -euo pipefail

R="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"     # ~/renode-matter-groundtruth
IMAGE=renode-matter-groundtruth:latest

if [[ "${1:-}" == "build" ]]; then
    docker build \
        --build-arg UID="$(id -u)" \
        --build-arg GID="$(id -g)" \
        -t "$IMAGE" \
        -f "$R/docker/Dockerfile" "$R/docker"
    exit 0
fi

TTY_FLAGS=()
[[ -t 0 && -t 1 ]] && TTY_FLAGS=(-it)

MOUNTS=(
    -v "$R":/home/chen/renode-matter-groundtruth
    -v "$HOME/matter-renode-work":/home/chen/matter-renode-work
    -v "$HOME/connectedhomeip":/home/chen/connectedhomeip
)

if [[ "${1:-}" == "otbr" ]]; then
    # otbr-based flows (e2e_diag_inner.sh / e2e_normal_inner.sh / e2e_flip_inner.sh) need their
    # own net+user namespace (dummy iface, IPv6 forwarding, private dbus, a wpan0 tun device, and
    # otbr-agent's /run/openthread-*.sock+.lock daemon socket). Empirically verified minimal grant
    # -- no --privileged, no apparmor override:
    #   --cap-add SYS_ADMIN        unshare(CLONE_NEWUSER|CLONE_NEWNET) is seccomp-gated on this cap
    #   --device /dev/net/tun      otbr-agent creates the wpan0 TUN interface
    #   --tmpfs /run:rw,mode=1777  OPENTHREAD_POSIX_CONFIG_DAEMON_SOCKET_BASENAME is hardcoded to
    #                              /run/openthread-%s at compile time (daemon.cpp); real container
    #                              /run is root-owned+non-writable to our mapped-root (mapping is
    #                              fake outside the nested userns), so give it a writable tmpfs the
    #                              same way docker already does for /tmp.
    # Dropping --mount from the unshare (vs. the scripts' own `unshare --user --net --mount`) avoids
    # needing an AppArmor override: the scripts' `mount -t tmpfs tmpfs /run` line is already
    # best-effort (`2>/dev/null`) and becomes a harmless no-op once /run is a writable tmpfs anyway.
    # Renode + otbr-agent + chip-tool all end up co-located inside the SAME unshared net namespace
    # (the script starts all three), so --network host at the docker level is irrelevant here and
    # is deliberately omitted -- the inner unshare re-isolates networking regardless.
    shift
    SCRIPT="${1:-e2e_diag_inner.sh}"
    docker run --rm "${TTY_FLAGS[@]}" \
        --cap-add SYS_ADMIN \
        --device /dev/net/tun \
        --tmpfs /run:rw,mode=1777 \
        "${MOUNTS[@]}" \
        "$IMAGE" \
        bash -lc "cd /home/chen/renode-matter-groundtruth && unshare --user --map-root-user --net --fork -- bash matter/renode-thread/scripts/$SCRIPT"
    exit 0
fi

docker run --rm "${TTY_FLAGS[@]}" \
    --network host \
    "${MOUNTS[@]}" \
    "$IMAGE" \
    bash -lc "${*:-bash matter/renode-thread/scripts/e2e_native.sh}"
