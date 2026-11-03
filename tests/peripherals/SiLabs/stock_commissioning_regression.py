"""Exercise real chip-tool/PASE and a native OT-simulation leader in one container.

All processes, sockets and controller storage belong to the disposable container.
The supplied firmware is mounted read-only. A PASE-only pass is not commissioning.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
import uuid


DATASET = '0e080000000000010000000300000f35060004001fffe0020811111111222222220708fd61f77bd3df233e051000112233445566778899aabbccddeeff030e4f70656e54687265616444656d6f010212340410445f2b5ca6f2a93a55ce570a70efeecb0c0402a0fff8'


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--runtime', type=Path, required=True)
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--chip-tool', type=Path, required=True)
    parser.add_argument('--ot-cli', type=Path)
    parser.add_argument('--platform', default='platforms/boards/silabs/brd2601b.repl')
    parser.add_argument('--load-base', type=lambda s: int(s, 0), default=0x08006000)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--pase-only', action='store_true')
    parser.add_argument('--image', default='renode-matter-groundtruth:latest')
    parser.add_argument('--passcode', type=int, default=20202021)
    parser.add_argument('--discriminator', type=int, default=3840)
    args = parser.parse_args()
    if not re.fullmatch(r'[\w./-]+\.repl', args.platform) or '..' in Path(args.platform).parts or Path(args.platform).is_absolute():
        parser.error('platform must be a relative .repl path')
    if not args.pase_only and args.ot_cli is None:
        parser.error('full pairing requires --ot-cli')
    root, elf, chip = (p.resolve(strict=True) for p in (args.runtime, args.elf, args.chip_tool))
    if not elf.read_bytes().startswith(b'\x7fELF'):
        parser.error('a nonempty, unmodified ELF is required')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for path in out.glob('*.log'):
        path.unlink()
    scenario = f'''mach create "commissioning"
machine LoadPlatformDescription @{args.platform}
machine LoadPlatformDescription @matter/renode-thread/scenarios/phase3-bridges.repl
sysbus LoadELF @/inputs/device.elf
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
sysbus.radio HostMailboxDoorbellCompensationEnabled true
start
'''
    (out / 'boot.resc').write_text(scenario)
    name = 'renode-commissioning-' + uuid.uuid4().hex[:12]
    network = name + '-net'
    command = ['docker', 'run', '--rm', '--name', name, '--user', '0', '--memory', '4g', '--pids-limit', '512', '-w', '/work',
               '-v', f'{root}:/work:ro', '-v', f'{elf}:/inputs/device.elf:ro', '-v', f'{chip}:/host-tools/chip-tool:ro',
               '--network', network, '-v', f'{out}:/evidence']
    if args.ot_cli:
        command += ['-v', f'{args.ot_cli.resolve(strict=True)}:/host-tools/ot-cli-ftd:ro']
    command += [args.image, './renode', '--disable-xwt', '--hide-log', '--port', '3456', '/evidence/boot.resc']
    monitor_program = '''import socket,time,sys
for _ in range(100):
 try: s=socket.create_connection(('127.0.0.1',3456),0.2);break
 except OSError: time.sleep(0.1)
else: raise RuntimeError('monitor unavailable')
s.settimeout(0.2)
while True:
 try:
  data=s.recv(65536)
  if not data:break
  sys.stdout.buffer.write(data);sys.stdout.buffer.flush()
 except socket.timeout:pass
'''
    started = time.monotonic()
    status, pairing_code = 'BOOT_TIMEOUT', None
    processes, streams = [], []
    def stream(filename):
        handle = (out / filename).open('w')
        streams.append(handle)
        return handle
    def read(filename):
        path = out / filename
        return path.read_text(errors='replace') if path.exists() else ''
    try:
        subprocess.run(['docker', 'network', 'create', '--internal', network], check=True, capture_output=True)
        renode = subprocess.Popen(command, stdout=stream('stdout.log'), stderr=subprocess.STDOUT)
        processes.append(renode)
        for _ in range(100):
            running = subprocess.run(['docker', 'inspect', '--format', '{{.State.Running}}', name], capture_output=True)
            if running.returncode == 0 and running.stdout.strip() == b'true':
                break
            if renode.poll() is not None:
                raise RuntimeError('Renode container exited; inspect stdout.log')
            time.sleep(0.1)
        monitor = subprocess.Popen(['docker', 'exec', name, 'python3', '-u', '-c', monitor_program], stdout=stream('monitor.log'), stderr=subprocess.STDOUT)
        processes.append(monitor)
        while time.monotonic() - started < 70 and renode.poll() is None:
            if 'CHIPoBLE GATT ready' in read('renode.log'):
                break
            if 'There was an error' in read('monitor.log'):
                raise RuntimeError('Scenario failed; inspect monitor.log')
            time.sleep(0.2)
        else:
            raise RuntimeError('Firmware did not reach GATT within 70s')
        if args.ot_cli:
            leader = subprocess.Popen(['docker', 'exec', '-i', '-w', '/tmp', name, '/host-tools/ot-cli-ftd', '1'], stdin=subprocess.PIPE,
                                      stdout=stream('leader.log'), stderr=subprocess.STDOUT, text=True)
            processes.append(leader)
            for line in (f'dataset set active {DATASET}', 'ifconfig up', 'thread start'):
                leader.stdin.write(line + '\n'); leader.stdin.flush(); time.sleep(0.5)
            for _ in range(20):
                leader.stdin.write('state\n'); leader.stdin.flush(); time.sleep(0.25)
                if re.search(r'(?m)^(?:>\s*)*leader\s*$', read('leader.log')):
                    break
            else:
                raise RuntimeError('OT simulation node did not become leader')
        pairing = ['docker', 'exec', '-e', 'CHIP_FAKE_BLE_PORT=3500', '-e', 'CHIP_BLE_BTP_CONN_RSP_TIMEOUT_MS=90000',
                   '-e', 'CHIP_BLE_BTP_ACK_TIMEOUT_MS=90000', name, '/host-tools/chip-tool', 'pairing', 'ble-thread',
                   '1', 'hex:' + DATASET, str(args.passcode), str(args.discriminator), '--bypass-attestation-verifier', 'true',
                   '--timeout', str(args.timeout)]
        if args.pase_only:
            pairing += ['--pase-only', '1']
        client = subprocess.Popen(pairing, stdout=stream('pairing.log'), stderr=subprocess.STDOUT)
        processes.append(client)
        try:
            pairing_code = client.wait(timeout=args.timeout + 15)
        except subprocess.TimeoutExpired:
            status = 'PAIRING_TIMEOUT'
        else:
            log = read('pairing.log')
            marker = 'PASE establishment successful' if args.pase_only else 'Device commissioning completed with success'
            status = 'PASE_PASS' if args.pase_only and pairing_code == 0 and marker in log else 'PAIRING_FAILED'
            if not args.pase_only and pairing_code == 0 and marker in log:
                status = 'COMMISSIONING_PASS'
        if args.ot_cli and leader.poll() is None:
            leader.stdin.write('state\nchild table\nneighbor table\n')
            leader.stdin.flush()
            time.sleep(0.5)
    except Exception as error:
        (out / 'harness-error.log').write_text(str(error) + '\n')
        status = 'HARNESS_OR_BOOT_ERROR'
    finally:
        subprocess.run(['docker', 'stop', '-t', '2', name], capture_output=True)
        for process in processes:
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for handle in streams:
            handle.close()
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True)
    log = read('pairing.log')
    result = {'status': status, 'elapsed_seconds': round(time.monotonic() - started, 2), 'pairing_returncode': pairing_code,
              'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest(), 'chip_tool_sha256': hashlib.sha256(chip.read_bytes()).hexdigest(),
              'platform': args.platform, 'PASE_established': 'PASE establishment successful' in log,
              'network_enable_observed': 'NetworkEnable' in log or 'Network commissioning completed' in log,
              'hostmailbox_compensation': True, 'BTP_timeout_overrides_ms': 90000, 'pase_only': args.pase_only,
              'scope': 'Real unmodified firmware and native chip-tool; no firmware hooks; full commissioning excludes Invoke/Read unless separately tested'}
    (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return 0 if status in ('PASE_PASS', 'COMMISSIONING_PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
