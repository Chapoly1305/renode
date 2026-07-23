> ⚠️ SCOPE (2026-07-23): "RCE→unlock control-flow reachability demonstrated in emulation" means EMULATION CODE-PATH reachability ONLY. NO physical actuation on hardware (OTA testing negative; SWD fused/inaccessible; U400 BLE not bringable-up in Renode due to runtime RF-cal token gap — stock Matter app commissions over BLE, U400 does not). DoS/reset is the only hardware-proven finding. See the local project STATUS.md.

# Aliro L2CAP Reassembly Heap-Overflow — Verification Working Environment

**Created:** 2026-07-18 · **Status:** COMPLETE for CVD. Candidate vuln statically verified (3 tools),
dynamically verified in Renode up to the firmware's **real top-level event dispatch** (overflow fires
on real firmware bytes; upstream does NOT clamp), **pre-auth reachability** proven by full static call-
graph (no auth gate radio-link-up → overflow), **RCE→unlock** control-flow reachability demonstrated in
emulation, and an **nRF hardware trigger PoC** built for the air path.

### Deliverables index
| Doc | Establishes |
|---|---|
| `renode/ALIRO_L2CAP_DYNAMIC_VALIDATION.md` | approach B — the overflow fires; no upstream clamp |
| `renode/ALIRO_REALDISPATCH_VALIDATION.md` | **B+** — overflow via the firmware's real top-level event dispatch; + the Renode ceiling (why OTA/full-boot isn't emulatable) |
| `renode/ALIRO_PREAUTH_REACHABILITY.md` | static call-graph: pre-auth **and** pre-crypto, no auth gate on any hop |
| `renode/ALIRO_UNLOCK_REACHABILITY_POC.md` | overflow → corrupt `transfer+0x04` vtable ptr → PC = door-unlock `sub_80714f4` |
| `poc/README.md` + `poc/aliro_l2cap_overflow_poc.py` | nRF hardware trigger PoC (byte-level PDUs + HCI driver) |
| `analysis/FUNCTION_ANNOTATIONS.md` | full reversed-function/structure map (§1–§10) |

## 0. Objective

Dynamically verify a candidate **pre-auth remote heap buffer overflow** in Aqara's
Aliro BLE L2CAP reassembly, and settle two open questions:
1. **Reachability / exploitability vs. DoS** — is `offset+len` actually pushable past
   `sdu_size` at the vulnerable `memcpy`, and does it corrupt useful heap state?
2. **Upstream bounding** — does the SiLabs `sl_bt` L2CAP CoC layer clamp cumulative
   `offset+len` to `sdu_size` before invoking the callback? (If not → live overflow.)

The static case is done and cross-checked; see §3–§5.

**ANSWERED (2026-07-18, static RE + Renode approach B):** NO upstream clamp. The SDK transfer
manager `sli_bt_l2cap_transfer_on_bt_event` (0x801de58) dispatches `channel_data` to the reassembly
handler `sub_801d96c`, which reads `sdu_size` from the wire, computes the fragment length UNCLAMPED,
and calls `on_data_received` — its only bounds test (`sdu_pointer+frag_len > sdu_size`, 0x801d9c8)
runs AFTER the copy. Dynamically confirmed on real firmware: a declared `sdu_size=4` + a 198-byte
first fragment produces a 194-byte linear heap OOB write (no forced PC below the handler entry). Full
receive chain + addresses + repro in `renode/ALIRO_L2CAP_DYNAMIC_VALIDATION.md`. Everything needed to
continue is in this directory.

## 1. The finding (one paragraph)

`aliro_ble_l2cap_on_data_received` (the SiLabs-BT-stack L2CAP CoC data callback)
reassembles a received SDU into a heap buffer sized to the peer-declared `sdu_size`,
then does `memcpy(buffer + offset, fragment_data, fragment_len)` **with no
`offset + len <= sdu_size` bounds check**. The only guard is a non-fatal `offset <=
sdu_size` log-assert that falls straight through into the copy. A malicious BLE peer
(no pairing required — see §4) can declare a small `sdu_size` and stream fragments whose
cumulative `offset+len` exceeds it → **linear heap overflow with attacker-controlled
content, offset, and length**. Class: CWE-787, pre-auth, remote over BLE.

## 2. Target

| | |
|---|---|
| Product | **Aqara Smart Lock U400**, VID 0x115F (4447) PID 0x2804 (10244), fw **v3110**, mainnet |
| SoC | Silicon Labs **EFR32MG24** (ARM Cortex-M33, Thumb-2 only) |
| Build | **Simplicity SDK 2024.6.0**, Aqara proprietary Aliro stack (`aliro_ble_*`) |
| ELF | `firmware/U400_primary_target.elf` (also in `firmware/aliro_elfs/`) |
| elf sha256 | `3ebef355f56de4faaf1ff66d02ffbcfa59c0d55c117d38256cddb056750f9bc7` |
| Load base | `0x08006000` (single exec segment `0x8006000–0x816a2d8`); RAM `0x20000000+` |
| Arch in BN | `thumb2` (NOT `armv7` — see §6 gotcha) |

