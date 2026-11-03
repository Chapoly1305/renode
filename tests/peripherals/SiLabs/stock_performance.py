"""Measure a production ELF's BLE polling throughput without firmware hooks.

Run inside the build image with test_lib configured. Disable .NET tiering for
both runtimes when comparing it; use identical image, firmware, clocks and seed.
This does not measure commissioning, Thread traffic or hardware timing fidelity.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import time

import test_lib


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--hostmailbox-compensation", action="store_true")
    args = parser.parse_args()
    data = args.elf.read_bytes()
    if not data.startswith(b"\x7fELF"):
        parser.error("--elf must contain a nonempty ELF")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    log = args.output.with_suffix(".renode.log").resolve()
    test_lib.create_emulation(rng_seed=0x2603, quantum_time=0.0001)
    cmd = test_lib.execute_monitor_command
    for command in [
        'mach create "performance"',
        'machine LoadPlatformDescription @platforms/boards/silabs/brd2601b.repl',
        'machine LoadPlatformDescription @matter/renode-thread/scenarios/phase3-bridges.repl',
        f'sysbus LoadELF @{args.elf.resolve()}',
        'cpu VectorTableOffset 0x08006000',
        'cpu PerformanceInMips 39', 'seqcpu PerformanceInMips 39',
        'emulation CreateBLEMedium "stock-wireless"',
        'connector Connect sysbus.radio stock-wireless',
        'connector Connect sysbus.bleCentral stock-wireless',
        'connector Connect sysbus.ieee802154 stock-wireless',
        'emulation SetGlobalSerialExecution true',
        'emulation SetGlobalAdvanceImmediately true',
        f'logFile @{log} true', 'logLevel 2',
    ]:
        cmd(command)
    if args.hostmailbox_compensation:
        cmd('sysbus.radio HostMailboxDoorbellCompensationEnabled true')
    records = []
    for index in range(4):
        wall, cpu = time.perf_counter(), time.process_time()
        cmd('emulation RunFor "1"')
        elapsed, used = time.perf_counter() - wall, time.process_time() - cpu
        records.append(dict(sample=index, virtual_seconds=1, wall_seconds=elapsed,
                            cpu_seconds=used, virtual_per_wall=1 / elapsed,
                            phase="boot_and_warmup" if index == 0 else "post_boot_ble"))
        print(json.dumps(records[-1]), flush=True)
        if index == 0:
            assert 'CHIPoBLE GATT ready' in log.read_text(), 'Warmup did not reach GATT'
    result = dict(seed="0x2603", quantum_microseconds=100, cpu_mips_each=39,
                  advance_immediately=True, serial_execution=True, gatt_ready=True,
                  hostmailbox_compensation=args.hostmailbox_compensation,
                  tiered_compilation_env=os.environ.get("DOTNET_TieredCompilation", "default"),
                  elf_sha256=hashlib.sha256(data).hexdigest(), records=records,
                  scope="Boot and BLE polling only; normal warning logging retained")
    args.output.write_text(json.dumps(result, indent=2) + "\n")


if __name__ == "__main__":
    main()
