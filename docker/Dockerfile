# Created: 2026-07-27
# Purpose: run the stock-ground-truth Renode+Matter BLE/Thread commissioning proof
#          (e2e_native.sh, and the otbr-based e2e_*_inner.sh variants) inside a Linux
#          container, to test whether the whole harness is container-portable (motivated
#          by otbr/Docker not working on macOS).
# Retention: PERMANENT (reference env for reproducing the commissioning proof)
#
# Runtime-only image: does NOT rebuild Renode, connectedhomeip, or otbr from source. The
# already-built trees (renode-matter-groundtruth, matter-renode-work/connectedhomeip,
# connectedhomeip) are bind-mounted in at container run time (see run.sh) — this image only
# supplies the shared libraries and dotnet runtime those prebuilt binaries need. Base image
# pinned to ubuntu:24.04 to match the glibc/lib versions the host binaries were built against.
FROM ubuntu:24.04

RUN apt-get update && apt-get install -y --no-install-recommends \
    dotnet-runtime-8.0 \
    libssl3t64 \
    libglib2.0-0t64 \
    libevent-core-2.1-7t64 \
    libevent-pthreads-2.1-7t64 \
    zlib1g libmount1 libselinux1 libffi8 libpcre2-8-0 libblkid1 libstdc++6 \
    libdbus-1-3 libprotobuf-lite32t64 libsystemd0 libcap2 libgcrypt20 \
    liblz4-1 liblzma5 libzstd1 libgpg-error0 \
    dbus python3 ipset \
    iproute2 procps util-linux coreutils grep bash ca-certificates \
    && rm -rf /var/lib/apt/lists/*

ARG UID=1000
ARG GID=1000
# ubuntu:24.04 already ships a uid/gid-1000 "ubuntu" user; reuse that slot by renaming
# it rather than colliding on useradd/groupadd.
RUN if getent passwd "${UID}" >/dev/null; then \
        userdel -r "$(getent passwd "${UID}" | cut -d: -f1)"; \
    fi; \
    if getent group "${GID}" >/dev/null; then \
        groupdel "$(getent group "${GID}" | cut -d: -f1)"; \
    fi; \
    groupadd -g ${GID} chen && useradd -m -u ${UID} -g ${GID} -s /bin/bash chen

USER chen
ENV HOME=/home/chen
WORKDIR /home/chen/renode-matter-groundtruth
