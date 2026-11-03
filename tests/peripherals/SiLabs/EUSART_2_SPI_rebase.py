"""Recovered deployed synchronous completion and LG polled TX-ready semantics."""
import unittest

import test_lib


class EusartSpiRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cmd = test_lib.execute_monitor_command
        cmd('mach create "spi"')
        cmd('machine LoadPlatformDescriptionFromString "eusart: UART.SiLabs_EUSART_2 @ sysbus <0, +0x4000>; cmu: Miscellaneous.SiLabs.SiLabs_CMU_1 @ sysbus <0x4000, +0x4000> { hfxoFrequency: 39000000 }"')
        cls.uart = test_lib._e.get_mach('spi').sysbus.eusart.internal

    def setUp(self):
        self.uart.Reset()
        self.uart.WriteDoubleWord(4, 1)
        self.uart.WriteDoubleWord(8, 1)  # SYNC mode.
        self.uart.WriteDoubleWord(0x38, 5)  # RXEN | TXEN.

    def test_sync_transfer_sets_completion_and_ready_flags(self):
        self.uart.WriteDoubleWord(0x204C, 3)
        self.uart.WriteDoubleWord(0x44, 0x9F)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x4C)) & 3, 3)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x48)) & 0x20, 0x20)

    def test_tx_ready_level_survives_software_flag_clear(self):
        self.uart.WriteDoubleWord(0x204C, 2)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x4C)) & 2, 2)

    def test_tx_ready_irq_tracks_enable(self):
        self.uart.WriteDoubleWord(0x50, 2)
        self.assertTrue(self.uart.TransmitIRQ.IsSet)
        self.uart.WriteDoubleWord(0x204C, 2)
        self.assertTrue(self.uart.TransmitIRQ.IsSet)
        self.uart.WriteDoubleWord(0x2050, 2)
        self.assertFalse(self.uart.TransmitIRQ.IsSet)

    def test_tx_disable_deasserts_ready_level(self):
        self.uart.WriteDoubleWord(0x50, 2)
        self.uart.WriteDoubleWord(0x38, 8)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x4C)) & 2, 0)
        self.assertFalse(self.uart.TransmitIRQ.IsSet)

    def test_status_tx_fifo_ready_is_read_only(self):
        self.uart.WriteDoubleWord(0x2048, 1 << 6)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x48)) & (1 << 6), 1 << 6)


if __name__ == '__main__':
    unittest.main(verbosity=2)
