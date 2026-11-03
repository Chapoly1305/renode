#!/usr/bin/env bash
# Build the pinned source trees, or run isolated real-firmware commissioning.
set -euo pipefail
stock_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
stock_image=${STOCK_IMAGE:-local/renode-stock:26q3}
cd "$stock_root"
case "${1:-help}" in
    prepare)
        git submodule sync --recursive
        mapfile -t renode_modules < <(git config --file .gitmodules --get-regexp '^submodule\..*\.path$' |
            awk '$2 != "matter/connectedhomeip" {print $2}')
        git submodule update --init --recursive "${renode_modules[@]}"
        git submodule update --init matter/connectedhomeip
        python3 matter/connectedhomeip/scripts/checkout_submodules.py --platform linux --shallow --recursive
        # Resources are not a submodule. Pin the version used by this rebase.
        if [[ ! -e lib/resources/.git ]]; then
            git init lib/resources
            git -C lib/resources remote add origin https://github.com/renode/renode-resources.git
            git -C lib/resources fetch --depth 1 origin 9d7824771f3db67663950cae06f5bb7f89fbf33b
            git -C lib/resources checkout --detach FETCH_HEAD
        fi
        test "$(git -C lib/resources rev-parse HEAD)" = 9d7824771f3db67663950cae06f5bb7f89fbf33b
        ;;
    build)
        "$stock_root/docker/stock.sh" prepare
        docker build -t local/renode-build:silabs-26q3 -f docker/Dockerfile.build .
        docker run --rm --memory 8g --pids-limit 2048 -v "$stock_root:/work" \
            local/renode-build:silabs-26q3 ./build.sh --skip-fetch
        docker build -t local/renode-host-tools:26q3 -f docker/Dockerfile.host-tools .
        docker run --rm --memory 10g --pids-limit 2048 -v "$stock_root:/work" \
            -e "BUILD_JOBS=${BUILD_JOBS:-8}" local/renode-host-tools:26q3 ./docker/build-host-tools.sh
        docker build -t "$stock_image" -f docker/Dockerfile.stock .
        ;;
    commission)
        shift
        exec python3 tests/peripherals/SiLabs/stock_commissioning_regression.py \
            --bundled-image "$stock_image" "$@"
        ;;
    *)
        printf '%s\n' 'Usage: docker/stock.sh prepare | build | commission --elf FILE --output DIR [--platform RELATIVE.repl] [--overlay RELATIVE.repl] [--pase-only]'
        ;;
esac
