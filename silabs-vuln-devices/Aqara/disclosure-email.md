**Subject:** Security vulnerability report — Aqara Zigbee products

Dear Aqara Security Team,

My name is Junming Chen, and I am a Ph.D. student at George Mason University working under the supervision of Dr. Qiang Zeng. We are writing to report security vulnerabilities identified in the following Aqara Zigbee products: the Smart Pet Feeder C1 (lumi.feeder.acn001) and the Single Switch Module T1, No-Neutral (lumi.switch.n0agl1).

In our analysis of the Pet Feeder C1 firmware (version 0.0.0_3833, build 20221213122302), we found that the handler for the manufacturer-specific cluster 0xFCC0 (its segmented-message reassembly routine) uses a segment index taken directly from the incoming frame to compute the destination of a memory copy, without validating that index. Because the index is never bounds-checked, an attacker controls where the copied bytes are written. An attacker who can send a crafted Zigbee message to the device may be able to corrupt device memory well outside the intended buffer, which can crash the device and, because both the write location and the contents are attacker-controlled, may be developed further toward code execution. The message is handled after Zigbee network-layer decryption, so the attacker must be joined to the same network; no authentication beyond network membership is required.

In the Single Switch Module T1 firmware (version 0.0.0.0023, build 20220121143603), a separate manufacturer-specific 0xFCC0 handler copies an attacker-controlled field into a fixed-size stack buffer without checking its length. The copy overruns the buffer and overwrites saved registers and the function's saved return address. The writable length is bounded by the radio receive limit, so the impact we confirmed is that a single crafted message corrupts the return address and causes the device to crash or reset (denial of service). The same network-membership precondition applies.

We reproduced both vulnerable execution paths using firmware emulation (Renode) and confirmed that the crafted input causes, respectively, an out-of-bounds memory write at an attacker-chosen location on the Pet Feeder, and a saved-return-address overwrite that faults the processor on return on the Single Switch Module. Based on this validation, we believe the reported issues are reproducible and unlikely to be static-analysis false positives. We have not yet confirmed the issues on a physical device unless otherwise noted in the attached report.

The attached report provides the affected firmware information, technical analysis, triggering input, emulation results, and suggested remediation.

We are reporting these issues as part of a coordinated vulnerability disclosure process. Please confirm receipt of this report and let us know whether your team requires any additional information. We would also appreciate being directed to the appropriate product security contact if this mailbox is not responsible for vulnerability reports.

Best regards,

Junming Chen
George Mason University
jchen73@gmu.edu
