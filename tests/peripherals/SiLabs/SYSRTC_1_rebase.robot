*** Settings ***
Resource    ${RENODEKEYWORDS}

*** Keywords ***
Create Timer
    Execute Command    mach create "timer"
    Execute Command    machine LoadPlatformDescriptionFromString "sysrtc: Timers.SiLabs_SYSRTC_1 @ sysbus <0x0, +0x4000> { frequency: 1000 }"
    Execute Command    sysbus WriteDoubleWord 0x10 1

Flags Should Equal
    [Arguments]    ${expected}
    ${flags}=    Execute Command    sysbus ReadDoubleWord 0x40
    Should Be Equal As Integers    ${flags}    ${expected}

*** Test Cases ***
Future Compare Wins Over Numerically Smaller Past Compare
    Create Timer
    Execute Command    sysbus WriteDoubleWord 0x18 100
    Execute Command    sysbus WriteDoubleWord 0x4C 50
    Execute Command    sysbus WriteDoubleWord 0x50 110
    Execute Command    sysbus WriteDoubleWord 0x48 3
    Execute Command    emulation RunFor "0.005"
    Flags Should Equal    0
    Execute Command    emulation RunFor "0.007"
    Flags Should Equal    4

Compare Fires Across Counter Wrap
    Create Timer
    Execute Command    sysbus WriteDoubleWord 0x18 0xFFFFFFFC
    Execute Command    sysbus WriteDoubleWord 0x4C 2
    Execute Command    sysbus WriteDoubleWord 0x48 1
    Execute Command    emulation RunFor "0.010"
    ${flags}=    Execute Command    sysbus ReadDoubleWord 0x40
    ${compare}=    Evaluate    int(${flags}) & 2
    Should Be Equal As Integers    ${compare}    2
