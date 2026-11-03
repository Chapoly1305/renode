#!/usr/bin/env bash
# Run inside Dockerfile.host-tools with the checkout mounted at /work.
set -euo pipefail
cd /work
build_jobs=${BUILD_JOBS:-8}
sdk_root=/work/matter/connectedhomeip
ot_source="$sdk_root/third_party/openthread/repo"
otbr_source="$sdk_root/third_party/ot-br-posix/repo"
cmake -S "$ot_source" -B output/host-build/openthread -G Ninja \
    -DOT_PLATFORM=simulation -DOT_APP_CLI=ON -DOT_APP_RCP=ON -DOT_TCP=OFF \
    -DOT_SIMULATION_VIRTUAL_TIME=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build output/host-build/openthread --target ot-cli-ftd --parallel "$build_jobs"
cmake -S "$otbr_source/third_party/openthread/repo" -B output/host-build/otbr-rcp -G Ninja \
    -DOT_PLATFORM=simulation -DOT_APP_RCP=ON -DOT_APP_CLI=OFF -DOT_TCP=OFF \
    -DOT_SIMULATION_VIRTUAL_TIME=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build output/host-build/otbr-rcp --target ot-rcp --parallel "$build_jobs"
cmake -S "$otbr_source" -B output/host-build/otbr -G Ninja \
    -DBUILD_TESTING=OFF -DOTBR_MDNS=openthread -DOTBR_DBUS=OFF \
    -DOTBR_WEB=OFF -DOTBR_REST=OFF -DOT_TCP=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build output/host-build/otbr --parallel "$build_jobs"
cd "$sdk_root"
# With system compilers/Python there are no CIPD toolchain overrides. Preserve
# any existing bootstrap-generated file instead of replacing a user's setup.
if [[ ! -e build_overrides/pigweed_environment.gni ]]; then
    printf '%s\n' '# System compiler/Python build: no CIPD toolchain overrides.' \
        > build_overrides/pigweed_environment.gni
fi
gn gen out/renode-host-tools --args='chip_enable_fake_ble_transport=true chip_enable_fake_operational_transport=false'
ninja -C out/renode-host-tools -j "$build_jobs" chip-tool TestBleTimeout
out/renode-host-tools/tests/TestBleTimeout
