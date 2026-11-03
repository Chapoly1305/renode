"""Compare uninstrumented production-image boots in isolated Docker containers.

Run on the host. Pass --runtime NAME=/path/to/renode once per runtime, and
--elf /path/to/nonempty.elf. Requires the image built by docker/Dockerfile.build.
This checks GATT readiness only; it does not claim commissioning or fidelity.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--runtime", action="append", required=True, metavar="NAME=PATH")
    parser.add_argument("--elf", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timeout", type=float, default=70)
    parser.add_argument("--board", default="brd2601b")
    parser.add_argument("--platform", type=Path, help="Platform path relative to each runtime root")
    parser.add_argument("--overlay", action="append", type=Path, default=[], help="Additive platform path relative to each runtime root")
    parser.add_argument("--load-base", type=lambda x: int(x, 0), default=0x08006000)
    parser.add_argument("--image", default="local/renode-build:silabs-26q3")
    parser.add_argument("--hostmailbox-compensation", action="store_true",
                        help="Opt into the stock-harness masked-doorbell compensation; invalid for IRQ/timing fidelity claims")
    args = parser.parse_args()
    elf = args.elf.resolve(strict=True)
    data = elf.read_bytes()
    if not data.startswith(b"\x7fELF"):
        parser.error("--elf must contain a real ELF; empty workspace placeholders are invalid")
    if not re.fullmatch(r"[\w-]+", args.board):
        parser.error("invalid board name")
    args.output = args.output.resolve()
    args.output.mkdir(parents=True, exist_ok=True)
    results = []
    for specification in args.runtime:
        label, root = specification.split("=", 1)
        if not re.fullmatch(r"[\w-]+", label):
            parser.error("runtime label must contain letters, numbers, hyphens or underscores")
        root = Path(root).resolve(strict=True)
        platform = args.platform or Path(f"platforms/boards/silabs/{args.board}.repl")
        platforms = [platform, *args.overlay]
        for path in platforms:
            if path.is_absolute() or not re.fullmatch(r"[\w./-]+\.repl", str(path)) or '..' in path.parts:
                parser.error("platform paths must be relative .repl paths without parent traversal")
            if not (root / path).resolve(strict=True).is_relative_to(root):
                parser.error("platform must resolve inside the runtime root")
        if args.overlay:
            # Merge registration cancellations before instantiation. Loading a
            # second description cannot cancel an already registered device.
            platform_commands = 'machine LoadPlatformDescriptionFromString """\n' + '\n'.join(f'using "{path}"' for path in platforms) + '\n"""'
        else:
            platform_commands = f'machine LoadPlatformDescription @{platform}'
        case = args.output / label
        case.mkdir(exist_ok=True)
        for old in ("renode.log", "stdout.log", "monitor.log"):
            (case / old).unlink(missing_ok=True)
        compensation_command = "sysbus.radio HostMailboxDoorbellCompensationEnabled true" if args.hostmailbox_compensation else ""
        (case / "boot.resc").write_text(f'''mach create "deployed"
{platform_commands}
machine LoadPlatformDescription @matter/renode-thread/scenarios/phase3-bridges.repl
sysbus LoadELF @/workspace/device.elf
cpu VectorTableOffset {args.load_base:#x}
cpu PerformanceInMips 39
seqcpu PerformanceInMips 39
emulation CreateBLEMedium "wireless"
connector Connect sysbus.radio wireless
connector Connect sysbus.bleCentral wireless
connector Connect sysbus.ieee802154 wireless
emulation SetGlobalSerialExecution true
emulation SetGlobalAdvanceImmediately false
logFile @/evidence/renode.log true
logLevel 2
{compensation_command}
start
''')
        name = "renode-regression-" + uuid.uuid4().hex[:12]
        command = ["docker", "run", "--rm", "--name", name, "--memory", "3g",
                   "--pids-limit", "512", "-w", "/work", "-v", f"{root}:/work:ro",
                   "-v", f"{elf}:/workspace/device.elf:ro", "-v", f"{case}:/evidence",
                   args.image, "./renode", "--disable-xwt", "--hide-log", "--port",
                   "3456", "/evidence/boot.resc"]
        # Renode's telnet monitor initializes the startup script on connection.
        connect = '''import socket,time,sys
for i in range(100):
 try:
  s=socket.create_connection(("127.0.0.1",3456),0.2);break
 except OSError: time.sleep(0.1)
else: raise RuntimeError("monitor did not become available")
s.settimeout(0.2)
while True:
 try:
  data=s.recv(65536)
  if not data: break
  sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
 except socket.timeout: pass
'''
        start = time.monotonic()
        status = "TIMEOUT"
        with (case / "stdout.log").open("w") as stdout, (case / "monitor.log").open("w") as monitor:
            proc = subprocess.Popen(command, stdout=stdout, stderr=subprocess.STDOUT)
            connector = None
            try:
                # The container must exist before docker exec; retry existence,
                # while the connector itself waits for the monitor to listen.
                while time.monotonic() - start < 10 and proc.poll() is None:
                    exists = subprocess.run(["docker", "inspect", "--format", "{{.State.Running}}", name], capture_output=True)
                    if exists.returncode == 0 and exists.stdout.strip() == b"true":
                        connector = subprocess.Popen(["docker", "exec", name, "python3", "-u", "-c", connect], stdout=monitor, stderr=subprocess.STDOUT)
                        break
                    time.sleep(0.1)
                while time.monotonic() - start < args.timeout and proc.poll() is None:
                    log = (case / "renode.log").read_text(errors="replace") if (case / "renode.log").exists() else ""
                    if "CHIPoBLE GATT ready" in log:
                        status = "PASS"
                        break
                    if "There was an error" in (case / "monitor.log").read_text(errors="replace"):
                        status = "SCENARIO_ERROR"
                        break
                    time.sleep(0.25)
                if proc.poll() is not None and status != "PASS":
                    status = "PROCESS_EXITED"
                elapsed = time.monotonic() - start
            finally:
                subprocess.run(["docker", "stop", "-t", "2", name], capture_output=True)
                proc.wait(timeout=10)
                if connector is not None:
                    connector.wait(timeout=10)
        result = {"runtime": label, "root": str(root), "status": status,
                  "elapsed_seconds": round(elapsed, 2), "elf": str(elf),
                  "elf_sha256": hashlib.sha256(data).hexdigest(), "board": args.board,
                  "platform": str(platform), "overlays": [str(p) for p in args.overlay],
                  "board_identity_confirmed": False, "load_base": hex(args.load_base),
                  "timeout_seconds": args.timeout, "terminated_by_harness": True,
                  "hostmailbox_compensation_enabled": args.hostmailbox_compensation,
                  "docker_exit_after_cleanup": proc.returncode,
                  "scope": "Boot to GATT; no firmware hooks or commissioning"}
        results.append(result)
        (args.output / "results.json").write_text(json.dumps(results, indent=2) + "\n")
        print(json.dumps(result), flush=True)
    return 0 if all(result["status"] == "PASS" for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
