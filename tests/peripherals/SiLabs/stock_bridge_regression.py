"""Exercise compiled bridges and real loopback UDP without target firmware.

The peer below is a registered bridge with a supplied PHY signature, not a
hardware radio model. These tests check bridge transport and queue semantics.
"""
import binascii
import socket
import time
import unittest

import test_lib
from System import Action, Array, Byte
from Antmicro.Renode.Peripherals.Wireless import (
    IRadio, InterferenceQueue, PhySignature, RadioModulationFamily, RadioSymbolCoding,
)


def netbytes(data):
    return Array[Byte](data)


def fcs(data):
    # KERMIT via the non-reflected CCITT primitive, independent of the model's
    # per-bit implementation. Check the standard check-string before use.
    reverse = lambda b: int(f"{b:08b}"[::-1], 2)
    crc = binascii.crc_hqx(bytes(reverse(b) for b in data), 0)
    return bytes((reverse(crc >> 8), reverse(crc & 255)))


class BridgeRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        assert fcs(b"123456789") == bytes.fromhex("8921")
        test_lib.create_emulation(rng_seed=0x2603)
        cls.cmd = staticmethod(test_lib.execute_monitor_command)
        cls.cmd('mach create "bridge"')
        cls.cmd('machine LoadPlatformDescriptionFromString "ble: Wireless.BleCentralBridge @ sysbus <0x10000, +0x100> { port: 43500 }; bridge: Wireless.Ieee802154HostBridge @ sysbus <0x20000, +0x100> { channel: 15; portBase: 49000 }"')
        cls.machine = test_lib._e.get_mach("bridge")
        cls.peer = cls.machine.sysbus.ble.internal
        cls.bridge = cls.machine.sysbus.bridge.internal
        cls.oqpsk = PhySignature(RadioModulationFamily.Oqpsk, RadioSymbolCoding.Dsss, 250000, 31, 3, 0, 1)
        cls.ble = PhySignature(RadioModulationFamily.Fsk, RadioSymbolCoding.Nrz, 1000000, 0, 0, 0, 1)
        cls.frames = []
        cls.handler = Action[IRadio, Array[Byte]](lambda sender, data: cls.frames.append(bytes(data)))
        cls.bridge.FrameSent += cls.handler
        cls.listener = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cls.listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        cls.listener.bind(("", 49000))
        cls.listener.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP, socket.inet_aton("224.0.0.116") + socket.inet_aton("127.0.0.1"))
        cls.listener.settimeout(1)
        cls.host = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        cls.host.setsockopt(socket.IPPROTO_IP, socket.IP_MULTICAST_IF, socket.inet_aton("127.0.0.1"))

    @classmethod
    def tearDownClass(cls):
        cls.listener.close()
        cls.host.close()
        cls.bridge.FrameSent -= cls.handler

    def setUp(self):
        self.bridge.Reset()
        self.frames.clear()
        for sender in (self.peer, self.bridge):
            if InterferenceQueue.TryGetTxSignature(sender)[0]:
                InterferenceQueue.Remove(sender, False)

    def run_for(self, seconds=0.005):
        self.cmd(f'emulation RunFor "{seconds}"')

    def learn(self, signature=None):
        mpdu = bytes.fromhex("01002a0000")
        InterferenceQueue.Add(self.peer, signature or self.oqpsk, self.bridge.Channel, 0, netbytes(mpdu))
        self.bridge.ReceiveFrame(netbytes(b"\xa7\x05" + mpdu), self.peer)

    def host_send(self, mpdu, channel=15):
        self.host.sendto(bytes((channel,)) + mpdu, ("224.0.0.116", 49000))
        time.sleep(0.02)  # let the actual UDP RX thread enqueue the time-domain event
        self.run_for()

    def test_01_unknown_phy_drops_host_frame(self):
        self.host_send(bytes.fromhex("01002b0000"))
        self.assertEqual(self.frames, [])

    def test_02_ble_signature_cannot_teach_802154(self):
        self.learn(self.ble)
        self.host_send(bytes.fromhex("01002b0000"))
        self.assertEqual(self.frames, [])

    def test_03_device_to_udp_repairs_fcs(self):
        self.learn()
        self.run_for()
        expected = b"\x0f\x01\x00\x2a" + fcs(bytes.fromhex("01002a"))
        for _ in range(20):
            datagram, sender = self.listener.recvfrom(2048)
            if sender[1] == 49002:
                self.assertEqual(datagram, expected)
                break
        else:
            self.fail("No device frame relayed to UDP")

    def test_04_host_to_device_preserves_phy_framing_and_completion(self):
        self.learn()
        self.run_for()
        mpdu = bytes.fromhex("01002b1234")
        self.host_send(mpdu)
        self.assertEqual(self.frames, [b"\xa7\x05" + mpdu])
        found, signature = InterferenceQueue.TryGetTxSignature(self.bridge)
        self.assertTrue(found, "completed TX retains metadata for deferred RX")
        self.assertTrue(signature.IsDecodeCompatibleWith(self.oqpsk))
        self.assertFalse(InterferenceQueue.AnyOtherOnAir(self.peer, self.bridge.Channel))

    def test_05_wrong_channel_is_dropped(self):
        self.learn()
        self.run_for()
        self.host_send(bytes.fromhex("01002b0000"), channel=16)
        self.assertEqual(self.frames, [])

    def test_06_host_ack_request_gets_local_ack(self):
        self.learn()
        self.run_for()
        self.host_send(bytes.fromhex("21002c1234"))
        expected = b"\x0f\x02\x00\x2c" + fcs(bytes.fromhex("02002c"))
        for _ in range(30):
            datagram, sender = self.listener.recvfrom(2048)
            if sender[1] == 49002 and datagram == expected:
                break
        else:
            self.fail("Host ACK request was not acknowledged")
        self.assertEqual(len(self.frames), 1)

    def test_07_host_ack_frame_is_suppressed(self):
        self.learn()
        self.run_for()
        self.host_send(bytes.fromhex("02002d0000"))
        self.assertEqual(self.frames, [])

    def test_08_host_burst_is_delivered_in_order(self):
        self.learn()
        self.run_for()
        mpdus = [bytes((1, 0, sequence, 0, 0)) for sequence in (0x30, 0x31, 0x32)]
        for mpdu in mpdus:
            self.host.sendto(b"\x0f" + mpdu, ("224.0.0.116", 49000))
        time.sleep(0.02)
        self.run_for(0.02)
        self.assertEqual(self.frames, [b"\xa7\x05" + mpdu for mpdu in mpdus])
        self.assertFalse(InterferenceQueue.AnyOtherOnAir(self.peer, self.bridge.Channel))

    def test_09_reset_requires_fresh_phy_learning(self):
        self.learn()
        self.run_for()
        self.bridge.Reset()
        self.host_send(bytes.fromhex("0100330000"))
        self.assertEqual(self.frames, [])

    def test_10_ble_connect_ind_has_le_1m_signature_and_terminate_stops_polling(self):
        self.peer.Reset()
        sent = []
        handler = Action[IRadio, Array[Byte]](lambda sender, data: sent.append(bytes(data)))
        self.peer.FrameSent += handler
        try:
            advertisement = bytes.fromhex("d6be898e000901020304050616f6ff000000")
            self.peer.ReceiveFrame(netbytes(advertisement), self.bridge)
            self.run_for(0.001)
            self.assertTrue(sent)
            self.assertEqual(sent[0][4] & 15, 5)  # CONNECT_IND
            found, signature = InterferenceQueue.TryGetTxSignature(self.peer)
            self.assertTrue(found)
            self.assertTrue(signature.IsDecodeCompatibleWith(self.ble))
            terminate = bytes.fromhex("35b19aaf03020213000000")
            self.peer.ReceiveFrame(netbytes(terminate), self.bridge)
            count = len(sent)
            self.run_for(0.1)
            self.assertEqual(len(sent), count)
        finally:
            self.peer.FrameSent -= handler

    def test_11_ble_hops_across_all_37_physical_data_channels(self):
        self.peer.Reset()
        channels = []
        handler = Action[IRadio, Array[Byte]](lambda sender, data: channels.append(int(sender.Channel)))
        self.peer.FrameSent += handler
        try:
            self.peer.ReceiveFrame(netbytes(bytes.fromhex("d6be898e000901020304050616f6ff000000")), self.bridge)
            # A healthy synthetic peer answers each event, so supervision must
            # not engage while measuring the complete channel hop cycle.
            for _ in range(40):
                self.run_for(0.03)
                self.peer.ReceiveFrame(netbytes(bytes.fromhex("35b19aaf0100000000")), self.bridge)
            self.assertEqual(channels[0], 240200)
            expected = set(range(240400, 242600, 200)) | set(range(242800, 248000, 200))
            self.assertEqual(set(channels[1:]), expected)
            self.peer.ReceiveFrame(netbytes(bytes.fromhex("35b19aaf03020213000000")), self.bridge)
            self.run_for(0.1)
        finally:
            self.peer.FrameSent -= handler

    def test_12_silent_peer_is_abandoned_and_can_readvertise(self):
        self.peer.Reset()
        sent = []
        handler = Action[IRadio, Array[Byte]](lambda sender, data: sent.append(bytes(data)))
        self.peer.FrameSent += handler
        advertisement = netbytes(bytes.fromhex("d6be898e000901020304050616f6ff000000"))
        try:
            self.peer.ReceiveFrame(advertisement, self.bridge)
            self.run_for(1.2)
            self.assertEqual(self.peer.Channel, 240200)
            count = len(sent)
            self.run_for(0.1)
            self.assertEqual(len(sent), count)
            self.peer.ReceiveFrame(advertisement, self.bridge)
            self.run_for(0.001)
            self.assertEqual(sent[-1][4] & 15, 5)
            self.assertGreater(len(sent), count)
        finally:
            self.peer.FrameSent -= handler

    def test_13_reset_cancels_pending_connect_ind(self):
        self.peer.Reset()
        sent = []
        handler = Action[IRadio, Array[Byte]](lambda sender, data: sent.append(bytes(data)))
        self.peer.FrameSent += handler
        try:
            self.peer.ReceiveFrame(netbytes(bytes.fromhex("d6be898e000901020304050616f6ff000000")), self.bridge)
            self.peer.Reset()
            self.run_for(0.01)
            self.assertEqual(sent, [])
        finally:
            self.peer.FrameSent -= handler

    def test_14_old_connection_timer_cannot_inject_into_new_connection(self):
        self.peer.Reset()
        sent = []
        handler = Action[IRadio, Array[Byte]](lambda sender, data: sent.append(bytes(data)))
        self.peer.FrameSent += handler
        advertisement = netbytes(bytes.fromhex("d6be898e000901020304050616f6ff000000"))
        try:
            self.peer.ReceiveFrame(advertisement, self.bridge)
            self.run_for(0.0006)
            self.peer.Reset()
            self.peer.ReceiveFrame(advertisement, self.bridge)
            self.run_for(0.01)
            data_frames = [pdu for pdu in sent if pdu[:4] == bytes.fromhex("35b19aaf")]
            self.assertEqual(len(data_frames), 1)
        finally:
            self.peer.FrameSent -= handler


class RadioRoutingRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if getattr(test_lib, '_e', None) is None:
            test_lib.create_emulation(rng_seed=0x2603)
        cmd = test_lib.execute_monitor_command
        cmd('mach create "routing"')
        cmd('machine LoadPlatformDescription @platforms/boards/silabs/brd2601b.repl')
        cmd('machine LoadPlatformDescription @matter/renode-thread/scenarios/phase3-bridges.repl')
        cls.machine = test_lib._e.get_mach("routing")

    def tune(self, frequency_mhz):
        bus = self.machine.sysbus.internal
        # SYNTH frequency control word, HFXO=39MHz, LO divider=1.
        bus.WriteDoubleWord(0xB801803C, 1)
        bus.WriteDoubleWord(0xB8018034, round(frequency_mhz * (1 << 19) / 39))

    def test_ble_scan_key_matches_actual_radio_synth(self):
        self.tune(2402)
        self.assertEqual(self.machine.sysbus.radio.internal.Channel, 240200)
        self.assertEqual(self.machine.sysbus.bleCentral.internal.Channel, 240200)

    def test_all_thread_channels_match_actual_radio_and_keep_protocol_number(self):
        bridge = self.machine.sysbus.ieee802154.internal
        for channel in range(11, 27):
            with self.subTest(channel=channel):
                bridge.ThreadChannel = channel
                self.tune(2405 + 5 * (channel - 11))
                self.assertEqual(bridge.Channel, self.machine.sysbus.radio.internal.Channel)
                self.assertEqual(bridge.ThreadChannel, channel)


if __name__ == "__main__":
    unittest.main(verbosity=2)
