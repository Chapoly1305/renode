*** Settings ***
Resource    ${RENODEKEYWORDS}
Test Setup    Create Mailbox

*** Keywords ***
Create Mailbox
    Execute Command    mach create "mailbox"
    Execute Command    machine LoadPlatformDescriptionFromString "semailbox: Miscellaneous.SiLabs.SiLabs_SEMAILBOX_1 @ sysbus <0x0, +0x4000>"

Tx Status Should Match
    [Arguments]    ${mask}    ${expected}
    ${status}=    Execute Command    sysbus ReadDoubleWord 0x40
    ${masked}=    Evaluate    int(${status}) & int(${mask})
    Should Be Equal As Integers    ${masked}    ${expected}

Tx IRQ Should Be
    [Arguments]    ${state}
    ${irq}=    Execute Command    sysbus.semailbox TxIRQ
    Should Contain    ${irq}    GPIO: ${state}

*** Test Cases ***
Empty Fifo Has Space With IRQ Gated By Enable
    Tx Status Should Match    0x300000    0x100000
    Tx IRQ Should Be    unset
    Execute Command    sysbus WriteDoubleWord 0x58 1
    Tx IRQ Should Be    set
    Execute Command    sysbus WriteDoubleWord 0x58 0
    Tx IRQ Should Be    unset

Partial Multiword Message Still Advertises Space
    Execute Command    sysbus WriteDoubleWord 0x58 1
    Execute Command    sysbus WriteDoubleWord 0x50 80
    Execute Command    sysbus WriteDoubleWord 0x0 0
    Tx Status Should Match    0x30FFFF    0x100002
    Tx IRQ Should Be    set

Full Fifo Deasserts Space And TX Interrupt
    Execute Command    sysbus WriteDoubleWord 0x58 1
    Execute Command    sysbus WriteDoubleWord 0x50 80
    FOR    ${i}    IN RANGE    15
        Execute Command    sysbus WriteDoubleWord 0x0 0
    END
    Tx Status Should Match    0x30FFFF    0x200010
    Tx IRQ Should Be    unset

Complete Multiword Command Drains TX And Signals RX
    Execute Command    sysbus WriteDoubleWord 0x58 3
    Execute Command    sysbus WriteDoubleWord 0x50 16
    Execute Command    sysbus WriteDoubleWord 0x0 0xFFFF0000
    Execute Command    sysbus WriteDoubleWord 0x0 0
    Execute Command    sysbus WriteDoubleWord 0x0 0
    Tx Status Should Match    0x30FFFF    0x100000
    Tx IRQ Should Be    set
    ${irq}=    Execute Command    sysbus.semailbox RxIRQ
    Should Contain    ${irq}    GPIO: set
    Execute Command    sysbus ReadDoubleWord 0x54
    ${irq}=    Execute Command    sysbus.semailbox RxIRQ
    Should Contain    ${irq}    GPIO: unset

Reset Clears Partial Command And IRQ
    Execute Command    sysbus WriteDoubleWord 0x58 1
    Execute Command    sysbus WriteDoubleWord 0x50 80
    Execute Command    machine Reset
    Tx Status Should Match    0x30FFFF    0x100000
    Tx IRQ Should Be    unset
    Execute Command    sysbus WriteDoubleWord 0x58 1
    Tx IRQ Should Be    set
