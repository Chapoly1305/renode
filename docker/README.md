# Containerized commissioning env (matter-emulated-commissioning)

Runs the Renode + Matter BLE/Thread commissioning proof in a Linux Docker container.
Motivated by otbr/Docker not working on macOS: otbr needs Linux-native netlink,
NAT64, and mDNS reflection, and Docker Desktop's hidden Linux VM doesn't expose
real host networking the way native Linux Docker does. This setup proves the
harness is fully portable on real Linux (this machine, or any Linux host/VM) —
that's the actual fix, not fighting macOS.

## What the image is

Runtime-only: it does **not** rebuild Renode, connectedhomeip, or otbr from
source. `Dockerfile` is `ubuntu:24.04` (pinned to match the host's glibc/lib
versions the prebuilt binaries were built against) with only the dotnet-8.0
runtime and the shared libraries chip-tool / Renode / otbr-agent need at
runtime. The already-built trees are bind-mounted in at `docker run` time by
`run.sh`, at the same absolute paths the scripts already hardcode — so no
script edits and no multi-GB image.

## Usage

```
./docker/run.sh build                     # build/rebuild the image
./docker/run.sh                           # run e2e_native.sh (default)
./docker/run.sh "bash matter/renode-thread/scripts/e2e_native.sh 90"
./docker/run.sh otbr e2e_diag_inner.sh    # run an otbr-based flow
./docker/run.sh otbr e2e_normal_inner.sh
./docker/run.sh otbr e2e_flip_inner.sh
```

## Native flow (`e2e_native.sh`)

No otbr involved — just needs `--network host` so the loopback ports (3456
Renode monitor, 3500 fake-BLE) and the `224.0.0.116:9000` OT-sim UDP multicast
behave identically to running directly on the host. Verified full pass:
BLE PASE → NOC → Thread Network Setup/Enable → device attaches to Thread.
Stops at `FindOperationalForStayActive` by design — that stage needs otbr.

Re-verified 2026-07-27: BLE PASE → NOC → ThreadNetworkSetup/Enable all succeeded,
device attached to Thread (leader's neighbor/router table shows it), then
`FindOperationalForStayActive` timed out as expected (no otbr in this flow).

## otbr flow (`e2e_diag_inner.sh` / `e2e_normal_inner.sh` / `e2e_flip_inner.sh`)

These need real kernel network-interface operations: a dummy `infra0`
interface, IPv6 forwarding, and a `wpan0` TUN device for otbr-agent, plus a
writable `/run` for its daemon socket/lock.

`run.sh otbr` runs them with:

```
docker run --user root --cap-add NET_ADMIN --device /dev/net/tun ...
```

No `--privileged`, no AppArmor override, no nested `unshare`. Full flow
verified end-to-end under this exact recipe: BLE PASE → NOC → Thread
commissioning → device self-partitions to leader → otbr merges as router+BR
→ device registers its Matter SRP service → **operational CASE established
→ cluster command InvokeResponse SUCCESS**.

Re-verified 2026-07-27 under the current `--user root --cap-add NET_ADMIN
--device /dev/net/tun` recipe: otbr merged as router (`partition=1172417620`),
published OMR `fd0d:b0b0:cafe:1::/64`, device SLAAC'd it and registered its
`_matter` SRP service (`srp_svc=1 srp_host=2`), and the final cluster command
came back `InvokeResponseMessage ... status = 0x00 (SUCCESS)`.

### Who actually asks for `NET_ADMIN` / `/dev/net/tun`, and why

These flags aren't an interactive prompt anyone answers at runtime — they're
static grants baked into the `docker run` invocation (in `run.sh`) by
whoever launches the container. Nothing asks permission mid-run; the kernel
just allows or denies the syscall based on what was granted at container
start.

The actual processes that need the capability to do their job:

- **`ip` (iproute2)**, invoked by the scripts themselves, for
  `ip link add infra0 type dummy`, bringing it up, and
  `sysctl -w net.ipv6.conf.all.forwarding=1`. These are Linux **rtnetlink**
  operations (create/configure a network interface, toggle a
  `/proc/sys/net` knob) — the kernel gates them on `CAP_NET_ADMIN` in the
  calling process's network namespace.
- **`otbr-agent`** itself, when it creates the `wpan0` Thread interface —
  it does `open("/dev/net/tun")` + `ioctl(TUNSETIFF)`, which needs both the
  device node to exist in `/dev` and `CAP_NET_ADMIN` for the ioctl to
  succeed.

Docker is the one *enforcing* the gate, not asking for anything: by default
it strips `NET_ADMIN` from every container's capability set and doesn't
populate `/dev/net/tun` in `/dev` at all. That's a deliberate default-deny
security posture, independent of whether the process inside is root.
`--cap-add NET_ADMIN --device /dev/net/tun` on the `docker run` line is what
overrides that default for this one container.

### Confirmed floor — why not go lower

Tested incrementally, in both directions, rather than assuming an answer:

- **Zero grants, even as container root**: `ip link add` fails with
  `Operation not permitted`, and `/dev/net/tun` doesn't exist in `/dev` at
  all. Docker withholds both specifically as a security boundary,
  independent of UID — this is the real floor. Genuine TUN/network-interface
  creation is an inherently privileged kernel operation; going lower would
  mean not using real otbr-agent/kernel networking at all and stubbing the
  border-router logic in pure userspace instead, which stops testing the
  actual code path.
- **A superseded, heavier 3-grant recipe** (`--cap-add SYS_ADMIN` +
  `--tmpfs /run:rw,mode=1777` + a nested
  `unshare --user --map-root-user --net --fork` inside the container) also
  worked, but is unnecessary: the scripts' header comments document running
  under a manual `unshare --user --net --mount`, which was written for bare
  Linux where you don't want to pollute your real host netns with a random
  dummy interface and always-on forwarding. Inside a container that isolation
  is redundant — Docker's own per-container network namespace already
  provides it. The nested-unshare version needed `SYS_ADMIN` just to pass
  `unshare(CLONE_NEWUSER)`'s seccomp gate, and needed the `/run` tmpfs
  workaround because a nested *unprivileged* user namespace's mapped "root"
  is fake outside its own namespace (can't write the real `/run`). Running
  directly as the container's real root sidesteps both problems at once.

### Gotchas

- The otbr `_inner.sh` scripts `exec > logfile 2>&1` at the very top,
  redirecting all their own output to a file (e.g. `/tmp/e2e_diag.log`)
  inside the container — nothing reaches `docker run`'s stdout. To see
  progress, `cat`/`tail` that log path from *inside* the same container
  invocation (a `--rm` container's filesystem is gone once it exits).
- Both flows are wall-clock-timing-sensitive (chip-tool's BLE
  connect-handshake timeout is a stock ~15s constant). Heavy background CPU
  load on the host (e.g. concurrent fuzzer campaigns) reliably starves
  Renode's real-time bridging and causes spurious BLE PASE timeouts —
  unrelated to the container. Check `uptime` before a run.
