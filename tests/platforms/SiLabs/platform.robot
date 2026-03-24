*** Variables ***
# The ELF variable must be set from command line to the RailTest elf file to be used. 
# For example: renode-test --variable ELF:path_to_elf_file/railtest_sixg301.out this_test.robot
${URI}                          https://artifactory.silabs.net/artifactory/renode-production/prebuilt/siwx353
${ELF}                          ${URI}/power_manager_freertos_functional_testsuite-bd0701ad7527.out
${BOARD}                        board_everest
${QUANTUM_TIME}                 0.000020
${UART}                         itm
${DEFAULT_UART_TIMEOUT}         1

*** Keywords ***
Initial Setup
    Execute Command             emulation SetGlobalSerialExecution true
    Execute Command             emulation SetQuantum "${QUANTUM_TIME}"
    Execute Command             emulation SetAdvanceImmediately true
    Set Default Uart Timeout    ${DEFAULT_UART_TIMEOUT}
    Execute Command             logLevel 0

Create Node
    [Arguments]  ${machine_name}
    [Return]     ${tester_id}
    Execute Command             mach clear
    Execute Command             mach create "${machine_name}"
    Execute Command             machine LoadPlatformDescription @platforms/boards/silabs/${BOARD}.repl
    Execute Command             sysbus LoadELF @${ELF}
    Execute Command             sysbus.cpu VectorTableOffset `sysbus GetSymbolAddress "__Vectors"`
    Execute Command             sysbus LogAllPeripheralsAccess false
    ${tester_id}=               Create Terminal Tester  sysbus.${UART}  machine=${machine_name}  defaultPauseEmulation=true
    Execute Command             logLevel 3
    # This command together with using the "--enable-xwt" option when launching renote-test 
    # pops up a UART shell for each node and allows to see the nodes CLI activity.
    Execute Command             showAnalyzer sysbus.${UART}

Check For Failures
    [Arguments]  ${tester_id}
    Wait For Line On Uart       .*0 Failures.*  testerId=${tester_id}  timeout=3  treatAsRegex=true

    # If the line is not found within the timeout, the test will fail automatically.
    # Output of the test is something that looks like:
    # Memory Manager:267:test_statistics_functions:PASS
    # Memory Manager:3036:test_dynamic_allocator_large_heap:IGNORE: Heap size is less than 512KB, skipping test.
    # 
    # -----------------------
    # 61 Tests 0 Failures 2 Ignored 

*** Test Cases ***
Basic Test
    Initial Setup

    ${NODE1_TESTER_ID}=         Create Node  node1
    Check For Failures          ${NODE1_TESTER_ID}
