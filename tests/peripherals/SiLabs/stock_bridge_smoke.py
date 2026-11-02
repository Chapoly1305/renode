"""Load the rebased BRD2601B and both stock-firmware bridges, without firmware.

This checks platform/model compatibility only, not commissioning or radio fidelity.
Run with RENODE_BASE_PATH pointing at the candidate Renode checkout.
"""
import test_lib

test_lib.create_emulation(rng_seed=0x2603)
test_lib.execute_monitor_command('mach create "stock-bridge-smoke"')
test_lib.execute_monitor_command(
    'machine LoadPlatformDescription @platforms/boards/silabs/brd2601b.repl'
)
test_lib.execute_monitor_command(
    'machine LoadPlatformDescription @matter/renode-thread/scenarios/phase3-bridges.repl'
)
test_lib.execute_monitor_command('emulation CreateBLEMedium "stock-wireless"')
for peripheral in ('radio', 'bleCentral', 'ieee802154'):
    test_lib.execute_monitor_command(f'connector Connect sysbus.{peripheral} stock-wireless')
test_lib.execute_monitor_command('machine Reset')
print('PASS: BRD2601B, BLE bridge, 802.15.4 bridge and medium loaded and reset')
