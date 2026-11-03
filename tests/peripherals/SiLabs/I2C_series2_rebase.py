"""Series-2 aliases and an empty I2C bus must preserve real NACKs."""
import unittest

import test_lib


class Series2I2cRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cmd = test_lib.execute_monitor_command
        cmd('mach create "i2c"')
        cmd('machine LoadPlatformDescriptionFromString "i2c: I2C.EFR32xG2_I2CController @ sysbus <0, +0x4000>"')
        cls.i2c = test_lib._e.get_mach('i2c').sysbus.i2c.internal

    def setUp(self):
        self.i2c.Reset()

    def read(self, offset):
        return int(self.i2c.ReadDoubleWord(offset))

    def write(self, offset, value):
        self.i2c.WriteDoubleWord(offset, value)

    def address_absent_peer(self, command=0x0C):
        self.write(8, 1)
        self.write(0x34, 0x90)  # SE05x address 0x48, write direction.
        self.write(command, 1)

    def test_control_set_clear_toggle(self):
        self.write(0x1008, 5)
        self.assertEqual(self.read(8) & 5, 5)
        self.write(0x2008, 1)
        self.assertEqual(self.read(8) & 5, 4)
        self.write(0x3008, 5)
        self.assertEqual(self.read(8) & 5, 1)

    def test_interrupt_enable_aliases(self):
        self.write(0x1040, 0x81)
        self.assertEqual(self.read(0x40), 0x81)
        self.write(0x2040, 1)
        self.assertEqual(self.read(0x40), 0x80)
        self.write(0x3040, 0x80)
        self.assertEqual(self.read(0x40), 0)

    def test_alias_reads_resolve_to_base(self):
        self.write(8, 5)
        for window in (0x1000, 0x2000, 0x3000):
            self.assertEqual(self.read(window + 8), self.read(8))

    def test_absent_peer_nacks_even_with_interrupt_disabled(self):
        self.address_absent_peer()
        self.assertEqual(self.read(0x3C) & 0xC0, 0x80)
        self.assertFalse(self.i2c.IRQ.IsSet)

    def test_irq_enable_and_clear_follow_pending_nack(self):
        self.address_absent_peer()
        self.write(0x1040, 0x80)
        self.assertTrue(self.i2c.IRQ.IsSet)
        self.write(0x203C, 0x80)
        self.assertFalse(self.i2c.IRQ.IsSet)
        self.assertEqual(self.read(0x3C) & 0x80, 0)

    def test_command_set_alias_does_not_fabricate_ack(self):
        self.write(0x40, 0xFFFF)
        self.address_absent_peer(0x100C)
        self.assertEqual(self.read(0x3C) & 0xC0, 0x80)

    def test_flag_set_clear_toggle(self):
        bit = 1 << 10
        self.write(0x103C, bit)
        self.assertEqual(self.read(0x3C) & bit, bit)
        self.write(0x303C, bit)
        self.assertEqual(self.read(0x3C) & bit, 0)
        self.write(0x303C, bit)
        self.assertEqual(self.read(0x3C) & bit, bit)
        self.write(0x203C, bit)
        self.assertEqual(self.read(0x3C) & bit, 0)

    def test_alias_zero_does_not_change_unrelated_pending_events(self):
        self.address_absent_peer()
        before = self.read(0x3C)
        self.write(0x103C, 0)
        self.write(0x203C, 0)
        self.write(0x303C, 0)
        self.assertEqual(self.read(0x3C), before)
        self.write(0x103C, 1 << 10)
        self.assertEqual(self.read(0x3C), before | (1 << 10))
        self.write(0x203C, 1 << 10)
        self.assertEqual(self.read(0x3C), before)


if __name__ == '__main__':
    unittest.main(verbosity=2)