13 Aliro ELFs total (11 Aqara + 2 LG) in `firmware/aliro_elfs/` + `MANIFEST.{json,csv}`.
The overflow was confirmed on U400; the other 10 Aqara locks are the same `aliro_ble_*`
codebase and should carry the same bug (verify by diffing the handler).

## 3. Key addresses (U400) — the vulnerable handler `0x805e7b0`

| Addr | What |
|---|---|
| `0x805e7b0` | `aliro_ble_l2cap_on_data_received(arg1=chan, arg2=offset, arg3=len, arg4=data)` |
| `0x805e862` | `ldrh r0,[r5,#0xe]` (sdu_size) → `bl 0x8034b1c` = **`malloc(sdu_size)`** |
| `0x805e86c` | `str r0,[r4,#0x38]` — reassembly buffer stored at `transfer+0x38` |
| `0x805e82a-82e` | `ldrh r3,[r5,#0xe]; cmp r3,r7; bcc 0x805e884` — the ONLY guard (`sdu_size < offset`) |
| `0x805e884-89c` | assert-log block; ends `b.n 0x805e830` → **jumps back into the copy (non-fatal)** |
| `0x805e830-838` | `r0=buf; add r0,r7(offset); r1=data; r2=len; bl 0x8128220` = **`memcpy(buf+offset,data,len)`** ← OVERFLOW |
| `0x805e83c-842` | `add r6,r7; cmp r6,sdu_size; beq …` — `offset+len` computed only AFTER copy, for completion |

**Transfer descriptor** (60 bytes, `malloc` in `sub_805e5fc`): the BLE stack calls the
callback with `arg1 = descriptor + 4`. Field map (via `arg1`): `sdu_size=*(arg1+0xe)`,
`max_len=*(arg1+0x10)`, `conn_id=*(arg1+0x24)`; via descriptor base `r4`:
`reassembly_buf=*(r4+0x38)`. `sub_805e5fc` memsets 0x3c and sets defaults
(`+0xe`=500, `+0x10`=247); the per-transfer `sdu_size` used by the handler is set from
the wire elsewhere (find the SDU-start handler — one of the `0x805e3–0x805e6` funcs).

**Helpers:** `0x8034b1c`=malloc-wrapper (→`0x8010b90`), `0x8034b5c`=free (→`0x80cf04a`),
`0x8128220`=memcpy, `0x8033598`/`0x80336f0`=logging.

**Downstream path** (reassembled SDU → parser, for the ePubK/cryptogram validation
question): `on_data_received` posts msg `"aliro_l2cap_recv"` → dispatch `0x805f454`
(branch @`0x805f578`) → `sub_805ea3c`→`sub_805ea40` (buffers into session) →
`sub_801df64` (locked state machine) → `sub_80d5fc0`→`sub_80d5ff0` (segmentation SM) →
… table-driven TLV parse (deeper; not yet reached).

## 4. Evidence: BLE path + no pairing (why it's pre-auth remote)

**Spec (`26-42802-001_Aliro_1.0_spec` Ch.11.2):** *"The Bluetooth LE pairing … is OPTIONAL
and not required."* Reader advertises **connectable undirected** (any Central connects).
All Aliro security is application-layer (AUTH0/AUTH1 crypto) — which runs AFTER this
reassembly.

**Firmware:** the Aliro handlers are wired to SiLabs BT stack L2CAP CoC events —
`sl_bt_evt_l2cap_channel_data_id` → `on_data_received`; also `..._le_channel_open_request`,
`..._channel_opened`, `..._credit`. CoC accept checks **only the SPSM** (`"Requested SPSM
%d differs from transfer SPSM %d"`) — **no** encryption/bonding: there are **no `sl_bt_sm_*`
/ bondable / increase_security** strings, and `on_channel_opened` (`0x805e41c`) +
`on_data_received` have **zero** security checks. (The `matter_pairing_code`/`PASESession`
strings are Matter commissioning, unrelated to the Aliro CoC.)

→ **Any BLE device in range, unpaired, can open the Aliro L2CAP CoC (SPSM match only) and
reach the vulnerable reassembly.**

**CONFIRMED (2026-07-18, full static call-graph trace) — see `renode/ALIRO_PREAUTH_REACHABILITY.md`:**
every hop from radio-link-up to the overflowing `memcpy` was walked and decompiled; NO auth gate
on any hop.
- `sl_bt_evt_connection_opened` ("BLE Connected", masked 0x600a0) → `sub_805df38` (aliro event cb,
  registered @`sub_805db80`) → `sub_805dd04` → `sub_80ec036` → `sub_805e9c8`
  (`aliro_ble_l2cap_start_recv_data`) → creates+registers the RX transfer **UNCONDITIONALLY on
  connect**, before pairing/PASE/CASE/commissioning.
