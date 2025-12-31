#!/usr/bin/env python3
"""
BLE Bridge: Renode <-> BlueZ

This script bridges BLE frames between Renode simulation and host BlueZ.
Supports advertising, connection, and data transfer for Matter commissioning.

Protocol (UDP):
  [Type:1][Channel:1][Len:2 LE][Data:N]
  Type: 0x01 = TX (Renode -> Python), 0x02 = RX (Python -> Renode)

Usage:
  python3 ble_bridge.py [--renode-rx-port 5001] [--renode-tx-port 5000] [--hci 0]
"""

import argparse
import socket
import struct
import select
import sys
import os
import random
from dataclasses import dataclass, field
from typing import Optional, Dict, List, Any
from enum import IntEnum

# D-Bus imports (for BlueZ integration without root)
try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False

# =============================================================================
# BLE Constants
# =============================================================================

BLE_ADV_ACCESS_ADDR = 0x8E89BED6
BLE_ADV_CRC_INIT = 0x555555

# Advertising channels (logical -> physical)
ADV_CHANNEL_MAP = {37: 0, 38: 12, 39: 39}
ADV_CHANNELS = [37, 38, 39]

# PDU Types
class AdvPduType(IntEnum):
    ADV_IND = 0x00
    ADV_DIRECT_IND = 0x01
    ADV_NONCONN_IND = 0x02
    SCAN_REQ = 0x03
    SCAN_RSP = 0x04
    CONNECT_IND = 0x05
    ADV_SCAN_IND = 0x06

class DataPduLlid(IntEnum):
    RESERVED = 0x00
    DATA_CONT = 0x01  # Continuation fragment of L2CAP
    DATA_START = 0x02  # Start of L2CAP or complete PDU
    CONTROL = 0x03     # LL Control PDU

# Packet types for our UDP protocol
PKT_TYPE_TX = 0x01  # Renode -> Python
PKT_TYPE_RX = 0x02  # Python -> Renode

# =============================================================================
# Connection State
# =============================================================================

@dataclass
class ConnectionState:
    """Track state for an active BLE connection."""
    # Connection identifiers
    conn_handle: int = 0
    access_addr: int = 0
    crc_init: int = 0

    # Addresses
    init_addr: bytes = field(default_factory=lambda: b'\x00' * 6)
    init_addr_type: int = 0
    adv_addr: bytes = field(default_factory=lambda: b'\x00' * 6)
    adv_addr_type: int = 0

    # Channel hopping
    channel_map: bytes = field(default_factory=lambda: b'\xff\xff\xff\xff\x1f')  # All 37 data channels
    hop_increment: int = 5
    unmapped_channels: List[int] = field(default_factory=list)

    # Timing
    interval: int = 0x0018  # 30ms (units of 1.25ms)
    latency: int = 0
    timeout: int = 0x00C8  # 2000ms (units of 10ms)
    win_size: int = 1
    win_offset: int = 0

    # Sequence numbers
    tx_sn: int = 0  # Transmit sequence number
    tx_nesn: int = 0  # Transmit next expected sequence number
    rx_sn: int = 0  # Last received SN

    # State
    is_connected: bool = False
    current_channel: int = 0
    event_counter: int = 0

    def __post_init__(self):
        self._build_channel_list()

    def _build_channel_list(self):
        """Build list of used data channels from channel map."""
        self.unmapped_channels = []
        for i in range(37):
            byte_idx = i // 8
            bit_idx = i % 8
            if self.channel_map[byte_idx] & (1 << bit_idx):
                self.unmapped_channels.append(i)
        if not self.unmapped_channels:
            self.unmapped_channels = list(range(37))

    def next_channel(self) -> int:
        """Calculate next data channel using hop algorithm."""
        unmapped = (self.current_channel + self.hop_increment) % 37
        if unmapped in self.unmapped_channels:
            self.current_channel = unmapped
        else:
            # Remap to used channel
            idx = unmapped % len(self.unmapped_channels)
            self.current_channel = self.unmapped_channels[idx]
        self.event_counter += 1
        return self.current_channel

# =============================================================================
# D-Bus BlueZ Advertisement
# =============================================================================

BLUEZ_SERVICE = 'org.bluez'
ADAPTER_IFACE = 'org.bluez.Adapter1'
LE_ADVERTISING_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
LE_ADVERTISEMENT_IFACE = 'org.bluez.LEAdvertisement1'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'


