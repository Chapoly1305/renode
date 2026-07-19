# connectedhomeip fake CHIPoBLE transport — patch & modifications

This directory preserves the **connectedhomeip source changes** the Renode Matter commissioning
feature depends on. In the working checkout (`/Volumes/Tools/connectedhomeip`) these live as
**uncommitted WIP**, so they are captured here as an applyable patch to avoid losing them and to
document exactly what changed.

## `connectedhomeip-fake-ble-transport.patch`

The complete fake-transport feature (device + host), applyable to a matching connectedhomeip checkout:

```bash
cd <connectedhomeip>
git apply matter/patches/connectedhomeip-fake-ble-transport.patch   # baseline: see header in the patch
```

### Files it changes

| File | Kind | Purpose |
| --- | --- | --- |
| `src/platform/silabs/efr32/FakeBLETransport.cpp` | new | **Device** side: a FreeRTOS task that drives `BLEManagerImpl`'s CHIPoBLE state machine from framed bytes on **EUSART1** instead of the real `sl_bt` GATT stack. Frame = `[1B type][2B len LE][payload]`. |
| `src/platform/silabs/efr32/BLEManagerImpl.cpp` | modified | Calls `InitFakeBLETransport()` in `HandleBootEvent()` and routes `SendIndication`→`SendFakeIndication` under the build flag. |
| `src/platform/silabs/BLEManagerImpl.h` | modified | Declares the fake-transport entry points. |
| `src/platform/silabs/efr32/BUILD.gn` | modified | Adds `declare_args() { chip_enable_fake_ble_transport = false }` → defines `CHIP_DEVICE_CONFIG_ENABLE_FAKE_BLE_TRANSPORT=1`. |
| `src/platform/Linux/ble/FakeBleTransport.{cpp,h}` | new | **Host** side (chip-tool): a `BleConnectionDelegate`/`BlePlatformDelegate` that TCP-connects to `127.0.0.1:$CHIP_FAKE_BLE_PORT` and speaks the same frame protocol. |

> Note: `src/platform/Linux/BUILD.gn` already carries the `chip_enable_fake_ble_transport` arg upstream,
> so it is not part of this patch.

## Build recipe (recap)

```bash
# Device firmware (silabs):
./scripts/examples/gn_silabs_example.sh examples/lighting-app/silabs \
    out/lighting-fake-ble BRD2601B chip_enable_fake_ble_transport=true

# Host chip-tool (Linux): first fetch host submodules, then build.
./scripts/checkout_submodules.py --shallow --platform linux --recursive
./scripts/examples/gn_build_example.sh examples/chip-tool out/chip-tool-fake-ble \
    chip_enable_fake_ble_transport=true
# In a 7.7 GB Docker/Rosetta container the big command files OOM at full parallelism — finish with `ninja -j2`.
```

## Modifications made during Renode/Docker bring-up (2026-07-18)

Two fixes to the **host** file `src/platform/Linux/ble/FakeBleTransport.cpp` were required to get
chip-tool to build and to talk to the emulated device. Both are included in the patch above.

### 1. Compile fix — `BLE_CONNECTION_OBJECT` vs `void*`

On the Linux platform `BLE_CONNECTION_OBJECT` is a typed pointer (`BluezConnection*`), so the fixed
fake connection object cannot be `void*` (no implicit conversion at the `BleLayer` call sites; the
build fails with `invalid conversion from 'void*' to 'BluezConnection*'`).

```diff
-// The fake transport only ever talks to a single peer; use a fixed non-null connection object.
-void * const kFakeConnObj = reinterpret_cast<void *>(0x1);
+// The fake transport only ever talks to a single peer; use a fixed non-null connection object.
+// BLE_CONNECTION_OBJECT is a typed pointer (BluezConnection*) on the Linux platform, so use that
+// type directly rather than void* (which does not implicitly convert at the BleLayer call sites).
+BLE_CONNECTION_OBJECT const kFakeConnObj = reinterpret_cast<BLE_CONNECTION_OBJECT>(0x1);
```

### 2. Handshake fix — subscribe right after connect

The device gates its outgoing indications (including the **BTP handshake response**) on an active
C2 subscription. Over the fake pipe there is no GATT/CCCD layer, and the central's subscribe was not
reaching the device before the handshake WRITE, so the device stayed silent and chip-tool reported
`connect handshake timed out` (Ble Error 0x415). Fix: send a `kSubscribe` frame immediately after
`kConnect` in `FakeBleConnectionDelegate::NewConnection`.

```diff
     if (!SendFrame(FrameType::kConnect, nullptr, 0))
     {
         ChipLogError(Ble, "FakeBleTransport: failed to send CONNECT frame");
         BleConnectionDelegate::OnConnectionError(appState, CHIP_ERROR_INTERNAL);
         return;
     }
+
+    // The device gates its outgoing indications (including the BTP handshake response) on an active
+    // C2 subscription. In real CHIPoBLE the central enables C2 indications during connection setup;
+    // over the fake pipe there is no GATT/CCCD layer, so proactively announce the subscription right
+    // after connecting -- before the BTP handshake WRITE -- so the device can indicate back.
+    if (!SendFrame(FrameType::kSubscribe, nullptr, 0))
+    {
+        ChipLogError(Ble, "FakeBleTransport: failed to send SUBSCRIBE frame");
+        BleConnectionDelegate::OnConnectionError(appState, CHIP_ERROR_INTERNAL);
+        return;
+    }
```

## Status

With both fixes, chip-tool builds and the pipeline is proven end-to-end: real chip-tool ↔ relay ↔
Renode ↔ firmware, and the device **receives** CONNECT + SUBSCRIBE + the BTP handshake WRITE. A
**device-side** blocker remains — the firmware does not transmit the BTP handshake response (zero
EUSART1 TX), so PASE never starts. Suspected cause: a radio power-manager busy-wait at boot
(`radio` writes to `MCUEM1PMODE`, an unmodeled EM1P ack) starving the CHIP event-loop task. Device
logs go to SEGGER RTT (not visible in Renode); the next step is device-side log visibility or
modelling the radio EM1P ack. See `../README.md` and the project memory for details.