- CoC open-request (masked 0x14300a0) → `sub_801de58` → `sub_801d81c` accepts on ONLY: transfer
  exists + request SPSM == transfer SPSM `0x0080` (fixed constant, `descriptor+0x16`) + version flag
  `*0x20024074`!=0 (device-local boot readiness) → `sub_80aa368(...,0)` (open-channel-response ACCEPT).
  No `sl_bt_sm_*` / encryption / bonding / authorization.
- channel_data (masked 0x34300a0) → `sub_801d96c` → `on_data_received` `memcpy` overflow — at the
  **transport reassembly** layer, i.e. also **pre-application-crypto** (Aliro AUTH0/AUTH1 runs on the
  completed SDU higher up, never before the per-fragment memcpy).

So the overflow is reachable **pre-auth AND pre-crypto**. Method = static (BN call-graph + decompile);
the full connect→open→data sequence was NOT run through the real radio (radio init wedges at `b .`
@0x808105c) — but the conclusion is the *absence* of any auth branch, which execution would not add.

## 5. Static verification (3 independent tools agree — see `analysis/evidence/`)

| Tool | Artifact | Result |
|---|---|---|
| objdump (raw Thumb asm, no decompiler) | `handler_objdump_thumb.asm` | `malloc(sdu_size)`; `bcc`→log→`b.n` back to copy; `memcpy(buf+offset,data,len)`, no `offset+len` check |
| Binary Ninja (HLIL) | (reproduce from `U400_ls.bndb`) | same |
| Ghidra 11.1 (independent decompiler) | `handler_ghidra.c` | same: `if(sdu_size<offset){log;}` (no return) then `memcpy(buf+offset,data,len)` |

Not a decompiler artifact — the log-then-`b.n`-back-into-copy is explicit in the
instruction stream.

## 6. Impact assessment

- **Primitive:** controlled **linear** heap overflow — content (fragment payload), size
  (cumulative, attacker paces fragments, per-frame ≤ MPS ~247), into a small
  attacker-sized (`sdu_size`) heap chunk. Standard allocator with inline metadata
  (`0x8010b90`) → overflow corrupts the adjacent chunk header / next object.
- **Lower bound (near-certain): remote DoS** — crash/reboot/brick a lock over BLE, pre-auth.
- **Ceiling: RCE → unlock — control-flow reachability now DEMONSTRATED (emulator).** No ASLR
  (fixed load `0x8006000`, fixed RAM). The unlock actuation routine is `sub_80714f4`
  ("Door unlock start", wrapper `sub_80718fc`). The transfer manager dispatches received fragments
  via an indirect call through the per-transfer callback-vtable pointer at `transfer+0x04`
  (`sub_801d96c` `blx r7` @`0x801d9be`); the overflow overwrites that pointer → the firmware's own
  `blx` transfers PC into `sub_80714f4`. Two-step Renode PoC in
  `renode/ALIRO_UNLOCK_REACHABILITY_POC.md`. → Critical (CVSS ≈ 9–10). Remaining gap = real-device
  heap grooming (layout-dependent, same limitation as ZB-02); no physical actuation / no OTA weapon.
- **Ethical boundary:** analysis + vendor disclosure is in scope. Do **NOT** build a working
  door-opening exploit against deployed locks without authorized-test permission.

## 7. Renode dynamic-verification plan (the next step)

Renode is at **`/home/chen/sl-renode`** (binary `renode`; config `~/.config/renode`).
Two approaches, do the fast one first:

**(A) Direct-drive the reassembly (fast, verifies the bug itself):**
Load the ELF into a Cortex-M33 (EFR32MG24) Renode machine, set PC/SP to a harness that
calls `aliro_ble_l2cap_on_data_received` (`0x805e7b0`) with a crafted transfer descriptor
(small `sdu_size`) and fragments driving `offset+len > sdu_size`. Watch the heap region
for the OOB write (Renode memory watchpoint) at the `memcpy` `0x805e838`. This proves the
overflow independent of the BLE stack. **Bypasses the radio** — sufficient to prove the
defect and DoS-vs-controlled-write.

**(B) Full L2CAP injection (proves end-to-end reachability incl. upstream bounding): ✅ DONE.**
Implemented as a dispatch-injection at the firmware's own `channel_data` reassembly handler
`sub_801d96c` (one level below the mutex-gated BGAPI event loop). The firmware reads `sdu_size` from
the wire and computes the fragment length UNCLAMPED, then overflows — no forced PC below the handler.
Confirms the CoC layer does NOT clamp `offset+len`. Harness + result:
`renode/ALIRO_L2CAP_DYNAMIC_VALIDATION.md`, `renode/aliro_rx_inject.resc`. (Radio/RAIL bring-up is
NOT needed — boot is skipped and the joined-channel state is pre-seeded; only the 3 debug loggers are
stubbed. Full BGAPI-event-loop injection additionally needs the RTOS mutex at `*0x20013888` live.)