class DBusAdvertisement(dbus.service.Object if DBUS_AVAILABLE else object):
    """D-Bus LE Advertisement object for BlueZ."""

    PATH_BASE = '/org/bluez/renode/advertisement'

    def __init__(self, bus, index: int):
        if not DBUS_AVAILABLE:
            return
        self.path = f'{self.PATH_BASE}{index}'
        self.bus = bus
        self.ad_type = 'peripheral'
        self.service_uuids: List[str] = []
        self.manufacturer_data: Dict[int, Any] = {}
        self.service_data: Dict[str, Any] = {}
        self.local_name: Optional[str] = None
        self.include_tx_power = False
        self._registered = False
        dbus.service.Object.__init__(self, bus, self.path)

    def get_properties(self) -> Dict[str, Any]:
        """Get advertisement properties for D-Bus."""
        properties = {
            'Type': self.ad_type,
        }
        if self.service_uuids:
            properties['ServiceUUIDs'] = dbus.Array(self.service_uuids, signature='s')
        if self.manufacturer_data:
            properties['ManufacturerData'] = dbus.Dictionary(self.manufacturer_data, signature='qv')
        if self.service_data:
            properties['ServiceData'] = dbus.Dictionary(self.service_data, signature='sv')
        if self.local_name:
            properties['LocalName'] = dbus.String(self.local_name)
        if self.include_tx_power:
            properties['Includes'] = dbus.Array(['tx-power'], signature='s')
        return {LE_ADVERTISEMENT_IFACE: properties}

    def get_path(self) -> dbus.ObjectPath:
        return dbus.ObjectPath(self.path)

    def update_from_ad_data(self, ad_data: bytes):
        """Parse BLE advertising data and update properties."""
        self.service_uuids = []
        self.manufacturer_data = {}
        self.service_data = {}
        self.local_name = None

        i = 0
        while i < len(ad_data):
            if i + 1 >= len(ad_data):
                break
            length = ad_data[i]
            if length == 0:
                break
            if i + 1 + length > len(ad_data):
                break
            ad_type = ad_data[i + 1]
            data = ad_data[i + 2:i + 1 + length]
            i += 1 + length

            # Parse common AD types
            if ad_type == 0x01:  # Flags
                pass  # BlueZ handles flags automatically
            elif ad_type == 0x02 or ad_type == 0x03:  # 16-bit Service UUIDs
                for j in range(0, len(data), 2):
                    if j + 2 <= len(data):
                        uuid16 = struct.unpack('<H', data[j:j+2])[0]
                        self.service_uuids.append(f'{uuid16:04x}')
            elif ad_type == 0x06 or ad_type == 0x07:  # 128-bit Service UUIDs
                for j in range(0, len(data), 16):
                    if j + 16 <= len(data):
                        uuid_bytes = data[j:j+16][::-1]  # Reverse for standard format
                        uuid_str = '-'.join([
                            uuid_bytes[0:4].hex(),
                            uuid_bytes[4:6].hex(),
                            uuid_bytes[6:8].hex(),
                            uuid_bytes[8:10].hex(),
                            uuid_bytes[10:16].hex()
                        ])
                        self.service_uuids.append(uuid_str)
            elif ad_type == 0x08 or ad_type == 0x09:  # Local Name
                try:
                    self.local_name = data.decode('utf-8')
                except:
                    pass
            elif ad_type == 0xFF:  # Manufacturer Specific Data
                if len(data) >= 2:
                    company_id = struct.unpack('<H', data[0:2])[0]
                    self.manufacturer_data[company_id] = dbus.Array(data[2:], signature='y')
            elif ad_type == 0x16:  # Service Data - 16 bit UUID
                if len(data) >= 2:
                    uuid16 = struct.unpack('<H', data[0:2])[0]
                    self.service_data[f'{uuid16:04x}'] = dbus.Array(data[2:], signature='y')

    @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
    def GetAll(self, interface):
        if interface != LE_ADVERTISEMENT_IFACE:
            raise dbus.exceptions.DBusException(
                'org.freedesktop.DBus.Error.InvalidArgs',
                f'Unknown interface: {interface}')
        return self.get_properties()[LE_ADVERTISEMENT_IFACE]

    @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature='', out_signature='')
    def Release(self):
        print(f'[DBUS] Advertisement released')


class DBusBLEManager:
    """Manage BLE advertising via D-Bus/BlueZ."""

    def __init__(self, adapter_name: str = 'hci0'):
        if not DBUS_AVAILABLE:
            raise RuntimeError("D-Bus/GLib not available. Install: sudo apt install python3-dbus python3-gi")

        dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
        self.bus = dbus.SystemBus()
        self.adapter_path = f'/org/bluez/{adapter_name}'
        self.advertisement: Optional[DBusAdvertisement] = None
        self._ad_registered = False

        # Get adapter
        try:
            adapter_obj = self.bus.get_object(BLUEZ_SERVICE, self.adapter_path)
            self.adapter_props = dbus.Interface(adapter_obj, DBUS_PROP_IFACE)
            self.ad_manager = dbus.Interface(adapter_obj, LE_ADVERTISING_MANAGER_IFACE)
            print(f"[DBUS] Connected to BlueZ adapter {adapter_name}")
        except dbus.exceptions.DBusException as e:
            raise RuntimeError(f"Failed to connect to BlueZ: {e}")

        # Ensure adapter is powered on
        try:
            powered = self.adapter_props.Get(ADAPTER_IFACE, 'Powered')
            if not powered:
                print("[DBUS] Powering on adapter...")
                self.adapter_props.Set(ADAPTER_IFACE, 'Powered', dbus.Boolean(True))
        except:
            pass

    def set_advertising_data(self, ad_data: bytes):
        """Update advertising data."""
        if self.advertisement is None:
            self.advertisement = DBusAdvertisement(self.bus, 0)

        self.advertisement.update_from_ad_data(ad_data)
        print(f"[DBUS] Updated ad data: uuids={self.advertisement.service_uuids}, "
              f"name={self.advertisement.local_name}, mfg={list(self.advertisement.manufacturer_data.keys())}")

        # Re-register if already registered (to update data)
        if self._ad_registered:
            self._unregister_advertisement()
        self._register_advertisement()

    def _register_advertisement(self):
        """Register advertisement with BlueZ."""
        if self._ad_registered or self.advertisement is None:
            return

        try:
            self.ad_manager.RegisterAdvertisement(
                self.advertisement.get_path(),
                {},
                reply_handler=self._register_ad_cb,
                error_handler=self._register_ad_error_cb
            )
        except dbus.exceptions.DBusException as e:
            print(f"[DBUS] Failed to register advertisement: {e}")

    def _register_ad_cb(self):
        print("[DBUS] Advertisement registered successfully")
        self._ad_registered = True

    def _register_ad_error_cb(self, error):
        print(f"[DBUS] Failed to register advertisement: {error}")
        self._ad_registered = False

    def _unregister_advertisement(self):
        """Unregister current advertisement."""
        if not self._ad_registered or self.advertisement is None:
            return

        try:
            self.ad_manager.UnregisterAdvertisement(self.advertisement.get_path())
            self._ad_registered = False
        except:
            pass

    def stop(self):
        """Stop advertising."""
        self._unregister_advertisement()


