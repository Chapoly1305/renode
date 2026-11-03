"""RX enable/block semantics, preserving the physical EUSART2 FIFO capacity."""
import unittest

import test_lib


class EusartRxBlockRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cmd = test_lib.execute_monitor_command
        cmd('mach create "eusart"')
        cmd('machine LoadPlatformDescriptionFromString "eusart: UART.SiLabs_EUSART_2 @ sysbus <0, +0x4000>; cmu: Miscellaneous.SiLabs.SiLabs_CMU_1 @ sysbus <0x4000, +0x4000> { hfxoFrequency: 39000000 }"')
        cls.uart = test_lib._e.get_mach('eusart').sysbus.eusart.internal

    def setUp(self):
        self.uart.Reset()
        self.uart.WriteDoubleWord(4, 1)

    def command(self, value):
        self.uart.WriteDoubleWord(0x38, value)

    def read(self):
        return int(self.uart.ReadDoubleWord(0x3C))

    def test_disabled_receiver_discards_input(self):
        self.uart.WriteChar(0x55)
        self.assertEqual(self.read(), 0)

    def test_enabled_receiver_accepts_input(self):
        self.command(1)
        self.uart.WriteChar(0x55)
        self.assertEqual(self.read(), 0x55)

    def test_rxblock_discards_new_input_and_reports_status(self):
        self.command(1)
        self.command(1 << 4)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x48)) & 8, 8)
        self.uart.WriteChar(0x55)
        self.assertEqual(self.read(), 0)

    def test_unblock_restores_receive(self):
        self.command(1 | (1 << 4))
        self.command(1 << 5)
        self.assertEqual(int(self.uart.ReadDoubleWord(0x48)) & 8, 0)
        self.uart.WriteChar(0x55)
        self.assertEqual(self.read(), 0x55)

    def test_block_preserves_previously_queued_bytes(self):
        self.command(1)
        self.uart.WriteChar(0xAA)
        self.command(1 << 4)
        self.uart.WriteChar(0x55)
        self.assertEqual(self.read(), 0xAA)
        self.assertEqual(self.read(), 0)

    def test_reset_clears_rxblock(self):
        self.command(1 | (1 << 4))
        self.uart.Reset()
        self.assertEqual(int(self.uart.ReadDoubleWord(0x48)) & 8, 0)
        self.command(1)
        self.uart.WriteChar(0x55)
        self.assertEqual(self.read(), 0x55)


if __name__ == '__main__':
    unittest.main(verbosity=2)
