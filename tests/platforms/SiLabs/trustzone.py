from common import test_lib
from pyrenode3.wrappers import TerminalTester

################################################
# Globals
################################################

QUANTUM_TIME = 0.000050
DEBUG = True
TESTER_TIMEOUT = 10

################################################
# Utility functions
################################################

def vse_init(mach_name):
    """
    Initialize VSE (Vault Security Engine) mailboxes.
    
    This function sets up input and output mailboxes for the VSE by writing
    configuration data to specific memory locations and registers.
    
    Args:
        monitor: Renode Monitor instance to execute commands
    """
    monitor = test_lib.monitor()
    monitor.execute("mach set \"{mach_name}\"")

    # INPUT MAILBOX
    # Write SYSCFG->ROOTDATA0 with the input mailbox RAM (FRCRAM) address
    monitor.execute("sysbus.syscfg WriteDoubleWord 0x600 0xA0004C00")
    # Magic number
    monitor.execute("sysbus WriteDoubleWord 0xA0004C00 0xE5ECC0DE")
    # Version
    monitor.execute("sysbus WriteDoubleWord 0xA0004C04 0x0001020E")
    # Status (root key present)
    monitor.execute("sysbus WriteDoubleWord 0xA0004C08 0x00400000")
    # Checksum
    monitor.execute("sysbus WriteDoubleWord 0xA0004C14 0xE5ADC2D0")
    
    # OUTPUT MAILBOX
    # Write SYSCFG->ROOTDATA1 with the output mailbox RAM (FRCRAM) address
    monitor.execute("sysbus.syscfg WriteDoubleWord 0x604 0xA0004E00")
    # Magic number
    monitor.execute("sysbus WriteDoubleWord 0xA0004E00 0xE5ECC0DE")
    # Version
    monitor.execute("sysbus WriteDoubleWord 0xA0004E04 0x0001020E")
    # Status (root key present)
    monitor.execute("sysbus WriteDoubleWord 0xA0004E08 0x00400000")
    # Checksum
    monitor.execute("sysbus WriteDoubleWord 0xA0004E14 0xE5ADC2D0")

################################################
# Test
################################################

board, uart, nonSecureElf, secureElf = test_lib.parse_arguments()
test_lib.create_emulation(debug=DEBUG, quantum_time=QUANTUM_TIME)

machine = test_lib.emulation().add_mach("node1")
machine.load_repl(test_lib.renode_base_path() + "platforms/boards/silabs/" + board + ".repl")
machine.load_elf(nonSecureElf)
machine.load_elf(secureElf)
vse_init("node1")
node1 = TerminalTester(getattr(machine.sysbus, uart), TESTER_TIMEOUT)
test_lib._machine_name_to_machine_mapping["node1"] = machine
test_lib._tester_to_machine_mapping[node1] = machine

# Confirm that the NS app starts and prints on the CLI
test_lib.wait_for(node1, "PSA Crypto ECDH Example")