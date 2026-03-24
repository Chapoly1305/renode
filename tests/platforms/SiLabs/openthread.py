from common import test_lib

################################################
# Globals
################################################

QUANTUM_TIME = 0.000050
PROMPT = "> "
DEBUG = True

################################################
# Utility functions
################################################

def wait_for_boot(node):
    test_lib.wait_for(node, "Reset info:")
    test_lib.wait_for(node, "Extended Reset info:")
    test_lib.write_line(node, "")
    test_lib.wait_for(node, PROMPT)

def leader_start(node):
    test_lib.write_line(node, "dataset init new")
    test_lib.wait_for(node, "Done")
    test_lib.write_line(node, "dataset commit active")
    test_lib.wait_for(node, "Done")
    test_lib.write_line(node, "dataset networkkey")
    key = test_lib.wait_for(node, r"^.{32}$")
    test_lib.wait_for(node, "Done")
    test_lib.write_line(node, "ifconfig up")
    test_lib.wait_for(node, "Done")
    test_lib.write_line(node, "thread start")
    test_lib.wait_for(node, "Role detached -> leader")
    test_lib.write_line(node, "state")
    test_lib.wait_for(node, "leader")
    test_lib.wait_for(node, "Done")
    return key

def router_start(node, key):
    test_lib.write_line(node, f"dataset networkkey {key}")
    test_lib.wait_for(node, "Done")
    test_lib.write_line(node, "dataset commit active")
    test_lib.wait_for(node, "Done")
    test_lib.write_line(node, "ifconfig up")
    test_lib.wait_for(node, "Done")
    test_lib.write_line(node, "thread start")
    test_lib.wait_for(node, "Role detached -> child", 30)
    # TODO: for now we don't wait for the transition from child to router since the
    # default timing is very long. The timing could be reduced from CLI.
    # test_lib.wait_for(node, "Role child -> router", 100)
    # test_lib.write_line(node, "state")
    # test_lib.wait_for(node, "router")
    # test_lib.wait_for(node, "Done")

def get_ipv6_address(node):
    test_lib.write_line(node, "ipaddr linklocal")
    ipv6_addr = test_lib.wait_for(node, r"^([0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}", endline=True)
    test_lib.wait_for(node, "Done")
    return ipv6_addr

def ping_test(pinger, pinged):
    ipv6_addr = get_ipv6_address(pinged)
    test_lib.write_line(pinger, f"ping {ipv6_addr}")
    test_lib.wait_for(pinger, "1 packets transmitted, 1 packets received")
    test_lib.wait_for(pinger, "Done")

################################################
# Test
################################################

board, uart, elf = test_lib.parse_arguments()
test_lib.create_emulation(debug=DEBUG, quantum_time=QUANTUM_TIME)

node1 = test_lib.create_node("node1", board, elf, uart)
node2 = test_lib.create_node("node2", board, elf, uart)

wait_for_boot(node1)
wait_for_boot(node2)

network_key = leader_start(node1)
router_start(node2, network_key)

ping_test(node1, node2)
ping_test(node2, node1)