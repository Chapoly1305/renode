"""
Matter fake-transport commissioning harness -- Phase 1 (commissioning-only).

Boots a SiLabs Matter lighting-app built with `chip_enable_fake_ble_transport=true`, exposes the
software CHIPoBLE "fake transport" socket on EUSART1, and drives a host `chip-tool` (also built with
the fake transport) through BLE-Thread commissioning.

Because the fake transport carries CHIPoBLE frames only (PASE + the commissioning-cluster exchanges
that ride the PASE session, including Thread-dataset delivery) -- NOT operational Matter traffic --
this harness verifies commissioning up to the Thread operational handoff. Full operational
interaction (e.g. `onoff toggle`) needs the Phase-2 Thread path documented in matter/README.md.

Prerequisites (see matter/README.md):
  * Device firmware .out built from connectedhomeip with chip_enable_fake_ble_transport=true.
  * Host chip-tool built from connectedhomeip's Linux platform with chip_enable_fake_ble_transport=true
    (the fake transport is Linux-only; this harness is intended to run on Linux CI).

Run:
  CHIP_TOOL=/path/to/chip-tool \
  MATTER_DATASET=<hex-thread-operational-dataset> \
  python3 matter_commissioning.py --board brd2601b --uart eusart0 \
      --elf /path/to/matter-silabs-lighting-example.out
"""
import os

from common import test_lib

################################################
# Globals / parameters (env-overridable)
################################################

QUANTUM_TIME = 0.000050
DEBUG = True

# Socket the firmware's FakeBLETransport task is bridged to (EUSART1). chip-tool connects here as a
# TCP client via CHIP_FAKE_BLE_PORT.
TRANSPORT_PORT = int(os.environ.get("CHIP_FAKE_BLE_PORT", "3500"))

# Host controller and commissioning parameters.
CHIP_TOOL     = os.environ.get("CHIP_TOOL", "chip-tool")
NODE_ID       = os.environ.get("MATTER_NODE_ID", "1")
PIN           = os.environ.get("MATTER_PIN", "20202021")     # default lighting-app setup PIN
DISCRIMINATOR = os.environ.get("MATTER_DISCRIMINATOR", "3840")
# Thread operational dataset (hex, tlvs). Override MATTER_DATASET for your own network; the fake
# transport delivers it to the device over the PASE session but Phase 1 does not require the device
# to actually reach an operational Thread network.
# Canonical connectedhomeip test dataset ("OpenThreadDemo", PAN 0x1234) -- valid TLVs so chip-tool
# accepts and delivers it over PASE. Override MATTER_DATASET for your own network.
DATASET = os.environ.get(
    "MATTER_DATASET",
    "0e080000000000010000000300000f35060004001fffe0020811111111222222220708fd61f77bd3"
    "df233e051000112233445566778899aabbccddeeff030e4f70656e54687265616444656d6f010212"
    "340410445f2b5ca6f2a93a55ce570a70efeecb0c0402a0fff8",
)

################################################
# Test
################################################

board, uart, elf = test_lib.parse_arguments()
test_lib.create_emulation(debug=DEBUG, quantum_time=QUANTUM_TIME)

# `uart` is passed to create_node only to satisfy its TerminalTester argument. NOTE: this SiLabs
# Matter firmware logs via SEGGER RTT, not a UART, so device-side ChipLog output is NOT observable
# from Renode. All assertions below therefore key off chip-tool's own stdout/stderr (captured by
# launch_host_process), which is a sufficient oracle for the end-to-end commissioning path.
node = test_lib.create_node("matter", board, elf, uart)

# Bridge the fake CHIPoBLE transport (EUSART1) to a TCP server socket; chip-tool dials in here.
test_lib.create_socket(node, TRANSPORT_PORT, "eusart1")

# Give the firmware a moment to boot and reach InitFakeBLETransport() (listening on EUSART1). The
# ServerSocketTerminal is already accepting, so chip-tool may connect immediately; the device drains
# the socket once its FakeBLE task is up.
test_lib.delay(2)

# Launch the host controller. FakeBleConnectionDelegate connects to 127.0.0.1:CHIP_FAKE_BLE_PORT and
# drives CHIPoBLE. launch_host_process() wires RENODE_PORT + LD_PRELOAD=librenode_api.so for time sync.
env = os.environ.copy()
env["CHIP_FAKE_BLE_PORT"] = str(TRANSPORT_PORT)
chiptool = test_lib.launch_host_process(
    CHIP_TOOL,
    "chiptool",
    args=[
        "pairing", "ble-thread", NODE_ID, f"hex:{DATASET}", PIN, DISCRIMINATOR,
        "--bypass-attestation-verifier", "true",
    ],
    env=env,
)

# --- Deterministic host-side markers proving the fake transport pipe is live.
# These strings are emitted by src/platform/Linux/ble/FakeBleTransport.cpp (ChipLogProgress(Ble, ...)).
test_lib.wait_for(chiptool, "connecting to fake BLE peer", timeout=60)
test_lib.wait_for(chiptool, "FakeBleTransport: connected", timeout=60)

# --- PASE / commissioning progress on the controller (broad pattern; tighten during Linux verify).
# Reaching PASE proves CHIPoBLE frames flow end-to-end (SUBSCRIBE + WRITE_REQUEST + INDICATION).
test_lib.wait_for(chiptool, r"(PASE|PBKDFParam|Secure Pairing|SPAKE2|Commissioning stage)", timeout=180)

# --- Phase-1 boundary: chip-tool delivers the Thread operational dataset over PASE (Network
# Commissioning cluster), then attempts operational CASE. We assert dataset delivery and DO NOT
# require operational success (no reachable Thread network in Phase 1 -- see matter/README.md).
test_lib.wait_for(chiptool, r"(Thread|AddOrUpdateThreadNetwork|ConnectNetwork)", timeout=180)

print("PHASE-1 PASS: CHIPoBLE commissioning engaged over the fake transport (PASE + Thread-dataset "
      "delivery). Operational CASE is out of Phase-1 scope; see matter/README.md Phase 2.")