# =============================================================================
# BLE Bridge
# =============================================================================

class BLEBridge:
    """Bridge BLE frames between Renode and BlueZ."""

    def __init__(self, renode_rx_port: int = 5001, renode_tx_port: int = 5000,
                 hci_dev: int = 0, dry_run: bool = False, use_dbus: bool = True):
        self.renode_tx_port = renode_tx_port
        self.dry_run = dry_run
        self.hci_dev = hci_dev
        self.use_dbus = use_dbus

        # Renode UDP sockets
        self.renode_rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.renode_rx_sock.bind(('127.0.0.1', renode_rx_port))
        self.renode_rx_sock.setblocking(False)

        self.renode_tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # D-Bus manager (preferred, no root required)
        self.dbus_manager: Optional[DBusBLEManager] = None
        # HCI socket (legacy, requires root)
        self.hci_sock: Optional[socket.socket] = None

        if not dry_run:
            if use_dbus and DBUS_AVAILABLE:
                try:
                    self.dbus_manager = DBusBLEManager(f'hci{hci_dev}')
                    print(f"[INFO] Using D-Bus/BlueZ for advertising (no root required)")
                except Exception as e:
                    print(f"[WARN] Failed to initialize D-Bus: {e}")
                    print("[WARN] Falling back to HCI socket mode")
                    self.use_dbus = False

            if not self.use_dbus or self.dbus_manager is None:
                try:
                    self.hci_sock = self._open_hci_socket(hci_dev)
                    print(f"[INFO] Connected to hci{hci_dev} via raw socket")
                except Exception as e:
                    print(f"[WARN] Failed to open HCI socket: {e}")
                    print("[WARN] Running in dry-run mode (no BlueZ connection)")
                    self.dry_run = True

        # Advertising state
        self.advertising_enabled = False
        self.current_adv_data: Optional[bytes] = None
        self.adv_addr: bytes = b'\x00' * 6

        # Connection tracking: conn_handle -> ConnectionState
        self.connections: Dict[int, ConnectionState] = {}
        # Access address -> conn_handle mapping
        self.access_addr_map: Dict[int, int] = {}

        # GLib main loop for D-Bus (run in separate thread)
        self.glib_loop: Optional[Any] = None
        self.glib_thread: Optional[Any] = None

        print(f"[INFO] BLE Bridge started")
        print(f"[INFO]   Renode RX (from Renode): UDP port {renode_rx_port}")
        print(f"[INFO]   Renode TX (to Renode): UDP port {renode_tx_port}")
        if self.dbus_manager:
            print(f"[INFO]   Mode: D-Bus/BlueZ (user-space)")
        elif self.hci_sock:
            print(f"[INFO]   Mode: Raw HCI socket")
        else:
            print(f"[INFO]   Mode: Dry-run (no Bluetooth)")

    def _open_hci_socket(self, dev_id: int) -> socket.socket:
        """Open raw HCI socket to BlueZ."""
        import ctypes
        import ctypes.util

        AF_BLUETOOTH = 31
        BTPROTO_HCI = 1

        # Try to use libbluetooth's hci_open_dev for reliable socket creation
        libc_name = ctypes.util.find_library('bluetooth')
        if libc_name:
            try:
                libbluetooth = ctypes.CDLL(libc_name)
                libbluetooth.hci_open_dev.argtypes = [ctypes.c_int]
                libbluetooth.hci_open_dev.restype = ctypes.c_int

                fd = libbluetooth.hci_open_dev(dev_id)
                print(f"[DEBUG] hci_open_dev({dev_id}) returned fd={fd}")
                if fd < 0:
                    raise OSError(f"hci_open_dev({dev_id}) failed with {fd}")

                # Use socket.fromfd to properly duplicate the fd
                sock = socket.fromfd(fd, AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)
                # Close original fd since fromfd duplicates it
                import os
                os.close(fd)

                sock.setblocking(False)
                print(f"[DEBUG] Socket created successfully")

                # Set HCI filter
                self._set_hci_filter(sock)
                print(f"[DEBUG] HCI filter set successfully")
                return sock
            except Exception as e:
                import traceback
                print(f"[DEBUG] libbluetooth approach failed: {e}")
                traceback.print_exc()

        # Fallback: manual socket creation
        sock = socket.socket(AF_BLUETOOTH, socket.SOCK_RAW, BTPROTO_HCI)

        # Try different bind formats
        bind_attempts = [
            (dev_id,),           # Python's expected format
            dev_id,              # Just the integer
        ]

        bound = False
        for addr in bind_attempts:
            try:
                sock.bind(addr)
                bound = True
                break
            except (OSError, TypeError) as e:
                continue

        if not bound:
            sock.close()
            raise OSError("Could not bind HCI socket - try installing libbluetooth-dev")

        sock.setblocking(False)
        self._set_hci_filter(sock)
        return sock

    def _set_hci_filter(self, sock: socket.socket):
        """Set HCI filter to receive events and ACL data."""
        SOL_HCI = 0
        HCI_FILTER = 2

        # type_mask: HCI_EVENT_PKT (0x04) | HCI_ACLDATA_PKT (0x02)
        type_mask = (1 << 0x04) | (1 << 0x02)
        # event_mask: LE Meta Event (0x3E) and others
        event_mask_lo = 0xFFFFFFFF
        event_mask_hi = 0xFFFFFFFF
        opcode = 0

        hci_filter = struct.pack('<IIIH', type_mask, event_mask_lo, event_mask_hi, opcode)
        sock.setsockopt(SOL_HCI, HCI_FILTER, hci_filter)

    def run(self):
        """Main event loop."""
        import threading

        # Start GLib main loop in background thread for D-Bus callbacks
        if self.dbus_manager and DBUS_AVAILABLE:
            self.glib_loop = GLib.MainLoop()

            def glib_thread_func():
                try:
                    self.glib_loop.run()
                except:
                    pass

            self.glib_thread = threading.Thread(target=glib_thread_func, daemon=True)
            self.glib_thread.start()
            print("[INFO] D-Bus event loop started")

        sockets = [self.renode_rx_sock]
        if self.hci_sock:
            sockets.append(self.hci_sock)

        print("[INFO] Entering main loop... (Ctrl+C to exit)")

        try:
            while True:
                readable, _, _ = select.select(sockets, [], [], 0.1)

                for sock in readable:
                    if sock == self.renode_rx_sock:
                        self._handle_renode_frame()
                    elif sock == self.hci_sock:
                        self._handle_hci_packet()

                # Process GLib events if using D-Bus
                if self.dbus_manager and DBUS_AVAILABLE:
                    context = GLib.MainContext.default()
                    while context.pending():
                        context.iteration(False)

        except KeyboardInterrupt:
            print("\n[INFO] Shutting down...")
            if self.dbus_manager:
                self.dbus_manager.stop()
            if self.glib_loop:
                self.glib_loop.quit()

    # =========================================================================
    # Renode -> Python handlers
    # =========================================================================

    def _handle_renode_frame(self):
        """Handle frame received from Renode."""
        try:
            data, addr = self.renode_rx_sock.recvfrom(1024)
        except BlockingIOError:
            return

        if len(data) < 4:
            print(f"[WARN] Packet too short: {len(data)} bytes")
            return

        pkt_type, channel, length = struct.unpack('<BBH', data[:4])
        ble_frame = data[4:4+length]

        if pkt_type != PKT_TYPE_TX:
            return

        # Parse BLE frame
        if len(ble_frame) < 6:
            return

        access_addr = struct.unpack('<I', ble_frame[0:4])[0]

        if access_addr == BLE_ADV_ACCESS_ADDR:
            self._handle_adv_frame(channel, ble_frame)
        else:
            self._handle_data_frame(access_addr, channel, ble_frame)

    def _handle_adv_frame(self, channel: int, frame: bytes):
        """Handle advertising channel frame from Renode."""
        pdu_header = frame[4]
        pdu_type = pdu_header & 0x0F
        tx_add = (pdu_header >> 6) & 0x01
        rx_add = (pdu_header >> 7) & 0x01
        pdu_length = frame[5]

        if pdu_length + 6 > len(frame):
            return

        pdu_payload = frame[6:6+pdu_length]

        if pdu_type == AdvPduType.ADV_IND:
            self._handle_adv_ind(channel, tx_add, pdu_payload)
        elif pdu_type == AdvPduType.ADV_NONCONN_IND:
            self._handle_adv_ind(channel, tx_add, pdu_payload)
        elif pdu_type == AdvPduType.ADV_SCAN_IND:
            self._handle_adv_ind(channel, tx_add, pdu_payload)
        elif pdu_type == AdvPduType.SCAN_RSP:
            self._handle_scan_rsp(channel, tx_add, pdu_payload)

    def _handle_adv_ind(self, channel: int, tx_add: int, payload: bytes):
        """Handle ADV_IND/ADV_NONCONN_IND from Renode."""
        if len(payload) < 6:
            return

        adv_addr = payload[0:6]
        ad_data = payload[6:]

        self.adv_addr = adv_addr
        print(f"[RX ADV] ch={channel}, addr={adv_addr.hex()}, ad_len={len(ad_data)}")

        # Update advertising data if changed
        if ad_data != self.current_adv_data:
            self.current_adv_data = ad_data

            # Use D-Bus if available, otherwise fall back to HCI
            if self.dbus_manager:
                self.dbus_manager.set_advertising_data(ad_data)
                self.advertising_enabled = True
            else:
                self._set_hci_advertising_data(ad_data)
                if not self.advertising_enabled:
                    self._set_hci_advertising_params()
                    self._enable_hci_advertising(True)
                    self.advertising_enabled = True

    def _handle_scan_rsp(self, channel: int, tx_add: int, payload: bytes):
        """Handle SCAN_RSP from Renode."""
        if len(payload) < 6:
            return

        adv_addr = payload[0:6]
        scan_rsp_data = payload[6:]

        print(f"[RX SCAN_RSP] ch={channel}, addr={adv_addr.hex()}, len={len(scan_rsp_data)}")
        self._set_hci_scan_response_data(scan_rsp_data)

    def _handle_data_frame(self, access_addr: int, channel: int, frame: bytes):
        """Handle data channel frame from Renode."""
        if access_addr not in self.access_addr_map:
            return

        conn_handle = self.access_addr_map[access_addr]
        conn = self.connections.get(conn_handle)
        if not conn:
            return

        # Parse data PDU header
        # [Access Addr:4][Header:2][Payload:N][CRC:3]
        if len(frame) < 6:
            return

        header = struct.unpack('<H', frame[4:6])[0]
        llid = header & 0x03
        nesn = (header >> 2) & 0x01
        sn = (header >> 3) & 0x01
        md = (header >> 4) & 0x01
        length = (header >> 8) & 0xFF

        payload = frame[6:6+length]

        print(f"[RX DATA] handle=0x{conn_handle:04X}, llid={llid}, sn={sn}, nesn={nesn}, len={length}")

        # Update sequence numbers based on received frame
        # NESN from Renode tells us what it expects next, so update our tx_sn accordingly
        # SN from Renode is what it sent, so we should ACK by updating our tx_nesn
        conn.rx_sn = sn
        conn.tx_nesn = (sn + 1) & 0x01  # ACK by expecting next sequence

        if llid == DataPduLlid.CONTROL:
            self._handle_ll_control(conn, payload)
        elif llid in [DataPduLlid.DATA_START, DataPduLlid.DATA_CONT]:
            self._forward_data_to_hci(conn, llid, payload)

    def _handle_ll_control(self, conn: ConnectionState, payload: bytes):
        """Handle LL Control PDU."""
        if len(payload) < 1:
            return

        opcode = payload[0]
        print(f"  [LL CTRL] opcode=0x{opcode:02X}")

        # Common LL Control opcodes
        LL_CONNECTION_UPDATE_IND = 0x00
        LL_CHANNEL_MAP_IND = 0x01
        LL_TERMINATE_IND = 0x02
        LL_FEATURE_REQ = 0x08
        LL_FEATURE_RSP = 0x09
        LL_VERSION_IND = 0x0C
        LL_LENGTH_REQ = 0x14
        LL_LENGTH_RSP = 0x15

        if opcode == LL_TERMINATE_IND:
            print(f"  [LL CTRL] Connection terminated")
            self._handle_disconnect(conn)

    def _forward_data_to_hci(self, conn: ConnectionState, llid: int, payload: bytes):
        """Forward BLE LL data to HCI ACL."""
        if self.dry_run or not self.hci_sock:
            print(f"  [DRY-RUN] Would forward {len(payload)} bytes to HCI")
            return

        # Build HCI ACL Data packet
        # [pkt_type:1][handle+flags:2][length:2][data:N]

        # Packet boundary flag: 0x02 = first packet (start), 0x01 = continuing
        pb_flag = 0x02 if llid == DataPduLlid.DATA_START else 0x01
        bc_flag = 0x00  # Point-to-point

        handle_flags = (conn.conn_handle & 0x0FFF) | (pb_flag << 12) | (bc_flag << 14)

        hci_pkt = struct.pack('<BHH', 0x02, handle_flags, len(payload)) + payload

        try:
            self.hci_sock.send(hci_pkt)
            print(f"  [HCI TX] ACL data, handle=0x{conn.conn_handle:04X}, len={len(payload)}")
        except Exception as e:
            print(f"  [ERROR] Failed to send HCI ACL: {e}")

    # =========================================================================
    # HCI -> Python handlers
    # =========================================================================

    def _handle_hci_packet(self):
        """Handle HCI packet from BlueZ."""
        if not self.hci_sock:
            return

        try:
            data = self.hci_sock.recv(1024)
        except BlockingIOError:
            return

        if len(data) < 1:
            return

        pkt_type = data[0]

        if pkt_type == 0x04:  # HCI_EVENT_PKT
            self._handle_hci_event(data[1:])
        elif pkt_type == 0x02:  # HCI_ACLDATA_PKT
            self._handle_hci_acl_data(data[1:])

    def _handle_hci_event(self, data: bytes):
        """Handle HCI Event packet."""
        if len(data) < 2:
            return

        event_code = data[0]
        param_len = data[1]
        params = data[2:2+param_len]

        if event_code == 0x3E:  # LE Meta Event
            self._handle_le_meta_event(params)
        elif event_code == 0x05:  # Disconnection Complete
            self._handle_disconnection_complete(params)

    def _handle_le_meta_event(self, params: bytes):
        """Handle LE Meta Event."""
        if len(params) < 1:
            return

        subevent = params[0]

        if subevent == 0x01:  # LE Connection Complete
            self._handle_le_connection_complete(params[1:])
        elif subevent == 0x0A:  # LE Enhanced Connection Complete
            self._handle_le_enhanced_connection_complete(params[1:])

    def _handle_le_connection_complete(self, params: bytes):
        """Handle LE Connection Complete event."""
        if len(params) < 18:
            return

        status = params[0]
        conn_handle = struct.unpack('<H', params[1:3])[0]
        role = params[3]
        peer_addr_type = params[4]
        peer_addr = params[5:11]
        conn_interval = struct.unpack('<H', params[11:13])[0]
        conn_latency = struct.unpack('<H', params[13:15])[0]
        supervision_timeout = struct.unpack('<H', params[15:17])[0]

        print(f"[HCI LE Conn Complete] status={status}, handle=0x{conn_handle:04X}")
        print(f"  role={role}, peer={peer_addr.hex()}")
        print(f"  interval={conn_interval}, latency={conn_latency}, timeout={supervision_timeout}")

        if status != 0:
            return

        # Create connection state
        conn = self._create_connection(conn_handle, peer_addr, peer_addr_type,
                                        conn_interval, conn_latency, supervision_timeout)

        # Generate and send CONNECT_IND to Renode
        self._send_connect_ind_to_renode(conn)

    def _handle_le_enhanced_connection_complete(self, params: bytes):
        """Handle LE Enhanced Connection Complete (for BT 4.2+)."""
        # Similar to regular connection complete, with additional fields
        if len(params) < 30:
            return

        status = params[0]
        conn_handle = struct.unpack('<H', params[1:3])[0]
        role = params[3]
        peer_addr_type = params[4]
        peer_addr = params[5:11]
        # local_resolvable_private_addr = params[11:17]
        # peer_resolvable_private_addr = params[17:23]
        conn_interval = struct.unpack('<H', params[23:25])[0]
        conn_latency = struct.unpack('<H', params[25:27])[0]
        supervision_timeout = struct.unpack('<H', params[27:29])[0]

        print(f"[HCI LE Enhanced Conn Complete] status={status}, handle=0x{conn_handle:04X}")

        if status != 0:
            return

        conn = self._create_connection(conn_handle, peer_addr, peer_addr_type,
                                        conn_interval, conn_latency, supervision_timeout)
        self._send_connect_ind_to_renode(conn)

    def _handle_disconnection_complete(self, params: bytes):
        """Handle Disconnection Complete event."""
        if len(params) < 4:
            return

        status = params[0]
        conn_handle = struct.unpack('<H', params[1:3])[0]
        reason = params[3]

        print(f"[HCI Disconnect] handle=0x{conn_handle:04X}, reason=0x{reason:02X}")

        conn = self.connections.get(conn_handle)
        if conn:
            self._handle_disconnect(conn)

    def _handle_hci_acl_data(self, data: bytes):
        """Handle HCI ACL Data packet from BlueZ."""
        if len(data) < 4:
            return

        handle_flags = struct.unpack('<H', data[0:2])[0]
        length = struct.unpack('<H', data[2:4])[0]
        payload = data[4:4+length]

        conn_handle = handle_flags & 0x0FFF
        pb_flag = (handle_flags >> 12) & 0x03
        bc_flag = (handle_flags >> 14) & 0x03

        print(f"[HCI ACL RX] handle=0x{conn_handle:04X}, pb={pb_flag}, len={length}")

        conn = self.connections.get(conn_handle)
        if not conn:
            print(f"  [WARN] Unknown connection handle")
            return

        # Convert to BLE LL data PDU and send to Renode
        self._send_data_to_renode(conn, pb_flag, payload)

    # =========================================================================
    # Connection management
    # =========================================================================

    def _create_connection(self, conn_handle: int, peer_addr: bytes, peer_addr_type: int,
                           interval: int, latency: int, timeout: int) -> ConnectionState:
        """Create new connection state."""
        # Generate random access address (not advertising address)
        access_addr = self._generate_access_address()

        # Generate random CRC init
        crc_init = random.randint(0, 0xFFFFFF)

        conn = ConnectionState(
            conn_handle=conn_handle,
            access_addr=access_addr,
            crc_init=crc_init,
            init_addr=peer_addr,
            init_addr_type=peer_addr_type,
            adv_addr=self.adv_addr,
            interval=interval,
            latency=latency,
            timeout=timeout,
            hop_increment=random.randint(5, 16),
            is_connected=True,
        )

        self.connections[conn_handle] = conn
        self.access_addr_map[access_addr] = conn_handle

        print(f"[CONN] Created connection: handle=0x{conn_handle:04X}, aa=0x{access_addr:08X}")

        return conn

    def _generate_access_address(self) -> int:
        """Generate valid BLE access address."""
        while True:
            aa = random.randint(0, 0xFFFFFFFF)

            # Must not be advertising access address
            if aa == BLE_ADV_ACCESS_ADDR:
                continue

            # Should have reasonable bit transitions (simplified check)
            # At least 2 transitions in most significant 6 bits
            msb6 = (aa >> 26) & 0x3F
            transitions = bin(msb6 ^ (msb6 >> 1)).count('1')
            if transitions < 2:
                continue

            # Should not have all same bits
            if aa == 0x00000000 or aa == 0xFFFFFFFF:
                continue

            return aa

    def _handle_disconnect(self, conn: ConnectionState):
        """Handle connection disconnection."""
        print(f"[CONN] Disconnected: handle=0x{conn.conn_handle:04X}")

        # Send LL_TERMINATE_IND to Renode
        self._send_ll_terminate_to_renode(conn)

        # Clean up
        if conn.access_addr in self.access_addr_map:
            del self.access_addr_map[conn.access_addr]
        if conn.conn_handle in self.connections:
            del self.connections[conn.conn_handle]

    # =========================================================================
    # Send to Renode
    # =========================================================================

    def _send_to_renode(self, channel: int, frame: bytes):
        """Send BLE frame to Renode via UDP."""
        packet = struct.pack('<BBH', PKT_TYPE_RX, channel, len(frame)) + frame
        self.renode_tx_sock.sendto(packet, ('127.0.0.1', self.renode_tx_port))

    def _send_connect_ind_to_renode(self, conn: ConnectionState):
        """Send CONNECT_IND PDU to Renode."""
        print(f"[TX CONNECT_IND] aa=0x{conn.access_addr:08X}")

        # Build CONNECT_IND PDU
        # PDU payload: InitA (6) + AdvA (6) + LLData (22) = 34 bytes

        # LLData structure:
        # - AA (4 bytes): Access Address
        # - CRCInit (3 bytes)
        # - WinSize (1 byte)
        # - WinOffset (2 bytes)
        # - Interval (2 bytes)
        # - Latency (2 bytes)
        # - Timeout (2 bytes)
        # - ChM (5 bytes): Channel Map
        # - Hop + SCA (1 byte): Hop[4:0] + SCA[7:5]

        ll_data = struct.pack('<I', conn.access_addr)  # AA
        ll_data += struct.pack('<I', conn.crc_init)[:3]  # CRCInit (3 bytes)
        ll_data += struct.pack('<B', conn.win_size)  # WinSize
        ll_data += struct.pack('<H', conn.win_offset)  # WinOffset
        ll_data += struct.pack('<H', conn.interval)  # Interval
        ll_data += struct.pack('<H', conn.latency)  # Latency
        ll_data += struct.pack('<H', conn.timeout)  # Timeout
        ll_data += conn.channel_map  # ChM (5 bytes)
        ll_data += struct.pack('<B', (conn.hop_increment & 0x1F) | (0 << 5))  # Hop + SCA

        # PDU payload = InitA + AdvA + LLData
        pdu_payload = conn.init_addr + conn.adv_addr + ll_data

        # PDU header: type=CONNECT_IND (0x05), TxAdd, RxAdd
        # TxAdd=0 (public initiator), RxAdd based on advertiser
        pdu_header = AdvPduType.CONNECT_IND | (conn.init_addr_type << 6) | (0 << 7)
        pdu_length = len(pdu_payload)

        # Full BLE frame: Access Address (4) + PDU Header (1) + Length (1) + Payload + CRC (3)
        frame = struct.pack('<I', BLE_ADV_ACCESS_ADDR)
        frame += struct.pack('<BB', pdu_header, pdu_length)
        frame += pdu_payload
        frame += b'\x00\x00\x00'  # Placeholder CRC (Renode should recalculate)

        # Send on advertising channel
        self._send_to_renode(37, frame)
        print(f"  [TX] CONNECT_IND sent, {len(frame)} bytes")

    def _send_data_to_renode(self, conn: ConnectionState, pb_flag: int, payload: bytes):
        """Send data PDU to Renode."""
        # Determine LLID from packet boundary flag
        # pb_flag: 0x00 = first non-flushable, 0x01 = continuing, 0x02 = first flushable
        if pb_flag in [0x00, 0x02]:
            llid = DataPduLlid.DATA_START
        else:
            llid = DataPduLlid.DATA_CONT

        # Build data PDU header
        # [LLID:2][NESN:1][SN:1][MD:1][RFU:3][Length:8]
        header = (llid & 0x03)
        header |= (conn.tx_nesn & 0x01) << 2
        header |= (conn.tx_sn & 0x01) << 3
        header |= (0 & 0x01) << 4  # MD = 0
        header |= (len(payload) & 0xFF) << 8

        # Build frame
        frame = struct.pack('<I', conn.access_addr)
        frame += struct.pack('<H', header)
        frame += payload
        frame += b'\x00\x00\x00'  # Placeholder CRC

        # Update sequence numbers
        conn.tx_sn = (conn.tx_sn + 1) & 0x01

        # Advance to next data channel for next packet
        channel = conn.current_channel
        conn.next_channel()

        self._send_to_renode(channel, frame)
        print(f"  [TX DATA] ch={channel}, llid={llid}, len={len(payload)}")

    def _send_ll_terminate_to_renode(self, conn: ConnectionState):
        """Send LL_TERMINATE_IND to Renode."""
        # LL Control PDU with LL_TERMINATE_IND opcode
        payload = bytes([0x02, 0x13])  # opcode=0x02, error=0x13 (remote user terminated)

        header = DataPduLlid.CONTROL
        header |= (conn.tx_nesn & 0x01) << 2
        header |= (conn.tx_sn & 0x01) << 3
        header |= (len(payload) & 0xFF) << 8

        frame = struct.pack('<I', conn.access_addr)
        frame += struct.pack('<H', header)
        frame += payload
        frame += b'\x00\x00\x00'

        self._send_to_renode(conn.current_channel, frame)
        print(f"  [TX LL_TERMINATE_IND]")

    # =========================================================================
    # HCI Commands
    # =========================================================================

    def _send_hci_command(self, ogf: int, ocf: int, params: bytes = b''):
        """Send HCI command."""
        if self.dry_run or not self.hci_sock:
            return

        opcode = (ogf << 10) | ocf
        cmd = struct.pack('<HB', opcode, len(params)) + params
        self.hci_sock.send(b'\x01' + cmd)

    def _set_hci_advertising_data(self, ad_data: bytes):
        """Set advertising data via HCI."""
        if self.dry_run:
            print(f"  [DRY-RUN] Set advertising data: {ad_data.hex()}")
            return

        # Truncate to max 31 bytes and pad
        truncated_len = min(len(ad_data), 31)
        padded = ad_data[:31].ljust(31, b'\x00')
        params = struct.pack('<B', truncated_len) + padded

        self._send_hci_command(0x08, 0x0008, params)
        print(f"  [HCI] Set advertising data ({truncated_len} bytes)")

    def _set_hci_scan_response_data(self, data: bytes):
        """Set scan response data via HCI."""
        if self.dry_run:
            print(f"  [DRY-RUN] Set scan response data: {data.hex()}")
            return

        # Truncate to max 31 bytes and pad
        truncated_len = min(len(data), 31)
        padded = data[:31].ljust(31, b'\x00')
        params = struct.pack('<B', truncated_len) + padded

        self._send_hci_command(0x08, 0x0009, params)
        print(f"  [HCI] Set scan response data ({truncated_len} bytes)")

    def _set_hci_advertising_params(self):
        """Set advertising parameters via HCI."""
        if self.dry_run:
            print(f"  [DRY-RUN] Set advertising parameters")
            return

        params = struct.pack('<HHBBB6sBB',
            0x0100,     # min_interval (160ms)
            0x0100,     # max_interval (160ms)
            0x00,       # ADV_IND (connectable undirected)
            0x00,       # Own address type (public)
            0x00,       # Peer address type
            b'\x00' * 6,  # Peer address
            0x07,       # All advertising channels
            0x00        # Filter policy
        )

        self._send_hci_command(0x08, 0x0006, params)
        print(f"  [HCI] Set advertising parameters")

    def _enable_hci_advertising(self, enable: bool):
        """Enable/disable advertising via HCI."""
        if self.dry_run:
            print(f"  [DRY-RUN] {'Enable' if enable else 'Disable'} advertising")
            return

        params = struct.pack('<B', 0x01 if enable else 0x00)
        self._send_hci_command(0x08, 0x000A, params)
        print(f"  [HCI] {'Enabled' if enable else 'Disabled'} advertising")


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='BLE Bridge: Renode <-> BlueZ')
    parser.add_argument('--renode-rx-port', type=int, default=5001,
                        help='UDP port to receive from Renode (default: 5001)')
    parser.add_argument('--renode-tx-port', type=int, default=5000,
                        help='UDP port to send to Renode (default: 5000)')
    parser.add_argument('--hci', type=int, default=0,
                        help='HCI device number (default: 0 for hci0)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run without BlueZ connection')
    parser.add_argument('--no-dbus', action='store_true',
                        help='Use raw HCI socket instead of D-Bus (requires root)')
    args = parser.parse_args()

    if not DBUS_AVAILABLE and not args.no_dbus and not args.dry_run:
        print("[WARN] D-Bus not available. Install: sudo apt install python3-dbus python3-gi")
        print("[WARN] Falling back to raw HCI mode (requires root)")

    bridge = BLEBridge(
        renode_rx_port=args.renode_rx_port,
        renode_tx_port=args.renode_tx_port,
        hci_dev=args.hci,
        dry_run=args.dry_run,
        use_dbus=not args.no_dbus
    )
    bridge.run()


if __name__ == '__main__':
    main()