**(B+) Max-fidelity: real top-level event dispatch: ✅ DONE.** Upgraded entry to the firmware's OWN
registered BGAPI event callback `sub_805df38` with a complete `l2cap_channel_data` event; the firmware
itself does the id-demux (`sub_801de58`, masks id 0x034300a0), init-gate, find-transfer, and dispatches
`on_data_received` via the REAL .data vtable at its real addr 0x20002828 → 194B OOB. No forced PC below
the callback. `renode/ALIRO_REALDISPATCH_VALIDATION.md` + `aliro_realdispatch_inject.resc` +
`aliro_realdispatch.run.log`. That report also documents the **Renode ceiling** (no BLE PHY; radio init
fatally traps on multiple `b .`; started-machine can't be re-driven; loaded-state heap is dead) — i.e.
why a fully-natural boot-to-OTA run is not achievable in-emulator and why the nRF hardware PoC (`poc/`)
is the complementary air-path proof.

**Renode gotchas / notes:**
- EFR32MG24 platform: check `/home/chen/sl-renode` for an `efr32`/`series-2` `.repl`
  platform file; if absent, author a minimal Cortex-M33 machine mapping flash @0x08000000
  and RAM @0x20000000, load the ELF (it's positioned at 0x08006000 by a bootloader offset).
- The ELF has no section headers; use `sysbus LoadELF` with the raw image or `LoadBinary`
  at 0x08006000.
- Prior Renode work exists under `~/.claude/projects/-home-chen-sl-renode`.

## 8. Tooling (all on this Linux host)

| Tool | Path | Use |
|---|---|---|
| Renode | `/home/chen/sl-renode/renode` | dynamic emulation |
| Binary Ninja (headless) | `PYTHONPATH=/home/chen/binaryninja/python python3` (v5.3.9757) | reload `U400_ls.bndb` |
| Ghidra 11.1 | `/home/chen/ghidra_11.1_PUBLIC/support/analyzeHeadless` | 2nd decompiler |
| IDA 9.2 + FLAIR | `/home/chen/ida-classroom-free-9.2/{idat,tools/flair/{pelf,sigmake}}` | disasm / sigs |
| ARM toolchain | `/usr/local/bin/arm-none-eabi-{gcc,objdump,nm}` (gcc 9.3) | ground-truth disasm |

## 9. Reload the Binary Ninja analysis (has 15,258 functions already)

```python
import binaryninja as bn
bv = bn.load("analysis/U400_ls.bndb", update_analysis=False)   # do NOT re-analyze; it's saved
fn = bv.get_function_at(0x805e7b0)
for l in fn.hlil.root.lines: print(l)
```
Reusable scripts in `analysis/bn_scripts/` (loaders, ref-finders, tag-scan, decompilers).
**Gotcha:** functions must be created as `thumb2` — `bv.add_function(addr,
bn.Architecture['thumb2'].standalone_platform)`; a plain `armv7` add mis-decodes as ARM.
Cortex-M loads addrs via **MOVW/MOVT** (not just literal pools) — BN resolves refs only
after the function is analyzed as thumb2.

## 10. Directory layout

```
aliro-reassembly-audit/
├── HANDOFF.md                         ← this file
├── firmware/
│   ├── aliro_silabs_firmware.zip      full DCL pack (13 aliro + 7 no_aliro, .ota+.elf)
│   ├── aliro_elfs/                    13 Aliro app ELFs
│   ├── U400_primary_target.elf        primary target
│   └── MANIFEST.{json,csv}, README.md
├── analysis/
│   ├── U400_ls.bndb                   BN DB, 15,258 funcs analyzed
│   ├── bn_scripts/                    reusable headless BN scripts
│   └── evidence/
│       ├── handler_0x805e7b0.bin      raw bytes of the handler
│       ├── handler_objdump_thumb.asm  objdump ground truth
│       └── handler_ghidra.c           independent Ghidra decompile
└── renode/                            (build the harness here)
```

## 11. Provenance / do-not-publish

Firmware is copyrighted vendor material pulled from the Matter DCL (mainnet/testnet,
2026-07-18) — keep this environment **out of any public git repo** (mirrors how the CSA
specs are handled). The reported SiLabs `aliro_2.0.0` findings 01–09 are a **separate**
codebase (the SiLabs reference reader) and do NOT ship in these firmwares (proven via
absent constant-table fingerprint) — this environment is only about the **Aqara-native**
reassembly overflow.
