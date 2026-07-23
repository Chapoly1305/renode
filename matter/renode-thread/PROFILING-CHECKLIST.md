# Renode stock-firmware profiling playbook (Thread/Matter over emulated radio)

How to localize a "packet doesn't get through" bug in **unmodified** firmware with the
*fewest* hooks. Firmware is never modified — hooks are read-only observation points.

Guiding idea: **don't hook every message type. Hook ONE discriminator per layer
boundary and read the type/command field.** One command-aware hook replaces a dozen
per-type handler hooks.

---

## The minimal probe kit ("Tier 0 + 3")

### Tier 0 — zero firmware hooks (use the emulator model)
These need no symbols and no ELF knowledge; turn them on first.

- [ ] **Radio RX/TX outcome**: `logLevel 1 sysbus.radio`
      → logs `Received at ... : <hex>` / `Dropping (<reason>) : <hex>` / `Sending frame`.
      Gives you, per frame: received-vs-dropped, **drop reason**
      (`not in RXSEARCH` / `PRE-SYNC COLLISION` / `SYNC MISMATCH` / `RX collision` /
      `TX was aborted`), and **frame size** (→ single-frame vs fragmented).
      *(Verify Info lines actually appear — if only WARNING shows, the level didn't apply; re-issue it.)*
- [ ] **Bridge injection log**: `verbose: true` on the `ieee802154` bridge in the .repl
      → `host->device MPDU (N B)` for every frame the bridge injects (sizes reveal fragmentation).

Tier 0 alone resolves most **link-level** problems (frame loss, fragment loss, collisions,
radio-not-listening) without touching the firmware.

### Tier 1 — three firmware hooks (the layer-boundary bisection)
Locate by name with one `nm` pass (see below). Each hook writes one byte to its **own**
file (shared-file appends tear). Fire = "a packet reached this layer".

| # | Layer boundary | Symbol (grep pattern) | Answers |
|---|----------------|-----------------------|---------|
| 1 | 6LoWPAN reassembled → IPv6 | `Ip6.*HandleDatagram` | did the (possibly fragmented) datagram assemble? **the single best below/above-reassembly separator** |
| 2 | app protocol dispatch (Thread control) | `Mle3Mle16HandleUdpReceive` | did an MLE msg reach the stack — **read the command byte here** for per-type visibility |
| 3 | Matter inbound | `SessionManager.*OnMessageReceived` | did an operational Matter message arrive |

That's it. If you're only chasing Thread attach, #1 + #2 suffice. Add #3 only once you're
into operational CASE.

---

## Bisection decision tree (where does the packet die?)

```
radio "Received"?  ── no ──▶ LINK: check drop reason
   │                          • "not in RXSEARCH" → device not listening (2nd fragment / half-duplex / BLE contention)
   │                          • "collision"/"sync" → timing/overlap in the bridge
   yes
   ▼
Ip6::HandleDatagram fires? ── no ──▶ MAC / 6LoWPAN: single-frame ok but fragmented fails ⇒ reassembly / fragment loss
   │                                  (cross-check Tier-0 sizes: was it >1 frame?)
   yes
   ▼
MLE command byte present at HandleUdpReceive? ── missing ──▶ UDP demux / local delivery of that dest addr
   │  (10=ParentResp 12=ChildIdResp 4=Adv 8=DataResp …)
   present but not acted on
   ▼
app-level reject (state / security / neighbor) — hook the specific handler only now
```

The one-hook-reads-the-type trick: at the dispatch site, `nm`+`objdump` the function once to
find the instruction that loads the type/command into a register (or a known stack slot), hook
that PC, and read it:
```
cpu AddHook 0x<pc> "open('/tmp/cmd','a').write(str(cpu.GetRegister(3).RawValue)+'\n')"
```
This replaced ~6 per-command MLE handler hooks with one.

---

## Locating the addresses — one nm pass

```bash
NM=arm-none-eabi-nm; ELF=<firmware>.out
$NM $ELF | grep -E \
 'Ip6.*HandleDatagram|Mle3Mle16HandleUdpReceive|SessionManager.*OnMessageReceived'
# add-on probes only if the tree points there:
$NM $ELF | grep -E 'Mac3Mac19HandleReceivedFrame|ProcessReceiveSecurity'          # MAC / crypto
$NM $ELF | grep -E 'Srp6Client.*(ProcessAutoStart|SelectUnicastEntry|Start|SendUpdate)'  # SRP register
$NM $ELF | grep -E 'Mle3Mle.*(SendChildIdRequest|HandleChildIdResponse|BecomeDetached)'  # attach detail
```
Use the **even** address `nm` prints (Renode `AddHook` wants the PC, thumb bit stripped).

---

## Prerequisites checklist (per firmware)

- [ ] **Symbolized ELF** (`.out`/`.elf`) — verify `nm <elf> | head` prints names, not just addrs.
      *No symbols ⇒ big jump in cost: reverse-engineer functions in Binary Ninja/Ghidra first.*
- [ ] Firmware's OpenThread + Matter **version** known (so a symbol's semantics are trusted).
- [ ] A .repl + scenario that **boots** the image, and a harness that **reliably reaches the failing stage**
      (here: BLE commission → TNE → attach). Flaky reach ⇒ wrap in a retry loop.
- [ ] Addresses are **firmware-specific**: re-run `nm` whenever the image changes (offsets move).

## Per-investigation checklist

- [ ] Turn on **Tier 0** first (radio Info + bridge verbose). Often enough on its own.
- [ ] **Calibrate**: confirm each hook fires on a *known-good* message before trusting a zero
      (a zero can mean "inlined / wrong overload", not "didn't happen" — some symbols don't fire).
- [ ] One byte per hook to its **own** `/tmp/<tag>` file; histogram with `sort|uniq -c`.
- [ ] Hooks are **per-function-entry = low perturbation**. Avoid periodic RTT drains (they perturb
      and can freeze the device mid-window). OT core logging is often **compiled out** — don't rely on it.
- [ ] Bisect top-down with the tree; only add narrow hooks (specific handler, security, outbound
      `Send*`) once the tree points at that layer.
- [ ] Register/memory reads in a hook: `cpu.GetRegister(n).RawValue`, `machine.SystemBus.ReadByte(addr)`.

## Reusable scenarios in this repo
`matter/renode-thread/scenarios/e2e-15.4-*.resc` — worked examples of each probe layer
(`-mledisp` = the command-byte dispatch hook; `-fragrx` = radio-Info fragment correlation;
`-srp` = SRP client ladder). Copy the closest one and swap addresses from your `nm` pass.
