"""Public SPI transactions covering the physical NOR deltas in local R4."""
import unittest

import test_lib
from System import Array, Object
from System.Reflection import BindingFlags


class SpiNorRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cmd = test_lib.execute_monitor_command
        cmd('mach create "nor"')
        cmd('machine LoadPlatformDescriptionFromString "spi: UART.SiLabs_EUSART_2 @ sysbus <0, +0x4000>; cmu: Miscellaneous.SiLabs.SiLabs_CMU_1 @ sysbus <0x4000, +0x4000> { hfxoFrequency: 39000000 }; backing: Memory.MappedMemory { size: 0x2000000 }; flash: SPI.GenericSpiFlash @ spi { underlyingMemory: backing; manufacturerId: 0xC2; memoryType: 0x20; capacityCode: 0x19 }"')
        cls.flash = test_lib._e.get_mach('nor').sysbus.spi.flash.internal

    def setUp(self):
        self.flash.Reset()

    def transaction(self, values):
        try:
            return [int(self.flash.Transmit(value)) for value in values]
        finally:
            self.flash.FinishTransmission()

    def program(self, address, value):
        self.transaction([6])
        self.transaction([2, (address >> 16) & 255, (address >> 8) & 255, address & 255, value])

    def read(self, address):
        return self.transaction([3, (address >> 16) & 255, (address >> 8) & 255, address & 255, 0])[-1]

    def test_programming_can_only_clear_bits(self):
        self.program(0x100, 0xF0)
        self.program(0x100, 0x0F)
        self.assertEqual(self.read(0x100), 0)
        self.program(0x100, 0xFF)
        self.assertEqual(self.read(0x100), 0)

    def test_extended_address_selects_the_same_write_and_read_bank(self):
        self.program(0x200, 0xA5)
        self.transaction([6])
        self.transaction([0xC5, 1])
        self.program(0x200, 0x5A)
        self.assertEqual(self.read(0x200), 0x5A)
        self.transaction([6])
        self.transaction([0xC5, 0])
        self.assertEqual(self.read(0x200), 0xA5)

    def test_read_one_past_end_is_rejected(self):
        self.assertEqual(self.transaction([0x13, 2, 0, 0, 0, 0])[-1], 0)

    def test_write_one_past_end_is_rejected(self):
        self.transaction([6])
        self.transaction([0x12, 2, 0, 0, 0, 0x12])
        self.assertEqual(self.read(0), 0xFF)

    def test_subclass_write_verifier_rejects_exact_end_address(self):
        # Derived flash models use this protected contract before accessing the
        # backing store. Parse a real command and check it before any data byte.
        try:
            self.transaction([6])
            for value in [0x12, 2, 0, 0, 0]:
                self.flash.Transmit(value)
            verify = self.flash.GetType().GetMethod('TryVerifyWriteToMemory', BindingFlags.Instance | BindingFlags.NonPublic)
            parameters = Array[Object]([None])
            self.assertFalse(verify.Invoke(self.flash, parameters))
            self.assertEqual(int(parameters[0]), 0x2000000)
        finally:
            self.flash.FinishTransmission()

    def test_write_without_write_enable_is_ignored(self):
        before = self.read(0x300)
        self.transaction([2, 0, 3, 0, 0x12])
        self.assertEqual(self.read(0x300), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
