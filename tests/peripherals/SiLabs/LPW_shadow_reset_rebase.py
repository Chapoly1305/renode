"""Compatibility RF state must reset without overriding modeled registers.

These test retained fallback/serial-scheduler behavior, not silicon fidelity.
"""
import unittest

import test_lib


class LpwResetRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cmd = test_lib.execute_monitor_command
        cmd('mach create "rf-reset"')
        cmd('machine LoadPlatformDescription @platforms/boards/silabs/brd2601b.repl')
        cls.radio = test_lib._e.get_mach('rf-reset').sysbus.radio.internal
        cls.synth_cal_reset = int(cls.radio.ReadDoubleWordFromSynthesizer(0x2C))

    def setUp(self):
        self.radio.Reset()

    def test_01_fallback_secure_aliases_share_storage(self):
        r = self.radio
        r.WriteDoubleWordToSynthesizer(0xFFC, 0x11)
        r.WriteDoubleWordToSynthesizerNonSecure(0x1FFC, 0x22)
        r.WriteDoubleWordToSynthesizer(0x2FFC, 0x10)
        r.WriteDoubleWordToSynthesizerNonSecure(0x3FFC, 0x01)
        self.assertEqual(int(r.ReadDoubleWordFromSynthesizer(0xFFC)), 0x22)
        self.assertEqual(int(r.ReadDoubleWordFromSynthesizerNonSecure(0xFFC)), 0x22)

    def test_02_reset_restores_predefined_fallback_value(self):
        self.radio.WriteDoubleWordToSynthesizer(0x2C, 0xDEADBEEF)
        self.radio.Reset()
        self.assertEqual(int(self.radio.ReadDoubleWordFromSynthesizer(0x2C)), self.synth_cal_reset)

    def test_03_reset_discards_unhandled_register_writes(self):
        self.radio.WriteDoubleWordToSynthesizer(0xFFC, 0x12345678)
        self.radio.Reset()
        self.assertEqual(int(self.radio.ReadDoubleWordFromSynthesizer(0xFFC)), 0)

    def test_04_modeled_readonly_fields_take_precedence(self):
        before = int(self.radio.ReadDoubleWordFromRadioControllerNonSecure(0x0C))
        self.radio.WriteDoubleWordToRadioControllerNonSecure(0x0C, 0xFFFFFFFF)
        # DEMODENS (bit 29) is writable; the remaining status fields are not.
        mask = 0xDFFFFFFF
        self.assertEqual(int(self.radio.ReadDoubleWordFromRadioControllerNonSecure(0x0C)) & mask, before & mask)

    def test_05_reset_discards_storage_poll_streak(self):
        r = self.radio
        both = (1 << 2) | (1 << 18) | (1 << 27)
        r.WriteDoubleWordToRadioControllerNonSecure(0x44, both)
        for _ in range(127):
            self.assertEqual(int(r.ReadDoubleWordFromRadioController(0x44)), both)
        r.Reset()
        r.WriteDoubleWordToRadioControllerNonSecure(0x44, both)
        for _ in range(127):
            self.assertEqual(int(r.ReadDoubleWordFromRadioController(0x44)), both)
        self.assertEqual(int(r.ReadDoubleWordFromRadioController(0x44)), both & ~(1 << 2))
        self.assertEqual(int(r.ReadDoubleWordFromRadioControllerNonSecure(0x44)), both)

    def test_06_nonsecure_reads_preserve_the_handshake(self):
        both = (1 << 2) | (1 << 18)
        self.radio.WriteDoubleWordToRadioControllerNonSecure(0x44, both)
        for _ in range(256):
            self.assertEqual(int(self.radio.ReadDoubleWordFromRadioControllerNonSecure(0x44)), both)


if __name__ == '__main__':
    unittest.main(verbosity=2)
