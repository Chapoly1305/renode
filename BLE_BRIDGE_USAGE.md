# BLE Bridge Usage Guide

## Overview

The BLE Bridge connects simulated BLE devices in Renode to real Bluetooth hardware via BlueZ. This enables Matter device commissioning from a real phone to a simulated Matter device.

**Tested Configuration:**
- **Renode Script:** `matter_ble_bridge.resc`
- **Python Bridge:** `ble_bridge.py` (with D-Bus support)
- **Firmware:** `matter/matter-silabs-lighting-example.out` (73MB, Matter lighting app for EFR32MG24)
- **Platform:** Silicon Labs BRD4186C (EFR32MG24 with RAIL BLE stack)

## Prerequisites

```bash
# Python dependencies (for D-Bus mode, no sudo required)
sudo apt install python3-dbus python3-gi

# Verify Bluetooth adapter is available
bluetoothctl list
hciconfig
```

## Quick Start (Recommended)

**Terminal 1** - Start Python bridge:
```bash
cd ~/renode
python3 ble_bridge.py
```

Expected output:
```
[DBUS] Connected to BlueZ adapter hci0
[INFO] Using D-Bus/BlueZ for advertising (no root required)
[INFO] BLE Bridge started
[INFO]   Renode RX (from Renode): UDP port 5001
[INFO]   Renode TX (to Renode): UDP port 5000
[INFO]   Mode: D-Bus/BlueZ (user-space)
[DBUS] D-Bus event loop started
[INFO] Entering main loop... (Ctrl+C to exit)
```

**Terminal 2** - Run Renode:
```bash
cd ~/renode
./renode matter_ble_bridge.resc
# In Renode console, type:
start
```

Expected output:
```
[RX ADV] ch=0, addr=7efa471df7d9, ad_len=15
[DBUS] Updated ad data: uuids=['fff6'], name=None, mfg=[]
[DBUS] Advertisement registered successfully
```

✅ **Success indicators:**
- Python bridge shows `[RX ADV]` messages
- Service UUID `fff6` (Matter pairing service) detected
- Advertisement registered with BlueZ

## Components

| Component | File | Description | Status |
|-----------|------|-------------|--------|
| **Python Bridge** | `ble_bridge.py` | UDP↔BlueZ bridge with D-Bus/HCI support | ✅ Tested |
| **Renode Plugin** | `src/Plugins/BLEBridgePlugin/` | C# plugin for frame capture | ✅ Built |
| **Renode Script** | `matter_ble_bridge.resc` | Matter demo with BLE bridge | ✅ Working |
| **Firmware** | `matter/matter-silabs-lighting-example.out` | Matter lighting app (EFR32MG24) | ✅ Tested |
| **Test Script** | `test_matter.sh` | Automated test script | ✅ Available |

## Python Bridge Modes

### Mode 1: D-Bus/BlueZ (Recommended, No Sudo)

```bash
python3 ble_bridge.py
```

**Advantages:**
- No root privileges required
- Stable, uses official BlueZ D-Bus API
- Handles advertising automatically

**Requirements:**
- `python3-dbus` and `python3-gi` packages
- BlueZ running (`systemctl status bluetooth`)

### Mode 2: Raw HCI Socket (Fallback, Requires Sudo)

```bash
sudo python3 ble_bridge.py --no-dbus
```

**When to use:**
- D-Bus fails for some reason
- Need low-level HCI access
- Connection handling (not yet fully supported in D-Bus mode)

**Note:** May encounter "Invalid argument" errors on some systems.

### Mode 3: Dry-Run (Testing Without Bluetooth)

```bash
python3 ble_bridge.py --dry-run
```

**Use for:**
- Testing Renode side without Bluetooth hardware
- Debugging frame capture
- Verifying Matter firmware is advertising

## Detailed Setup Options

### Option 1: Native Build (For Development)

If you have .NET SDK installed:

```bash
# Install .NET SDK (if not already installed)
wget https://packages.microsoft.com/config/ubuntu/22.04/packages-microsoft-prod.deb -O packages-microsoft-prod.deb
sudo dpkg -i packages-microsoft-prod.deb
sudo apt update
sudo apt install -y dotnet-sdk-8.0

# Build Renode with BLE plugin
cd ~/renode
./build.sh

# Terminal 1: Start Python bridge
python3 ble_bridge.py

# Terminal 2: Run Renode
./renode matter_ble_bridge.resc
# Then type: start
```

