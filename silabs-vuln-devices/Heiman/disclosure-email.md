**Subject:** Security vulnerability report — HEIMAN Zigbee product

Dear HEIMAN Security Team,

My name is Junming Chen, and I am a Ph.D. student at George Mason University working under the supervision of Dr. Qiang Zeng. We are writing to report a security vulnerability identified in the following HEIMAN Zigbee product: the HS1SA smoke detector (firmware 120BA080, build 20260311).

In our analysis of the HS1SA firmware (120BA080, build 20260311), we found that the handler for a manufacturer-specific command (manufacturer code 0x120B, command 0xF3) uses a length field taken from the incoming frame as a loop bound and writes the corresponding records into a fixed-size stack buffer, without validating that length. Because the field is attacker-controlled and unchecked, the write overruns the buffer. An attacker who can send a crafted Zigbee message to the device may be able to overwrite the function's saved return address, alter the device's control flow, and potentially execute arbitrary code. The message is handled after Zigbee network-layer decryption, so the attacker must be joined to the same network; no authentication beyond network membership is required.

We reproduced the vulnerable execution path using firmware emulation (Renode) and confirmed that the crafted input overwrites the saved return address, and that on the function's normal return the processor transfers execution to code carried in the message, which then runs. Based on this validation, we believe the reported issue is reproducible and unlikely to be a static-analysis false positive. We have not yet confirmed the issue on a physical device unless otherwise noted in the attached report.

The attached report provides the affected firmware information, technical analysis, triggering input, emulation results, and suggested remediation.

We are reporting this issue as part of a coordinated vulnerability disclosure process. Please confirm receipt of this report and let us know whether your team requires any additional information. We would also appreciate being directed to the appropriate product security contact if this mailbox is not responsible for vulnerability reports.

Best regards,

Junming Chen
George Mason University
jchen73@gmu.edu
