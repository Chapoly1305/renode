"""Replay the deployed EUSART4 synchronous-completion patch recovered from lab."""
import unittest

import test_lib


class Eusart4SpiRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cmd = test_lib.execute_monitor_command
        cmd('mach create "spi4"')
        cmd('machine LoadPlatformDescriptionFromString "eusart: UART.SiLabs_EUSART_4 @ sysbus <0, +0x4000>; cmu: Miscellaneous.SiLabs.SiLabs_CMU_1 @ sysbus <0x4000, +0x4000> { hfxoFrequency: 39000000 }"')
        cls.uart = test_lib._e.get_mach('spi4').sysbus.eusart.internal

    def setUp(self):
        self.uart.Reset()
        self.uart.WriteDoubleWord(4, 1)
        self.uart.WriteDoubleWord(8, 1)
        self.uart.WriteDoubleWord(0x38, 5)
        self.uart.WriteDoubleWord(0x204C, 3)

    def test_sync_transfer_raises_complete_and_ready(self):
        self.uart.WriteDoubleWord(0x44, 0x9F)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x4C)) & 3, 3)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x48)) & 0x20, 0x20)

    def test_every_sync_byte_rearms_irq(self):
        self.uart.WriteDoubleWord(0x50, 3)
        for value in (0x9F, 0, 0xFF):
            self.uart.WriteDoubleWord(0x204C, 3)
            self.assertFalse(self.uart.TransmitIRQ.IsSet)
            self.uart.WriteDoubleWord(0x44, value)
            self.assertTrue(self.uart.TransmitIRQ.IsSet)

    def test_async_completion_is_preserved(self):
        self.uart.WriteDoubleWord(8, 0)
        self.uart.WriteDoubleWord(0x44, 0x55)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x4C)) & 3, 3)

    def test_disabled_transmitter_does_not_complete(self):
        self.uart.WriteDoubleWord(0x38, 8)
        self.uart.WriteDoubleWord(0x44, 0x55)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x4C)) & 3, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