### Option 2: Docker Build (For CI/Testing)

```bash
cd ~/renode

# Build in Docker
docker run --rm -v "$(pwd):/renode" -w /renode \
  mcr.microsoft.com/dotnet/sdk:8.0 bash -c '
  git config --global --add safe.directory /renode
  ./build.sh
'

# Terminal 1: Start Python bridge on host
python3 ble_bridge.py

# Terminal 2: Run Renode in Docker
docker run -it --rm \
  -v "$(pwd):/renode" \
  -w /renode \
  --network host \
  mcr.microsoft.com/dotnet/sdk:8.0 \
  dotnet /renode/output/bin/Release/Renode.dll --console --disable-xwt

# In Renode monitor:
include @matter_ble_bridge.resc
start
```

### Option 3: Automated Test

```bash
cd ~/renode
./test_matter.sh
```

This script:
1. Starts `ble_bridge.py --dry-run` in background
2. Runs Renode with Matter firmware
3. Waits 10 seconds for BLE advertisements
4. Captures logs to `/tmp/bridge_matter.log` and `/tmp/renode_matter.log`
5. Cleans up processes

Check results:
```bash
cat /tmp/bridge_matter.log | grep "RX ADV"
```

## UDP Protocol Specification

### Port Configuration

| Port | Direction | Purpose |
|------|-----------|---------|
| **5000** | Bridge → Renode | Inject frames into simulation |
| **5001** | Renode → Bridge | Capture frames from simulation |

### Packet Format

```
[Type:1][Channel:1][Length:2 LE][BLE Frame:N]
```

| Field | Size | Description |
|-------|------|-------------|
| Type | 1 byte | 0x01=TX (Renode→Bridge), 0x02=RX (Bridge→Renode) |
| Channel | 1 byte | BLE channel (0-39, adv channels: 0,12,39) |
| Length | 2 bytes LE | Length of BLE frame in bytes |
| BLE Frame | N bytes | Raw BLE Link Layer frame |

### BLE Frame Structure

```
[Access Addr:4][PDU Header:1][Length:1][Payload:N][CRC:3]
```

**Advertising Access Address:** `0x8E89BED6`

**Matter Service UUID:** `0xFFF6`

## Firmware Details

The Matter lighting example implements:
- **Platform:** EFR32MG24 (ARM Cortex-M33, 1.5MB Flash, 256KB RAM)
- **Radio:** RAIL 2.4GHz (BLE + Thread)
- **BLE:** Advertises Matter commissioning service (UUID 0xFFF6)
- **Thread:** For operational communication after commissioning
- **Application:** Simple on/off light with Matter over Thread

Build your own:
```bash
# Clone Matter SDK
git clone https://github.com/project-chip/connectedhomeip.git
cd connectedhomeip
scripts/checkout_submodules.py --shallow --platform silabs

# Build lighting app
scripts/examples/gn_efr32_example.sh lighting-app brd4186c
```

## Troubleshooting

### D-Bus Connection Failed

```bash
# Check if BlueZ is running
systemctl status bluetooth

# Start BlueZ if stopped
sudo systemctl start bluetooth

# Check adapter is powered
bluetoothctl
[bluetooth]# power on
```

### "Address already in use" Error

```bash
# Kill existing bridge
pkill -f ble_bridge.py

# Check ports
netstat -ulnp | grep -E "5000|5001"
```

### No BLE Advertisements Seen

The Matter firmware needs time to initialize:

```bash
# In Renode console
start
# Wait 10-15 seconds of real time for virtual firmware to boot and start advertising
```

If still no output:
```bash
# Check if frames are being sent from Renode
tail -f /tmp/renode_matter.log | grep "TX frame"

# Check Python bridge is receiving
tail -f /tmp/bridge_matter.log | grep "RX ADV"
```

### HCI Socket "Invalid Argument" Error

This means raw HCI socket binding failed. Use D-Bus mode instead:

```bash
# Install D-Bus dependencies
sudo apt install python3-dbus python3-gi

# Run without --no-dbus flag
python3 ble_bridge.py
```

