#!/usr/bin/env python3
"""
BLE Bridge: Renode <-> BlueZ (D-Bus version)

This script bridges BLE frames between Renode simulation and host BlueZ
using the D-Bus API (no root required).

Protocol (UDP):
  [Type:1][Channel:1][Len:2 LE][Data:N]
  Type: 0x01 = TX (Renode -> Python), 0x02 = RX (Python -> Renode)

Usage:
  python3 ble_bridge_dbus.py [--renode-rx-port 5001] [--renode-tx-port 5000]

Requirements:
  pip install dbus-python PyGObject
"""

import argparse
import socket
import struct
import threading
import time
from typing import Optional, Dict, List

# D-Bus imports
try:
    import dbus
    import dbus.service
    import dbus.mainloop.glib
    from gi.repository import GLib
    DBUS_AVAILABLE = True
except ImportError:
    DBUS_AVAILABLE = False
    print("[WARN] dbus-python or PyGObject not installed")
    print("[WARN] Install with: pip install dbus-python PyGObject")

# =============================================================================
# BLE Constants
# =============================================================================

BLE_ADV_ACCESS_ADDR = 0x8E89BED6
BLUEZ_SERVICE = 'org.bluez'
ADAPTER_IFACE = 'org.bluez.Adapter1'
LE_ADVERTISING_MANAGER_IFACE = 'org.bluez.LEAdvertisingManager1'
LE_ADVERTISEMENT_IFACE = 'org.bluez.LEAdvertisement1'
GATT_MANAGER_IFACE = 'org.bluez.GattManager1'
GATT_SERVICE_IFACE = 'org.bluez.GattService1'
GATT_CHRC_IFACE = 'org.bluez.GattCharacteristic1'
DBUS_OM_IFACE = 'org.freedesktop.DBus.ObjectManager'
DBUS_PROP_IFACE = 'org.freedesktop.DBus.Properties'

# Packet types for UDP protocol
PKT_TYPE_TX = 0x01  # Renode -> Python
PKT_TYPE_RX = 0x02  # Python -> Renode

# Matter BLE constants
MATTER_SERVICE_UUID = '0000fff6-0000-1000-8000-00805f9b34fb'


# =============================================================================
# D-Bus Advertisement
# =============================================================================

if DBUS_AVAILABLE:
    class Advertisement(dbus.service.Object):
        """BlueZ LE Advertisement via D-Bus."""

        PATH_BASE = '/org/bluez/renode/advertisement'

        def __init__(self, bus, index, ad_data: bytes = b''):
            self.path = f'{self.PATH_BASE}{index}'
            self.bus = bus
            self.ad_type = 'peripheral'
            self.local_name = 'Matter Device'
            self.service_uuids = [MATTER_SERVICE_UUID]
            self.manufacturer_data = {}
            self.service_data = {}
            self.include_tx_power = False
            self._parse_ad_data(ad_data)

            dbus.service.Object.__init__(self, bus, self.path)

        def _parse_ad_data(self, ad_data: bytes):
            """Parse advertising data into D-Bus format."""
            i = 0
            while i < len(ad_data):
                if i + 1 >= len(ad_data):
                    break
                length = ad_data[i]
                if length == 0 or i + length > len(ad_data):
                    break
                ad_type = ad_data[i + 1]
                data = ad_data[i + 2:i + 1 + length]

                # Parse common AD types
                if ad_type == 0x01:  # Flags
                    pass  # BlueZ handles flags
                elif ad_type == 0x09:  # Complete Local Name
                    self.local_name = data.decode('utf-8', errors='ignore')
                elif ad_type == 0x16:  # Service Data - 16 bit UUID
                    if len(data) >= 2:
                        uuid = f'{data[1]:02x}{data[0]:02x}'
                        self.service_data[uuid] = dbus.Array(data[2:], signature='y')
                elif ad_type == 0xFF:  # Manufacturer Specific Data
                    if len(data) >= 2:
                        company_id = data[0] | (data[1] << 8)
                        self.manufacturer_data[company_id] = dbus.Array(data[2:], signature='y')

                i += 1 + length

        def get_properties(self):
            properties = {
                LE_ADVERTISEMENT_IFACE: {
                    'Type': self.ad_type,
                    'LocalName': self.local_name,
                    'ServiceUUIDs': dbus.Array(self.service_uuids, signature='s'),
                    'IncludeTxPower': dbus.Boolean(self.include_tx_power),
                }
            }
            if self.manufacturer_data:
                properties[LE_ADVERTISEMENT_IFACE]['ManufacturerData'] = dbus.Dictionary(
                    self.manufacturer_data, signature='qv')
            if self.service_data:
                properties[LE_ADVERTISEMENT_IFACE]['ServiceData'] = dbus.Dictionary(
                    self.service_data, signature='sv')
            return properties

        def get_path(self):
            return dbus.ObjectPath(self.path)

        @dbus.service.method(DBUS_PROP_IFACE, in_signature='s', out_signature='a{sv}')
        def GetAll(self, interface):
            if interface != LE_ADVERTISEMENT_IFACE:
                raise dbus.exceptions.DBusException(
                    'org.freedesktop.DBus.Error.InvalidArgs',
                    f'Unknown interface: {interface}')
            return self.get_properties()[LE_ADVERTISEMENT_IFACE]

        @dbus.service.method(LE_ADVERTISEMENT_IFACE, in_signature='', out_signature='')
        def Release(self):
            print('[ADV] Advertisement released')


