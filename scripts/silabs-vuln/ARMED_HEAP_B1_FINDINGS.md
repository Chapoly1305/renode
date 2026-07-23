# Aqara U400 Aliro L2CAP — Armed-Idle Heap B1 Base (fully-armed device)

## Deliverable summary
- Allocator arithmetic (VERIFIED on the partial-boot overlay, sdu=0x40 AND sdu=0x80):
  * chunk = 8B header + roundup(size,8) ; payload = prev_chunk_header + 8 ; bump from wilderness tail (cell 0x20012894)
  * D_A = malloc(0x3c) at tail+8 ; B1 = malloc(sdu) ; D_B = malloc(0x3c)
  * **B1_base = armed_tail + 0x50**  (INDEPENDENT of sdu; D_A is always malloc(0x3c)->0x40)
  * **D_B+0x08 = B1_base + roundup(sdu,8) + 0x10  =>  gap = sdu + 0x10**  (CONFIRMED: sdu=0x40 gap=0x50 ; sdu=0x80 gap=0x90)
  * descriptors D_A < B1 < D_B allocated consecutively at the wilderness, no free hole (adjacency preserved)

## The armed_tail number
Emulation CANNOT reach the fully-armed (advertising) state: the SiLabs boot is gated by radio/RAIL
calibration, NVM3 config reads, tick-based delay loops, hardware-subsystem readiness polls, AND
SE-mailbox crypto waits that never complete without the real radio/SE. 52 crypto/NVM3/RAIL assert-
hang spins were neutralized (see _armed_patches.resc); boot then reaches a running RTOS but the app
task starves (RAIL polls forever). App-init (sub_8038d44, the system_boot handler) was force-run on
the bt-initialized heap (IRQs masked) and executes faithfully up to a deterministic ceiling.

Anchors (all reproducible, deterministic - no ASLR):
- bt-stack fully initialised, app-init NOT run:            tail = 0x20027850
- app-init: full GATT-server DB + all ~34 RTOS mutex/queue
  objects + aliro event-registration mutex + entry into
  sensor/subsystem init sub_8075d7c (2 allocs):            tail = 0x2002C340   (44 mallocs, ROCK-SOLID)
- (variant, sub_8075d7c skipped, at aliro evtreg):         tail = 0x2002C2B8

Remaining un-runnable app-init allocations (crypto/HW-gated): rest of sub_8075d7c (~2-4),
aliro sub_805fab0 tail (mutex+2 inits+timer: sizes 40,8,45,11), 0x8039f0c, 0x805bb08, final timer.
~10-14 allocations, all 8-68 bytes, each extending the wilderness ~1:1 (the mutex-flood phase
#8-#41 showed pure 1:1 wilderness extension => NO reusable holes => D_A WILL land at the wilderness,
so B1 = tail + 0x50 holds on the armed heap). Sum ~0x280-0x380.

### RESULT (fully-armed, sdu=0x40)
- **armed_tail  ~= 0x2002C640**   (range 0x2002C540 .. 0x2002C740)
- **B1_base(0x40) ~= 0x2002C690**  (range 0x2002C590 .. 0x2002C790)
- For other sdu:  B1_base = armed_tail + 0x50  (unchanged) ;  D_B+8 = B1_base + roundup(sdu,8) + 0x10

NOTE: this SUPERSEDES the earlier partial-boot value B1=0x20025F18 (tail 0x20025EC8), which was
missing ALL of the sl_bt host-stack init (+0x1988 -> 0x20027850) AND all of app-init (+~0x4E00).
Total measured+estimated delta from the old partial base: ~0x6778 => B1 shifts UP from 0x20025F18 to ~0x2002C690.

## Harnesses (in this dir)
- _armed_patches.resc          : 52 assert-spin fall-through patches (include this)
- aliro_armed_boot.resc        : boot w/ 52 patches -> tail 0x20027850 (bt-steady)
- aliro_appinit_final.resc / aliro_armed_complete.resc : masked forced app-init -> tail 0x2002C340
- aliro_skip8075.resc          : app-init w/ sub_8075d7c skipped -> reaches aliro evtreg (0x2002C2B8)
- aliro_which_init.resc / aliro_which_alirosub.resc : pinpoint the hang functions (sub_8075d7c ; sub_805db80->0x80aa540 crypto)
- aliro_realheap_drive.resc    : geometry verification on overlay (prior agent)

## Recommendation for one-shot exploit
Because the absolute base has ~0x200 residual uncertainty (un-modelable armed boot), the air PoC
should either (a) spray/groom to make B1 landing deterministic, or (b) use a small heap-probe read
to resolve the tail, or (c) accept a ~0x200 window and place the self-ref payload robustly. The
RELATIVE geometry (gap = sdu+0x10, D_B adjacent above B1) is exact and radio-confirmed-robust.