### BlueZ Adapter Not Found

```bash
# List adapters
bluetoothctl list

# If no adapter, check USB Bluetooth dongles
lsusb | grep -i bluetooth

# Try different HCI device
python3 ble_bridge.py --hci 1
```

## Testing Connection Handling (Work in Progress)

Current status:
- ✅ Advertising: Working
- ✅ CONNECT_IND generation: Implemented
- ⚠️ Data channel: Implemented but not fully tested
- ⚠️ GATT operations: Requires full bidirectional bridge
- ❌ Pairing/SMP: Not yet implemented

To test full connection with a phone:
1. Ensure Matter controller app (Google Home, Apple Home, chip-tool)
2. Start bridge: `python3 ble_bridge.py --no-dbus` (raw HCI needed for connections)
3. Start Renode and Matter firmware
4. Use controller app to commission the device
5. Watch logs for CONNECT_IND and data exchanges

## Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                    Matter Controller App                     │
│                   (Phone / chip-tool)                        │
└───────────────────────────┬─────────────────────────────────┘
                            │ BLE
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                      Host BlueZ Stack                        │
│                    (hci0 Bluetooth Adapter)                  │
└───────────────────────────┬─────────────────────────────────┘
                            │ D-Bus API / HCI Socket
                            ▼
┌─────────────────────────────────────────────────────────────┐
│                    ble_bridge.py (Python)                    │
│  ┌──────────────────┐              ┌──────────────────────┐ │
│  │ D-Bus Manager    │              │ HCI Socket Handler   │ │
│  │ (user-space)     │              │ (requires root)      │ │
│  └────────┬─────────┘              └─────────┬────────────┘ │
│           └──────────────┬───────────────────┘              │
│                          │ UDP :5000/:5001                  │
└──────────────────────────┼──────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│              BLEBridgeServer.cs (Renode Plugin)             │
│                                                              │
│  ┌──────────────┐    ┌─────────────────┐    ┌───────────┐  │
│  │ UDP Receiver │───▶│ ReceiveFrame()  │───▶│ EFR32     │  │
│  └──────────────┘    └─────────────────┘    │ Radio     │  │
│  ┌──────────────┐    ┌─────────────────┐    │ (RAIL)    │  │
│  │ UDP Sender   │◀───│ FrameProcessed  │◀───│           │  │
│  └──────────────┘    └─────────────────┘    └───────────┘  │
└─────────────────────────────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────┐
│           Matter Firmware (matter-silabs-lighting-example)  │
│                                                              │
│  ┌────────────┐  ┌────────────┐  ┌─────────────────────┐   │
│  │ Matter     │  │ Thread     │  │ BLE (Commissioning) │   │
│  │ Application│  │ Network    │  │ Service UUID: 0xFFF6│   │
│  └────────────┘  └────────────┘  └─────────────────────┘   │
└─────────────────────────────────────────────────────────────┘
```

## Files Reference

| File | Purpose |
|------|---------|
| `ble_bridge.py` | Main Python bridge with D-Bus + HCI support |
| `matter_ble_bridge.resc` | Renode startup script for Matter demo |
| `test_matter.sh` | Automated test script |
| `matter/matter-silabs-lighting-example.out` | Matter firmware (EFR32MG24) |
| `src/Plugins/BLEBridgePlugin/BLEBridgeServer.cs` | C# UDP bridge server |
| `src/Plugins/BLEBridgePlugin/EmulationExtensions.cs` | Renode monitor commands |
| `src/Plugins/BLEBridgePlugin/BLEBridgePlugin.cs` | Plugin entry point |

## Next Steps

1. **Test on real hardware:** Run with physical Bluetooth adapter and Matter controller app
2. **Implement GATT server:** Add D-Bus GATT service for full connection support
3. **Add pairing support:** Implement SMP (Security Manager Protocol)
4. **Thread integration:** Bridge Thread frames for full Matter commissioning

## References

- [Matter Specification](https://csa-iot.org/developer-resource/specifications-download-request/)
- [BlueZ D-Bus API](https://github.com/bluez/bluez/blob/master/doc/)
- [Renode Documentation](https://renode.readthedocs.io/)
- [Silicon Labs Matter Examples](https://github.com/SiliconLabs/matter)
