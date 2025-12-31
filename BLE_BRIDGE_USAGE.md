# BLE Bridge Usage Guide

## Overview

The BLE Bridge connects simulated BLE devices in Renode to real Bluetooth hardware via BlueZ. This enables Matter device commissioning from a real phone to a simulated Matter device.

## Prerequisites

```bash
# Python dependencies (for D-Bus mode, no sudo required)
sudo apt install python3-dbus python3-gi

# Verify Bluetooth adapter is available
hciconfig
```

## Components

| Component | Description | Status |
|-----------|-------------|--------|
| `ble_bridge.py` | Python bridge with D-Bus/BlueZ integration | Tested, working |
| `BLEBridgePlugin` | C# Renode plugin for frame capture | Built, working |
| `matter_ble_bridge.resc` | Renode script for Matter demo | Ready |

## Option 1: Docker Build (Recommended)

Build Renode with the BLE plugin using Docker, then run interactively.

### Build once:

```bash
cd ~/renode

# Clean any previous build artifacts
rm -rf output/ src/Renode/bin/ src/Renode/obj/

# Build in Docker
docker run --rm -v "$(pwd):/renode" -w /renode \
  mcr.microsoft.com/dotnet/sdk:8.0 bash -c '
  git config --global --add safe.directory /renode
  ./build.sh
'
```

### Run the bridge:

**Terminal 1** - Start Python bridge:
```bash
python3 ble_bridge.py
```

**Terminal 2** - Run Renode in Docker:
```bash
docker run -it --rm -v "$(pwd):/renode" -w /renode --network host \
  mcr.microsoft.com/dotnet/sdk:8.0 \
  dotnet /renode/output/bin/Release/Renode.dll --console --disable-xwt
```

**In Renode monitor:**
```
include @matter_ble_bridge.resc
start
```

## Option 2: Native Build

If you have .NET SDK installed locally:

```bash
# Install .NET SDK
sudo apt install dotnet-sdk-8.0

# Build Renode with plugin
cd ~/renode
./build.sh

# Terminal 1: Start Python bridge
python3 ble_bridge.py

# Terminal 2: Run Renode
./renode matter_ble_bridge.resc
# Then type: start
```

## Option 3: Portable Renode (Wireshark Logging)

The portable Renode cannot load custom plugins, but you can capture BLE traffic to a pcap file:

```bash
# Run with Wireshark logging
./renode_1.16.0+*/renode matter_ble_wireshark_bridge.resc
start
```

BLE traffic is logged to `/tmp/renode_ble.pcap`. Open with Wireshark to analyze.

## Option 4: Test Python Bridge Standalone

Test the D-Bus bridge without Renode:

```bash
# Start the bridge
python3 ble_bridge.py &

# Send a test BLE advertising packet
python3 -c "
import socket, struct
sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
# BLE ADV_IND frame with 'MatterTest' name
access_addr = struct.pack('<I', 0x8E89BED6)
adv_addr = bytes([0x11, 0x22, 0x33, 0x44, 0x55, 0x66])
ad_data = bytes([0x02, 0x01, 0x06, 0x0B, 0x09]) + b'MatterTest'
pdu_payload = adv_addr + ad_data
ble_frame = access_addr + bytes([0x00, len(pdu_payload)]) + pdu_payload + bytes([0, 0, 0])
packet = struct.pack('<BBH', 0x01, 37, len(ble_frame)) + ble_frame
sock.sendto(packet, ('127.0.0.1', 5001))
print('Sent!')
"
```

Expected output:
```
[RX ADV] ch=37, addr=112233445566, ad_len=15
[DBUS] Updated ad data: uuids=[], name=MatterTest, mfg=[]
[DBUS] Advertisement registered successfully
```

## UDP Protocol

The bridge communicates with Renode via UDP:

| Port | Direction | Description |
|------|-----------|-------------|
| 5000 | Bridge RX | Receives frames from Python bridge |
| 5001 | Bridge TX | Sends frames to Python bridge |

**Packet format:** `[Type:1][Channel:1][Length:2 LE][BLE Frame:N]`

| Type | Meaning |
|------|---------|
| 0x01 | TX (Renode -> Bridge) |
| 0x02 | RX (Bridge -> Renode) |

## Python Bridge Modes

```bash
# D-Bus mode (default, no sudo required)
python3 ble_bridge.py

# Raw HCI mode (requires sudo, may have compatibility issues)
sudo python3 ble_bridge.py --no-dbus

# Dry-run mode (no Bluetooth, for testing)
python3 ble_bridge.py --dry-run
```

## Troubleshooting

### "Address already in use" error
```bash
pkill -f ble_bridge.py
```

### D-Bus not available
```bash
sudo apt install python3-dbus python3-gi
```

### No BLE frames captured
The Matter firmware needs several seconds of virtual time to initialize and start BLE advertising. Run the simulation longer:
```
start
# Wait 10+ seconds of real time
```

## Files

| File | Description |
|------|-------------|
| `ble_bridge.py` | Main Python bridge script |
| `src/Plugins/BLEBridgePlugin/` | C# Renode plugin source |
| `matter_ble_bridge.resc` | Renode script with BLE bridge |
| `matter_ble_wireshark_bridge.resc` | Renode script with pcap logging |