# =============================================================================
# BLE Bridge
# =============================================================================

class BLEBridgeDBus:
    """Bridge BLE frames between Renode and BlueZ via D-Bus."""

    def __init__(self, renode_rx_port: int = 5001, renode_tx_port: int = 5000,
                 adapter: str = 'hci0', dry_run: bool = False):
        self.renode_tx_port = renode_tx_port
        self.dry_run = dry_run or not DBUS_AVAILABLE
        self.adapter_path = f'/org/bluez/{adapter}'

        # Renode UDP sockets
        self.renode_rx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.renode_rx_sock.bind(('127.0.0.1', renode_rx_port))
        self.renode_rx_sock.setblocking(False)
        self.renode_tx_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)

        # D-Bus setup
        self.bus = None
        self.mainloop = None
        self.advertisement = None
        self.ad_manager = None

        if not self.dry_run:
            try:
                dbus.mainloop.glib.DBusGMainLoop(set_as_default=True)
                self.bus = dbus.SystemBus()
                self._setup_bluez()
            except Exception as e:
                print(f'[WARN] Failed to setup D-Bus: {e}')
                self.dry_run = True

        # State
        self.current_ad_data: Optional[bytes] = None
        self.adv_addr: bytes = b'\x00' * 6
        self.running = False

        print(f'[INFO] BLE Bridge (D-Bus) started')
        print(f'[INFO]   Renode RX: UDP port {renode_rx_port}')
        print(f'[INFO]   Renode TX: UDP port {renode_tx_port}')
        if self.dry_run:
            print(f'[INFO]   Mode: dry-run (no BlueZ)')

    def _setup_bluez(self):
        """Setup BlueZ D-Bus interfaces."""
        adapter_obj = self.bus.get_object(BLUEZ_SERVICE, self.adapter_path)
        self.ad_manager = dbus.Interface(adapter_obj, LE_ADVERTISING_MANAGER_IFACE)
        print(f'[INFO] Connected to BlueZ adapter: {self.adapter_path}')

    def run(self):
        """Main event loop."""
        self.running = True

        # Start UDP receiver thread
        udp_thread = threading.Thread(target=self._udp_loop, daemon=True)
        udp_thread.start()

        if not self.dry_run and self.bus:
            # Run GLib main loop for D-Bus
            self.mainloop = GLib.MainLoop()
            try:
                print('[INFO] Running D-Bus main loop... (Ctrl+C to exit)')
                self.mainloop.run()
            except KeyboardInterrupt:
                print('\n[INFO] Shutting down...')
        else:
            # Dry-run mode - just process UDP
            print('[INFO] Running in dry-run mode... (Ctrl+C to exit)')
            try:
                while self.running:
                    time.sleep(0.1)
            except KeyboardInterrupt:
                print('\n[INFO] Shutting down...')

        self.running = False

    def _udp_loop(self):
        """UDP receive loop (runs in thread)."""
        while self.running:
            try:
                self.renode_rx_sock.settimeout(0.1)
                data, addr = self.renode_rx_sock.recvfrom(1024)
                self._handle_renode_frame(data)
            except socket.timeout:
                continue
            except Exception as e:
                if self.running:
                    print(f'[ERROR] UDP receive: {e}')

    def _handle_renode_frame(self, data: bytes):
        """Handle frame received from Renode."""
        if len(data) < 4:
            return

        pkt_type, channel, length = struct.unpack('<BBH', data[:4])
        ble_frame = data[4:4+length]

        if pkt_type != PKT_TYPE_TX:
            return

        if len(ble_frame) < 6:
            return

        access_addr = struct.unpack('<I', ble_frame[0:4])[0]

        if access_addr == BLE_ADV_ACCESS_ADDR:
            self._handle_adv_frame(channel, ble_frame)

    def _handle_adv_frame(self, channel: int, frame: bytes):
        """Handle advertising frame from Renode."""
        pdu_header = frame[4]
        pdu_type = pdu_header & 0x0F
        pdu_length = frame[5]

        if pdu_length + 6 > len(frame):
            return

        pdu_payload = frame[6:6+pdu_length]

        # ADV_IND or similar
        if pdu_type in [0x00, 0x02, 0x06]:
            if len(pdu_payload) < 6:
                return

            adv_addr = pdu_payload[0:6]
            ad_data = pdu_payload[6:]

            self.adv_addr = adv_addr
            print(f'[RX ADV] ch={channel}, addr={adv_addr.hex()}, ad_len={len(ad_data)}')

            if ad_data != self.current_ad_data:
                self.current_ad_data = ad_data
                # Schedule update on GLib main loop for thread safety
                if not self.dry_run and DBUS_AVAILABLE:
                    GLib.idle_add(self._update_advertisement, ad_data)
                else:
                    self._update_advertisement(ad_data)

    def _update_advertisement(self, ad_data: bytes):
        """Update BlueZ advertisement with new data. Must be called from GLib main loop."""
        if self.dry_run:
            print(f'  [DRY-RUN] Would advertise: {ad_data.hex()}')
            return False  # Return False for GLib.idle_add

        try:
            # Remove old advertisement
            if self.advertisement:
                try:
                    self.ad_manager.UnregisterAdvertisement(self.advertisement.get_path())
                except dbus.exceptions.DBusException as e:
                    # Advertisement may already be unregistered
                    print(f'  [ADV] Unregister warning: {e}')
                try:
                    self.advertisement.remove_from_connection()
                except Exception:
                    pass

            # Create new advertisement
            self.advertisement = Advertisement(self.bus, 0, ad_data)

            # Register with BlueZ
            self.ad_manager.RegisterAdvertisement(
                self.advertisement.get_path(),
                {},
                reply_handler=self._register_ad_cb,
                error_handler=self._register_ad_error_cb
            )

        except Exception as e:
            print(f'  [ERROR] Failed to update advertisement: {e}')

        return False  # Return False for GLib.idle_add (don't repeat)

    def _register_ad_cb(self):
        print('  [ADV] Advertisement registered')

    def _register_ad_error_cb(self, error):
        print(f'  [ADV] Failed to register: {error}')

    def send_to_renode(self, channel: int, frame: bytes):
        """Send BLE frame to Renode via UDP."""
        packet = struct.pack('<BBH', PKT_TYPE_RX, channel, len(frame)) + frame
        self.renode_tx_sock.sendto(packet, ('127.0.0.1', self.renode_tx_port))


# =============================================================================
# Main
# =============================================================================

def main():
    parser = argparse.ArgumentParser(description='BLE Bridge: Renode <-> BlueZ (D-Bus)')
    parser.add_argument('--renode-rx-port', type=int, default=5001,
                        help='UDP port to receive from Renode (default: 5001)')
    parser.add_argument('--renode-tx-port', type=int, default=5000,
                        help='UDP port to send to Renode (default: 5000)')
    parser.add_argument('--adapter', type=str, default='hci0',
                        help='Bluetooth adapter (default: hci0)')
    parser.add_argument('--dry-run', action='store_true',
                        help='Run without BlueZ connection')
    args = parser.parse_args()

    bridge = BLEBridgeDBus(
        renode_rx_port=args.renode_rx_port,
        renode_tx_port=args.renode_tx_port,
        adapter=args.adapter,
        dry_run=args.dry_run
    )
    bridge.run()


if __name__ == '__main__':
    main()
