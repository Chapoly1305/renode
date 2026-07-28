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
    # otbr-based flows (e2e_diag_inner.sh / e2e_normal_inner.sh / e2e_flip_inner.sh) need real
    # kernel network-interface operations (a dummy "infra0" iface, IPv6 forwarding, a wpan0 TUN
    # device for otbr-agent) plus a writable /run for its daemon socket. Docker's own per-container
    # network namespace is already the isolation these scripts assume (their header comment
    # documents running under a manual `unshare --user --net --mount`) -- that manual unshare is
    # redundant inside a container and was dropped entirely, which also drops the two grants it
    # required (--cap-add SYS_ADMIN for the unshare(CLONE_NEWUSER) seccomp gate, and a --tmpfs /run
    # workaround for the nested-userns "fake root" not being able to write the real /run). Running
    # directly as the container's own root sidesteps both: real root can write /run directly, so
    # only two grants remain, and NET_ADMIN is the narrowly-scoped capability for what this
    # actually needs (vs. SYS_ADMIN, a much broader one):
    #   --cap-add NET_ADMIN     create/configure the dummy iface, IPv6 forwarding, wpan0 tun
    #   --device /dev/net/tun   otbr-agent's TUN interface (also absent from /dev by default)
    # Confirmed floor: with zero grants, even as root, `ip link add` fails with "Operation not
    # permitted" and /dev/net/tun doesn't exist -- Docker withholds both specifically as a security
    # boundary, independent of UID. Real interface/TUN creation is inherently privileged; going
    # below these two would mean not using genuine otbr-agent/kernel networking at all (stubbing
    # the border-router logic in pure userspace), which stops testing the real code path.
    shift
    SCRIPT="${1:-e2e_diag_inner.sh}"
    docker run --rm "${TTY_FLAGS[@]}" \
        --user root \
        --cap-add NET_ADMIN \
        --device /dev/net/tun \
        "${MOUNTS[@]}" \
        "$IMAGE" \
        bash -lc "export HOME=/home/chen && cd /home/chen/renode-matter-groundtruth && bash matter/renode-thread/scripts/$SCRIPT"
    exit 0
fi

docker run --rm "${TTY_FLAGS[@]}" \
    --network host \
    "${MOUNTS[@]}" \
    "$IMAGE" \
    bash -lc "${*:-bash matter/renode-thread/scripts/e2e_native.sh}"
