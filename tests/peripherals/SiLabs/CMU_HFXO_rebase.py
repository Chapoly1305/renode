"""Calibrate the same physical LF clock using each supported board crystal."""
import unittest

import test_lib


class CmuHfxoRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)

    def check_count(self, model, frequency, up, down, top, expected):
        name = f'cmu{model}_{frequency}_{up}_{down}_{top}'
        cmd = test_lib.execute_monitor_command
        cmd(f'mach create "{name}"')
        cmd(f'machine LoadPlatformDescriptionFromString "cmu: Miscellaneous.SiLabs.SiLabs_CMU_{model} @ sysbus <0, +0x4000> {{ hfxoFrequency: {frequency} }}"')
        cmu = test_lib._e.get_mach(name).sysbus.cmu.internal
        cmu.WriteDoubleWord(0x54, top | (up << 24) | (down << 28))
        self.assertEqual(int(cmu.ReadDoubleWord(0x58)), expected)

    def test_hfxo_upcounter_follows_board_crystal(self):
        for model in (3, 7):
            for frequency in (38400000, 39000000, 40000000):
                with self.subTest(model=model, frequency=frequency):
                    self.check_count(model, frequency, 2, 4, 128, frequency // 256)

    def test_hfxo_downcounter_follows_board_crystal(self):
        for model in (3, 7):
            for frequency in (38400000, 39000000, 40000000):
                with self.subTest(model=model, frequency=frequency):
                    self.check_count(model, frequency, 3, 3, 1000000, 32768000000 // frequency)

    def test_counter_saturates_at_twenty_bits(self):
        for model in (3, 7):
            with self.subTest(model=model):
                self.check_count(model, 38400000, 2, 4, 0xFFFFF, 0xFFFFF)


if __name__ == '__main__':
    unittest.main(verbosity=2)
