"""Exercise GPIO3 routing and edge state on the compiled peripheral model."""
import unittest

import test_lib


class GPIO3Regression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cmd = test_lib.execute_monitor_command
        cmd('mach create "gpio"')
        cmd('machine LoadPlatformDescriptionFromString "gpio: GPIOPort.SiLabs_GPIO_3 @ sysbus <0, +0x4000>"')
        cls.gpio = test_lib._e.get_mach('gpio').sysbus.gpio.internal

    def setUp(self):
        self.gpio.Reset()
        # One EXTI channel per pin in port A; reroute channel 1 to PB1.
        self.write(0x408, 0x32103210)
        self.write(0x40C, 0x32103210)
        self.write(0x400, 0x10)
        self.write(0x410, 0xFFFF)
        self.write(0x424, 3)

    def write(self, offset, value):
        self.gpio.WriteDoubleWord(offset, value)

    def flags(self):
        return int(self.gpio.ReadDoubleWord(0x420)) & 0xFFFF

    def clear(self):
        self.write(0x2420, 0xFFFF)

    def test_unrouted_pin_does_not_alias_exti0(self):
        self.gpio.OnGPIO(16, True)  # PB0 has no route.
        self.assertEqual(self.flags(), 0)
        self.assertFalse(self.gpio.EvenIRQ.IsSet)

    def test_routed_rising_edge_and_flag_clear(self):
        self.gpio.OnGPIO(0, True)
        self.assertEqual(self.flags(), 1)
        self.assertTrue(self.gpio.EvenIRQ.IsSet)
        self.clear()
        self.assertEqual(self.flags(), 0)
        self.gpio.OnGPIO(0, True)
        self.assertEqual(self.flags(), 0, 'Unchanged level must not retrigger')

    def test_high_port_pin_uses_its_own_previous_state(self):
        self.gpio.OnGPIO(17, True)  # PB1 -> EXTI1.
        self.assertEqual(self.flags(), 2)
        self.assertTrue(self.gpio.OddIRQ.IsSet)
        self.assertFalse(self.gpio.EvenIRQ.IsSet)
        self.clear()
        self.gpio.OnGPIO(18, True)  # Unrouted PB2 must not fabricate another edge.
        self.assertEqual(self.flags(), 0)

    def test_simultaneous_held_levels_have_independent_edge_state(self):
        self.gpio.OnGPIO(0, True)
        self.gpio.OnGPIO(17, True)
        self.assertEqual(self.flags(), 3)
        self.clear()
        self.gpio.OnGPIO(63, True)
        self.assertEqual(self.flags(), 0)

    def test_clearing_edge_trigger_does_not_enable_it(self):
        self.write(0x410, 0)
        self.write(0x414, 0)
        self.gpio.OnGPIO(0, True)
        self.gpio.OnGPIO(0, False)
        self.assertEqual(self.flags(), 0)

    def test_falling_edge_and_rerouting(self):
        self.write(0x414, 0xFFFF)
        self.gpio.OnGPIO(17, True)
        self.clear()
        self.gpio.OnGPIO(17, False)
        self.assertEqual(self.flags(), 2)
        self.clear()
        self.write(0x400, 0x20)  # EXTI1 now maps PC1, not PB1.
        self.gpio.OnGPIO(17, True)
        self.assertEqual(self.flags(), 0)
        self.gpio.OnGPIO(33, True)
        self.assertEqual(self.flags(), 2)

    def test_reset_clears_high_port_history_and_irqs(self):
        self.gpio.OnGPIO(17, True)
        self.setUp()
        self.assertEqual(self.flags(), 0)
        self.assertFalse(self.gpio.OddIRQ.IsSet)
        self.gpio.OnGPIO(17, True)
        self.assertEqual(self.flags(), 2)


if __name__ == '__main__':
    unittest.main(verbosity=2)
