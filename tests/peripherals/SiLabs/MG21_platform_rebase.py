"""Compiled MG21 platform contracts; synthetic startup, not a vendor boot proof."""
import struct
import unittest

import test_lib


class Mg21PlatformRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cls.cmd = staticmethod(test_lib.execute_monitor_command)
        cls.cmd('mach create "mg21"')
        cls.cmd('machine LoadPlatformDescription @platforms/boards/silabs/brd4180a.repl')
        cls.node = test_lib._e.get_mach('mg21')
        cls.bus = cls.node.sysbus.internal
        cls.cmd('cpu IsHalted true')
        cls.cmd('seqcpu IsHalted true')

    def test_board_memory_geometry(self):
        self.assertEqual(int(self.node.sysbus.sram.internal.Size), 0x18000)
        self.assertEqual(int(self.node.sysbus.flash.internal.Size), 0xFE000)
        self.assertEqual(int(self.node.sysbus.lockbitsdata.internal.Size), 0x2000)
        self.assertEqual(int(self.bus.ReadByte(0x80000)), 0xFF)

    def test_part_identity_and_crystal(self):
        self.assertEqual(int(self.bus.ReadDoubleWord(0x0FE08004)), ((200 & 0x3F) << 16) | 1)
        self.assertEqual(int(self.node.sysbus.cmu.internal.HfxoFrequency), 38400000)
        calibration = int(self.node.sysbus.nvic.internal.ReadDoubleWord(0x1C)) & 0xFFFFFF
        self.assertEqual(calibration * 100, 38400000)

    def test_both_i2c_instances_preserve_empty_bus_nack(self):
        for name, base in (('i2c0', 0x5A010000), ('i2c1', 0x50068000)):
            with self.subTest(instance=name):
                peripheral = getattr(self.node.sysbus, name).internal
                peripheral.Reset()
                self.assertEqual(peripheral.GetType().Name, 'EFR32xG2_I2CController')
                self.bus.WriteDoubleWord(base + 0x1008, 1)
                self.bus.WriteDoubleWord(base + 0x34, 0x90)
                self.bus.WriteDoubleWord(base + 0x0C, 1)
                self.assertEqual(int(self.bus.ReadDoubleWord(base + 0x3C)) & 0xC0, 0x80)

    def test_mg21_hfxo_profile_enables_its_unlock_path(self):
        device = self.node.sysbus.hfxo.internal
        device.Reset()
        device.WriteDoubleWord(0x28, 0)
        self.assertTrue(int(device.ReadDoubleWord(0x58)) & (1 << 30))
        device.WriteDoubleWord(0x28, 2)
        self.assertFalse(int(device.ReadDoubleWord(0x58)) & (1 << 30))

    def test_synthetic_app_vector_and_mov_sp_execute(self):
        # Matches the historical app load address. No real firmware is changed.
        for offset, value in enumerate(struct.pack('<II', 0x20010000, 0x6101)):
            self.bus.WriteByte(0x6000 + offset, value)
        program = bytes.fromhex('4fea0d0001490860fee700bf00100020')
        for offset, value in enumerate(program):
            self.bus.WriteByte(0x6100 + offset, value)
        self.bus.WriteDoubleWord(0x20001000, 0)
        self.cmd('cpu VectorTableOffset 0x6000')
        self.cmd('cpu PerformanceInMips 38')
        self.cmd('cpu IsHalted false')
        try:
            self.cmd('emulation RunFor "0.001"')
        finally:
            self.cmd('cpu IsHalted true')
        self.assertEqual(int(self.bus.ReadDoubleWord(0x20001000)), 0x20010000)


if __name__ == '__main__':
    unittest.main(verbosity=2)
