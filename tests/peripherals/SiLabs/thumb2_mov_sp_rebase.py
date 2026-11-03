"""CPU instruction control for the historical MG21 Thumb-2 MOV.W SP fix."""
import struct
import unittest

import test_lib


class ThumbMovSpRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        test_lib.create_emulation(rng_seed=0x2603)
        cls.cmd = staticmethod(test_lib.execute_monitor_command)
        cls.nodes = {}
        for core in ('cortex-m33', 'cortex-m85'):
            cls.cmd(f'mach create "{core}"')
            description = f'using "platforms/boards/silabs/brd2601b.repl"; cpu: {{ cpuType: "{core}" }}'
            cls.cmd(f'machine LoadPlatformDescriptionFromString """{description}"""')
            node = test_lib._e.get_mach(core)
            node.sysbus.cpu.internal.IsHalted = True
            node.sysbus.seqcpu.internal.IsHalted = True
            cls.nodes[core] = node

    def check_program(self, instruction, core='cortex-m33', expected=0x20002000):
        self.cmd(f'mach set "{core}"')
        self.cmd('machine Reset')
        self.cmd('cpu IsHalted true')
        self.cmd('seqcpu IsHalted true')
        bus = self.nodes[core].sysbus.internal
        # mov r0,sp; ldr r1,literal; str r0,[r1]; b .; nop; result address.
        program = instruction + bytes.fromhex('01490860fee700bf00100020')
        for offset, value in enumerate(program):
            bus.WriteByte(0x20000100 + offset, value)
        for offset, value in enumerate(struct.pack('<II', 0x20002000, 0x20000101)):
            bus.WriteByte(0x20000000 + offset, value)
        # An invalid instruction must terminate in a real HardFault handler,
        # rather than recursing through an uninitialized exception vector.
        bus.WriteDoubleWord(0x2000000C, 0x20000201)
        bus.WriteWord(0x20000200, 0xE7FE)
        bus.WriteDoubleWord(0x20001000, 0)
        self.cmd('cpu VectorTableOffset 0x20000000')
        self.cmd('cpu PerformanceInMips 39')
        self.cmd('cpu IsHalted false')
        self.cmd('emulation RunFor "0.001"')
        self.cmd('cpu IsHalted true')
        self.assertEqual(int(bus.ReadDoubleWord(0x20001000)), expected)

    def test_thumb2_mov_w_r0_sp(self):
        self.check_program(bytes.fromhex('4fea0d00'))

    def test_thumb16_mov_r0_sp_control(self):
        self.check_program(bytes.fromhex('684600bf'))

    def test_thumb2_mov_w_r0_sp_on_mve_core(self):
        self.check_program(bytes.fromhex('4fea0d00'), core='cortex-m85')

    def test_flag_setting_sp_encoding_remains_rejected_on_m33(self):
        self.check_program(bytes.fromhex('5fea0d00'), expected=0)

    def test_shifted_sp_encoding_remains_rejected(self):
        self.check_program(bytes.fromhex('4fea4d00'), expected=0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
