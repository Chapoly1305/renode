#!/usr/bin/env bash
# Created: 2026-08-18
# Purpose: Test BleCentralBridge.cs's connection-event gating WITHOUT rebuilding
#   Renode. It extracts ConnectionEvent / AbandonConnection / ScheduleFromAnchor
#   VERBATIM from the live source and drives them against a modelled BLE
#   peripheral, so the code under test is the code that ships.
#
#   This exists because a change to that gating regressed the entire emulation
#   corpus to 0 bootable: the interval search engaged on a HEALTHY link, because
#   the miss counter was evaluated ~150 us before the peer could physically have
#   replied. Check "A" below fails on that bug. Run this before shipping any
#   change to the bridge's event scheduling.
#
# usage: scripts/ble_bridge_gate_test/run.sh [path-to-BleCentralBridge.cs]
# Retention: PERMANENT while BleCentralBridge.cs owns connection-event timing.
set -euo pipefail
HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SRC="${1:-$HERE/../../../../src/Infrastructure/src/Emulator/Peripherals/Peripherals/Wireless/BleCentralBridge.cs}"
[[ -f "$SRC" ]] || { echo "FATAL: no source at $SRC" >&2; exit 2; }

if grep -q "ConnectionEvent(uint generation, double" "$SRC"; then LEVEL=p2; else LEVEL=p1; fi
echo "### source     : $SRC"
echo "### patch level: $LEVEL  (p1 = supervision only, p2 = + interval search)"

WORK="$(mktemp -d)"; trap 'rm -rf "$WORK"' EXIT
python3 - "$SRC" "$HERE/stubs_$LEVEL.cs.in" "$WORK/Bridge.cs" <<'PY'
import sys
src=open(sys.argv[1]).read()
def grab(sig):
    if sig not in src: return None
    i=src.index(sig); j=src.index("{", i); d=0
    for k in range(j,len(src)):
        if src[k]=="{": d+=1
        elif src[k]=="}":
            d-=1
            if d==0: return src[i:k+1]
names=["private void ConnectionEvent(uint generation, double intervalThatPlacedThisEvent)",
       "private void ConnectionEvent(uint generation)",
       "private void ScheduleFromAnchor(uint generation, double intervalUs)",
       "private void AbandonConnection()",
       "private double NowMicroseconds()"]
blocks=[grab(n) for n in names]
blocks=[b for b in blocks if b]
assert blocks, "no methods extracted -- has the signature changed?"
body="\n\n".join("    "+b for b in blocks)
body=body.replace("                AbandonConnection();\n                return;",
                  "                Abandons++; AbandonConnection();\n                return;")
out=open(sys.argv[2]).read().replace("@@EXTRACTED@@", body)
open(sys.argv[3],"w").write(out)
print("    extracted %d methods verbatim" % len(blocks))
PY
cp "$HERE/main_$LEVEL.cs" "$WORK/Main.cs"
[[ -f "$HERE/regressed_reference_$LEVEL.cs" ]] && cp "$HERE/regressed_reference_$LEVEL.cs" "$WORK/"
cat > "$WORK/gate.csproj" <<'EOF'
<Project Sdk="Microsoft.NET.Sdk">
  <PropertyGroup><OutputType>Exe</OutputType><TargetFramework>net8.0</TargetFramework>
  <Nullable>disable</Nullable><AssemblyName>gate</AssemblyName>
  <NoWarn>CS0414;CS0169;CS0649;CS0219</NoWarn></PropertyGroup>
</Project>
EOF
dotnet run --project "$WORK/gate.csproj" -v q --nologo
