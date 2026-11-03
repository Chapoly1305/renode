"""Load all migrated profiles against compiled 26q3; check bus and time contracts.

No firmware or device provision pages are loaded. Class-B/C profiles remain
explicit compatibility/diagnostic fixtures, not evidence of hardware fidelity.
"""
from pathlib import Path
from itertools import count
import re
import unittest

import test_lib


ROOT = Path('matter/renode-thread/overlays')


class OverlayRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cls.cmd = staticmethod(test_lib.execute_monitor_command)
        cls.nodes = {}
        cls.names = count()

    def load(self, path):
        key = str(path.relative_to(ROOT))
        if key in self.nodes:
            return self.nodes[key]
        name = 'overlay_' + str(next(self.names))
        self.cmd(f'mach create "{name}"')
        additive = {
            'adc0_sixg301.repl': 'brd1019a',
            'aqara_smu.repl': 'brd2601b',
            'brd4117a_aqara.repl': 'brd4117a',
            'dcdc_xg24.repl': 'brd2601b',
            'dcdc_xg24_unreg.repl': 'brd2601b',
            'lg4142_xg26_eusart23_i2c123.repl': 'brd4116a',
            'lg4142_xg26_iadc0.repl': 'brd4116a',
            'tuya4701_userdata_page.repl': 'brd2601b',
            'compat/i2c0_constant_ack.repl': 'brd2601b',
            'compat/burtc_access_counter_xg24.repl': 'brd2601b',
            'compat/burtc_access_counter_xg26.repl': 'brd4117a',
        }
        if key in additive:
            self.cmd(f'machine LoadPlatformDescriptionFromString "using \\"platforms/boards/silabs/{additive[key]}.repl\\"; using \\"{path}\\""')
        elif path.stem.startswith('efr32xG') or path.stem == 'lg4142_xg26_cpu_i2c0s2':
            board = 'brd2601b' if 'G24' in path.stem else 'brd4116a'
            # SoC fragments deliberately require the board's memory sizes and
            # part selection. Exercise their intended use as that board's base.
            description = Path(f'platforms/boards/silabs/{board}.repl').read_text()
            description = re.sub(r'using "[^"]+"', f'using "{path}"', description, count=1)
            self.cmd(f'machine LoadPlatformDescriptionFromString """{description}"""')
        else:
            self.cmd(f'machine LoadPlatformDescription @{path}')
        node = test_lib._e.get_mach(name)
        node.sysbus.cpu.internal.IsHalted = True
        node.sysbus.seqcpu.internal.IsHalted = True
        self.nodes[key] = node
        return node

    def test_all_profiles_load(self):
        paths = sorted(ROOT.rglob('*.repl'))
        self.assertGreaterEqual(len(paths), 47)
        for path in paths:
            with self.subTest(profile=str(path)):
                self.load(path)

    def test_board_crystal_and_cpu_tick_match_selected_profile(self):
        for path in sorted(ROOT.glob('brd*.repl')):
            if '38m4' not in path.stem and '40m' not in path.stem:
                continue
            with self.subTest(profile=path.name):
                frequency = 38400000 if '38m4' in path.stem else 40000000
                node = self.load(path)
                self.assertEqual(int(node.sysbus.cmu.internal.HfxoFrequency), frequency)
                calibration = int(node.sysbus.nvic.internal.ReadDoubleWord(0x1C)) & 0xFFFFFF
                self.assertEqual(calibration * 100, frequency)

    def test_real_series2_i2c_on_both_chip_families(self):
        for name in ('brd2601b_mg24a_i2cxg2', 'brd4117a_i2cxg2'):
            with self.subTest(profile=name):
                node = self.load(ROOT / f'{name}.repl')
                self.assertEqual(node.sysbus.i2c0.internal.GetType().Name, 'EFR32xG2_I2CController')
                bus = node.sysbus.internal
                bus.WriteDoubleWord(0x5B001008, 1)
                bus.WriteDoubleWord(0x5B000034, 0x90)
                bus.WriteDoubleWord(0x5B00000C, 1)
                self.assertEqual(int(bus.ReadDoubleWord(0x5B00003C)) & 0xC0, 0x80)

    def test_burtc_uses_virtual_time_instead_of_bus_access_count(self):
        for name in ('brd2601b_mg24a_burtc', 'brd4117a_burtc_i2cxg2'):
            with self.subTest(profile=name):
                node = self.load(ROOT / f'{name}.repl')
                timer = node.sysbus.burtc.internal
                timer.Reset()
                timer.WriteDoubleWord(0xC, 1)
                self.cmd('emulation RunFor "0.01"')
                count = int(timer.ReadDoubleWord(0x20))
                self.assertIn(count, (327, 328))
                self.assertEqual(int(timer.ReadDoubleWord(0x20)), count)
                timer.WriteDoubleWord(0xC, 2)
                self.cmd('emulation RunFor "0.01"')
                self.assertEqual(int(timer.ReadDoubleWord(0x20)), count)

    def test_external_nor_identity_and_erased_contents(self):
        for name in ('brd2601b_mg24a_burtc_i2cxg2_extflash', 'brd4117a_burtc_i2cxg2_extflash'):
            with self.subTest(profile=name):
                node = self.load(ROOT / f'{name}.repl')
                flash = node.sysbus.eusart1.extFlash.internal
                try:
                    result = [int(flash.Transmit(v)) for v in (0x9F, 0, 0, 0)]
                    self.assertEqual(result[1:], [0xA1, 0x40, 0x17])
                finally:
                    flash.FinishTransmission()
                try:
                    result = [int(flash.Transmit(v)) for v in (3, 0, 0, 0, 0)]
                    self.assertEqual(result[-1], 0xFF)
                finally:
                    flash.FinishTransmission()


if __name__ == '__main__':
    unittest.main(verbosity=2)
