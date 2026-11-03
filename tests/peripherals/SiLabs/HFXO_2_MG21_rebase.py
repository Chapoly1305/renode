"""Keep the MG21 FSM unlock opt-in and preserve the existing MG22 path."""
import unittest

import test_lib


class HfxoMg21Regression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cls.devices = {}
        cmd = test_lib.execute_monitor_command
        for name, option in (('default', ''), ('mg21', '; releaseFsmLockOnDisableOnDemand: true')):
            cmd(f'mach create "{name}"')
            cmd(f'machine LoadPlatformDescriptionFromString "hfxo: Miscellaneous.SiLabs.SiLabs_HFXO_2 @ sysbus <0, +0x4000> {{ startupDelayTicks: 3{option} }}; cmu: Miscellaneous.SiLabs.SiLabs_CMU_1 @ sysbus <0x4000, +0x4000> {{ hfxoFrequency: 38400000 }}"')
            cls.devices[name] = test_lib._e.get_mach(name).sysbus.hfxo.internal

    def setUp(self):
        for device in self.devices.values():
            device.Reset()

    def locked(self, device):
        return bool(int(device.ReadDoubleWord(0x58)) & (1 << 30))

    def test_default_disable_on_demand_keeps_fsm_lock(self):
        device = self.devices['default']
        device.WriteDoubleWord(0x28, 0)
        self.assertTrue(self.locked(device))
        device.WriteDoubleWord(0x28, 2)
        self.assertTrue(self.locked(device))

    def test_mg21_disable_on_demand_releases_fsm_lock(self):
        device = self.devices['mg21']
        device.WriteDoubleWord(0x28, 0)
        self.assertTrue(self.locked(device))
        device.WriteDoubleWord(0x28, 2)
        self.assertFalse(self.locked(device))

    def test_default_manual_override_still_releases_lock(self):
        device = self.devices['default']
        device.WriteDoubleWord(0x28, 0)
        device.WriteDoubleWord(0x28, 3)
        device.WriteDoubleWord(0x50, 1)
        device.WriteDoubleWord(0x50, 2)
        self.assertFalse(self.locked(device))

    def test_mg21_can_lock_again_and_reset(self):
        device = self.devices['mg21']
        device.WriteDoubleWord(0x28, 0)
        device.WriteDoubleWord(0x28, 2)
        device.WriteDoubleWord(0x28, 0)
        self.assertTrue(self.locked(device))
        device.Reset()
        self.assertFalse(self.locked(device))


if __name__ == '__main__':
    unittest.main(verbosity=2)
