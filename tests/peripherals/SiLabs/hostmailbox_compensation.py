"""Run small CPU polling fixtures against the compiled xG24 mailbox model.

These are synthetic unit programs, not modified production firmware or boot
measurements. They exercise real CPU bus-access context rather than mocking it.
"""
import struct
import unittest

import test_lib


class HostMailboxCompensation(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cls.cmd = staticmethod(test_lib.execute_monitor_command)
        cls.cmd('mach create "mailbox-fixture"')
        cls.cmd('machine LoadPlatformDescription @platforms/boards/silabs/brd2601b.repl')
        cls.machine = test_lib._e.get_mach("mailbox-fixture")
        cls.radio = cls.machine.sysbus.radio.internal
        cls.bus = cls.machine.sysbus.internal
        if cls.radio.HostMailboxDoorbellCompensationEnabled:
            raise AssertionError("Generic xG24 platform must default to uncompensated mailbox behaviour")

    def setUp(self):
        self.cmd('machine Reset')
        self.cmd('cpu IsHalted true')
        self.cmd('seqcpu IsHalted true')
        self.radio.HostMailboxDoorbellCompensationEnabled = False
        self.cmd('cpu PRIMASK 1')
        self.cmd('seqcpu PRIMASK 1')

    def write(self, address, value):
        self.bus.WriteDoubleWord(address, value)

    def arm(self, host_flags=1, host_enable=1, radio_flags=1, radio_enable=1):
        self.write(0x50098040, host_flags)
        self.write(0x50098044, host_enable)
        self.write(0xB802C040, radio_flags)
        self.write(0xB802C044, radio_enable)

    def poll(self, cpu="seqcpu", seconds=0.002, masked=True):
        # ldr r0, literal; ldr r1,[r0]; b back to ldr r1; literal = HOSTMAILBOX.IF
        base = 0xA0000100 if cpu == "seqcpu" else 0x20000100
        pc = 0x100 if cpu == "seqcpu" else base
        program = bytes.fromhex("01480168fde700bf") + struct.pack('<I', 0x50098040)
        for i, byte in enumerate(program):
            self.bus.WriteByte(base + i, byte)
        if cpu == "seqcpu":
            self.write(0xA0000000, 0x20004000)
            self.write(0xA0000004, pc | 1)
            self.cmd('seqcpu VectorTableOffset 0')
            self.machine.sysbus.seqcpu.internal.CpuWaitSignal.Set(False)
        else:
            self.write(0x20000000, 0x20004000)
            self.write(0x20000004, pc | 1)
            self.cmd('cpu VectorTableOffset 0x20000000')
        self.cmd(f'{cpu} PC {pc:#x}')
        self.cmd(f'{cpu} PRIMASK {int(masked)}')
        self.cmd(f'{cpu} PerformanceInMips 39')
        self.cmd(f'{cpu} IsHalted false')
        self.cmd(f'emulation RunFor "{seconds}"')
        self.cmd(f'{cpu} IsHalted true')

    def flags(self):
        return int(self.bus.ReadDoubleWord(0x50098040))

    def test_01_disabled_by_default(self):
        self.assertFalse(self.radio.HostMailboxDoorbellCompensationEnabled)
        self.arm()
        self.poll()
        self.assertEqual(self.flags(), 1)
        self.assertEqual(self.radio.HostMailboxDoorbellCompensationCount, 0)

    def test_02_mutual_masked_poll_only_clears_enabled_host_bits(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm(host_flags=5)
        self.poll()
        self.assertEqual(self.flags(), 4)
        self.assertEqual(self.radio.HostMailboxDoorbellCompensationCount, 1)
        self.assertEqual(int(self.bus.ReadDoubleWord(0xB802C040)), 1)
        self.assertFalse(self.radio.HostMailboxIRQ.IsSet)

    def test_03_host_cpu_reads_do_not_acknowledge(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm()
        self.poll(cpu="cpu")
        self.assertEqual(self.flags(), 1)
        self.assertEqual(self.radio.HostMailboxDoorbellCompensationCount, 0)

    def test_04_monitor_reads_do_not_acknowledge(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm()
        for _ in range(17000):
            self.assertEqual(self.radio.ReadDoubleWordFromHostMailboxNonSecure(0x40), 1)
        self.assertEqual(self.flags(), 1)
        self.assertEqual(self.radio.HostMailboxDoorbellCompensationCount, 0)

    def test_05_unmasked_host_is_not_compensated(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm()
        self.cmd('cpu PRIMASK 0')
        self.poll()
        self.assertEqual(self.flags(), 1)
        self.assertEqual(self.radio.HostMailboxDoorbellCompensationCount, 0)

    def test_06_unmasked_sequencer_is_not_compensated(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm()
        self.poll(masked=False)
        self.assertEqual(self.flags(), 1)
        self.assertEqual(self.radio.HostMailboxDoorbellCompensationCount, 0)

    def test_07_no_radio_doorbell_is_not_compensated(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm(radio_flags=0)
        self.poll()
        self.assertEqual(self.flags(), 1)

    def test_08_disabled_doorbells_are_not_compensated(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm(host_enable=0)
        self.poll()
        self.assertEqual(self.flags(), 1)
        self.arm(radio_enable=0)
        self.poll()
        self.assertEqual(self.flags(), 1)

    def test_09_short_poll_and_mailbox_write_reset_the_threshold(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm()
        self.poll(seconds=0.0005)
        self.assertEqual(self.flags(), 1)
        self.write(0x50098000, 0x12345678)
        self.poll(seconds=0.0005)
        self.assertEqual(self.flags(), 1)
        self.assertEqual(int(self.bus.ReadDoubleWord(0x50098000)), 0x12345678)

    def test_10_reset_clears_activation_count(self):
        self.radio.HostMailboxDoorbellCompensationEnabled = True
        self.arm()
        self.poll()
        self.assertEqual(self.radio.HostMailboxDoorbellCompensationCount, 1)
        self.cmd('machine Reset')
        self.assertEqual(self.radio.HostMailboxDoorbellCompensationCount, 0)
        self.assertEqual(self.flags(), 0)


if __name__ == "__main__":
    unittest.main(verbosity=2)
