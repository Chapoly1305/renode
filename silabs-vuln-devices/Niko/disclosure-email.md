**Subject:** Security vulnerability report — Niko Zigbee product

Dear Niko Security Team,

My name is Junming Chen, and I am a Ph.D. student at George Mason University working under the supervision of Dr. Qiang Zeng. We are writing to report a security vulnerability identified in the following Niko Zigbee product: the Niko connectable switch (firmware switchx2-113B0006).

In our analysis of the firmware from this switch (firmware switchx2-113B0006), we found that the manufacturer-specific Write-Attribute handler for cluster 0xFC00 copies an attribute record from the incoming frame into a fixed-size stack buffer without validating its length. Because the length is attacker-controlled and unchecked, the copy overruns the buffer. An attacker who can send a crafted Zigbee message to the device may be able to overwrite the function's saved return address, alter the device's control flow, and potentially execute arbitrary code. The message is handled after Zigbee network-layer decryption, so the attacker must be joined to the same network; no authentication beyond network membership is required.

We reproduced the vulnerable execution path using firmware emulation (Renode) and confirmed that the crafted input overwrites the saved return address, and that on the function's normal return the processor transfers execution to an attacker-chosen address and runs a small payload we placed in the message. Based on this validation, we believe the reported issue is reproducible and unlikely to be a static-analysis false positive. We have not yet confirmed the issue on a physical device unless otherwise noted in the attached report.

The attached report provides the affected firmware information, technical analysis, triggering input, emulation results, and suggested remediation.

We are reporting this issue as part of a coordinated vulnerability disclosure process. Please confirm receipt of this report and let us know whether your team requires any additional information. We would also appreciate being directed to the appropriate product security contact if this mailbox is not responsible for vulnerability reports.

Best regards,

Junming Chen
George Mason University
jchen73@gmu.edu
