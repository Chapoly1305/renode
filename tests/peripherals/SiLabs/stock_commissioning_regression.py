"""Exercise real chip-tool/PASE and native Thread tools in one container.

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
    runtime_choice = parser.add_mutually_exclusive_group(required=True)
    runtime_choice.add_argument('--runtime', type=Path)
    runtime_choice.add_argument('--bundled-image', help='Image containing /opt/renode and /host-tools')
    parser.add_argument('--elf', type=Path, required=True)
    parser.add_argument('--chip-tool', type=Path)
    parser.add_argument('--ot-cli', type=Path)
    parser.add_argument('--otbr', type=Path)
    parser.add_argument('--ot-ctl', type=Path)
    parser.add_argument('--ot-rcp', type=Path)
    parser.add_argument('--platform', default='platforms/boards/silabs/brd2601b.repl')
    parser.add_argument('--load-base', type=lambda s: int(s, 0), default=0x08006000)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--leader-timeout', type=int, default=45)
    parser.add_argument('--pase-only', action='store_true')
    parser.add_argument('--image', default='renode-matter-groundtruth:latest')
    parser.add_argument('--passcode', type=int, default=20202021)
    parser.add_argument('--discriminator', type=int, default=3840)
    parser.add_argument('--hostmailbox-compensation', choices=('on', 'off', 'default'), default='on')
    args = parser.parse_args()
    if args.timeout <= 0 or args.leader_timeout <= 0:
        parser.error('timeouts must be positive')
    if not re.fullmatch(r'[\w./-]+\.repl', args.platform) or '..' in Path(args.platform).parts or Path(args.platform).is_absolute():
        parser.error('platform must be a relative .repl path')
    if any((args.otbr, args.ot_ctl, args.ot_rcp)) and not all((args.otbr, args.ot_ctl, args.ot_rcp)):
        parser.error('--otbr, --ot-ctl and --ot-rcp must be supplied together')
    if args.otbr and args.ot_cli:
        parser.error('choose either --otbr or --ot-cli')
    if args.bundled_image and any((args.chip_tool, args.ot_cli, args.otbr, args.ot_ctl, args.ot_rcp)):
        parser.error('a bundled image already contains its host tools')
    if not args.bundled_image and args.chip_tool is None:
        parser.error('--runtime requires --chip-tool')
    use_otbr = bool(args.otbr or (args.bundled_image and not args.pase_only))
    if not args.pase_only and not (args.ot_cli or use_otbr):
        parser.error('full pairing requires --ot-cli or the three OTBR tools')
    root = args.runtime.resolve(strict=True) if args.runtime else None
    elf = args.elf.resolve(strict=True)
    chip = args.chip_tool.resolve(strict=True) if args.chip_tool else None
    chip_sha = hashlib.sha256(chip.read_bytes()).hexdigest() if chip else None
    if not elf.read_bytes().startswith(b'\x7fELF'):
        parser.error('a nonempty, unmodified ELF is required')
    out = args.output.resolve()
    out.mkdir(parents=True, exist_ok=True)
    for path in out.glob('*.log'):
        path.unlink()
    mailbox_command = '' if args.hostmailbox_compensation == 'default' else (
        'sysbus.radio HostMailboxDoorbellCompensationEnabled ' + ('true' if args.hostmailbox_compensation == 'on' else 'false'))
    scenario = f'''mach create "commissioning"
machine LoadPlatformDescription @{args.platform}
machine LoadPlatformDescription @matter/renode-thread/scenarios/phase3-bridges.repl
sysbus LoadELF @/inputs/device.elf
cpu VectorTableOffset {args.load_base:#x}
macro reset
"""
    cpu VectorTableOffset {args.load_base:#x}
"""
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
{mailbox_command}
start
'''
    (out / 'boot.resc').write_text(scenario)
    name = 'renode-commissioning-' + uuid.uuid4().hex[:12]
    network = name + '-net'
    command = ['docker', 'run', '--rm', '--name', name, '--user', '0', '--memory', '4g', '--pids-limit', '512',
               '-w', '/work' if root else '/opt/renode', '-v', f'{elf}:/inputs/device.elf:ro',
               '--network', network, '-v', f'{out}:/evidence']
    if root:
        command += ['-v', f'{root}:/work:ro', '-v', f'{chip}:/host-tools/chip-tool:ro']
    if args.ot_cli:
        command += ['-v', f'{args.ot_cli.resolve(strict=True)}:/host-tools/ot-cli-ftd:ro']
    if use_otbr:
        command += ['--cap-add', 'NET_ADMIN', '--device', '/dev/net/tun',
                    '--sysctl', 'net.ipv6.conf.all.forwarding=1']
        if not args.bundled_image:
            for binary, path in (('otbr-agent', args.otbr), ('ot-ctl', args.ot_ctl), ('ot-rcp', args.ot_rcp)):
                command += ['-v', f'{path.resolve(strict=True)}:/host-tools/{binary}:ro']
    command += [args.bundled_image or args.image, './renode', '--disable-xwt', '--hide-log', '--port', '3456', '/evidence/boot.resc']
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
    operational = {}
    def stream(filename):
        handle = (out / filename).open('w')
        streams.append(handle)
        return handle
    def read(filename):
        path = out / filename
        return path.read_text(errors='replace') if path.exists() else ''
    def ctl(*arguments, check=True):
        response = subprocess.run(['docker', 'exec', name, '/host-tools/ot-ctl', '-I', 'wpan0', *arguments],
                                  capture_output=True, text=True, timeout=10)
        with (out / 'otbr-ctl.log').open('a') as log:
            log.write('> ' + ' '.join(arguments) + '\n' + response.stdout + response.stderr)
        if check and (response.returncode != 0 or re.search(r'(?m)^Error \d+:', response.stdout)):
            raise RuntimeError('ot-ctl failed: ' + ' '.join(arguments))
        return response.stdout
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
        if chip_sha is None:
            chip_sha = subprocess.check_output(['docker', 'exec', name, 'sha256sum', '/host-tools/chip-tool'], text=True).split()[0]
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
        if use_otbr:
            plumbing = [
                ['ip', 'link', 'set', 'lo', 'multicast', 'on'],
                ['ip', 'link', 'add', 'infra0', 'type', 'dummy'],
                ['ip', 'link', 'set', 'infra0', 'up', 'multicast', 'on'],
                ['ip', '-6', 'addr', 'add', 'fd00:abcd::1/64', 'dev', 'infra0', 'nodad'],
                ['mkdir', '-p', '/tmp/otbr-state'],
            ]
            for instruction in plumbing:
                subprocess.run(['docker', 'exec', name, *instruction], check=True, capture_output=True)
            agent = subprocess.Popen(['docker', 'exec', '-w', '/tmp', name, '/host-tools/otbr-agent',
                                      '-I', 'wpan0', '-B', 'infra0', '-d7', '-v', '-s',
                                      '--vendor-name', 'Renode', '--model-name', 'RenodeSim',
                                      '--data-path', '/tmp/otbr-state',
                                      'spinel+hdlc+forkpty:///host-tools/ot-rcp?forkpty-arg=3'],
                                     stdout=stream('otbr-agent.log'), stderr=subprocess.STDOUT)
            processes.append(agent)
            for _ in range(100):
                if agent.poll() is not None:
                    raise RuntimeError('OTBR agent exited; inspect otbr-agent.log')
                if re.search(r'(?m)^disabled\s*$', ctl('state', check=False)):
                    break
                time.sleep(0.1)
            else:
                raise RuntimeError('OTBR control socket did not become ready')
            for instruction in (['dataset', 'set', 'active', DATASET], ['leaderweight', '128'],
                                ['routerselectionjitter', '1'], ['ifconfig', 'up'], ['thread', 'start']):
                ctl(*instruction)
            election_deadline = time.monotonic() + args.leader_timeout
            while time.monotonic() < election_deadline:
                if re.search(r'(?m)^leader\s*$', ctl('state')):
                    break
                time.sleep(0.5)
            else:
                raise RuntimeError('OTBR did not become leader')
            for instruction in (['srp', 'server', 'enable'],
                                ['prefix', 'add', 'fd0d:b0b0:cafe:1::/64', 'paos', 'med'], ['netdata', 'register']):
                ctl(*instruction)
        if args.ot_cli:
            leader = subprocess.Popen(['docker', 'exec', '-i', '-w', '/tmp', name, '/host-tools/ot-cli-ftd', '1'], stdin=subprocess.PIPE,
                                      stdout=stream('leader.log'), stderr=subprocess.STDOUT, text=True)
            processes.append(leader)
            for line in (f'dataset set active {DATASET}', 'ifconfig up', 'thread start'):
                leader.stdin.write(line + '\n'); leader.stdin.flush(); time.sleep(0.5)
            election_deadline = time.monotonic() + args.leader_timeout
            while time.monotonic() < election_deadline:
                if leader.poll() is not None:
                    raise RuntimeError('OT simulation node exited; inspect leader.log')
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
        if use_otbr:
            for instruction in (['state'], ['child', 'table'], ['neighbor', 'table'], ['router', 'table'],
                                ['netdata', 'show'], ['srp', 'server', 'host'], ['srp', 'server', 'service']):
                ctl(*instruction, check=False)
            if status == 'COMMISSIONING_PASS':
                for label, instruction in (
                    ('read_before', ['onoff', 'read', 'on-off', '1', '1']),
                    ('invoke_toggle', ['onoff', 'toggle', '1', '1']),
                    ('read_after', ['onoff', 'read', 'on-off', '1', '1']),
                ):
                    operation = subprocess.Popen(['docker', 'exec', name, '/host-tools/chip-tool', *instruction],
                                                 stdout=stream(label + '.log'), stderr=subprocess.STDOUT)
                    processes.append(operation)
                    try:
                        code = operation.wait(timeout=60)
                    except subprocess.TimeoutExpired:
                        code = None
                    operational[label] = {'returncode': code, 'command': instruction}
                    if code is None:
                        break
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
    before = re.findall(r'OnOff: (TRUE|FALSE)', read('read_before.log'))
    after = re.findall(r'OnOff: (TRUE|FALSE)', read('read_after.log'))
    operational_verified = (len(operational) == 3 and all(v['returncode'] == 0 for v in operational.values())
                            and len(before) == len(after) == 1 and before[0] != after[0])
    commissioning_verified = status == 'COMMISSIONING_PASS'
    if commissioning_verified and use_otbr:
        status = 'FULL_PASS' if operational_verified else 'OPERATIONAL_FAILED'
    result = {'status': status, 'elapsed_seconds': round(time.monotonic() - started, 2), 'pairing_returncode': pairing_code,
              'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest(), 'chip_tool_sha256': chip_sha,
              'platform': args.platform, 'PASE_established': 'PASE establishment successful' in log,
              'network_enable_started': "Starting commissioning stage 'ThreadNetworkEnable'" in log,
              'network_enable_completed': "Successfully finished commissioning step 'ThreadNetworkEnable'" in log,
              'UDP_CASE_Sigma2_received': 'Received Sigma2 msg' in log,
              'hostmailbox_compensation': args.hostmailbox_compensation, 'BTP_timeout_overrides_ms': 90000, 'pase_only': args.pase_only,
              'thread_host': 'OTBR' if use_otbr else ('CLI' if args.ot_cli else None), 'operational': operational,
              'image': args.bundled_image or args.image, 'commissioning_verified': commissioning_verified,
              'onoff_before': before, 'onoff_after': after, 'operational_verified': operational_verified,
              'scope': 'Unmodified firmware, native chip-tool, real OTBR/TUN and UDP CASE. FULL_PASS requires successful commissioning plus Read/Toggle/Read with an observed state change. Hostmailbox and timeout compensation remain explicit.'}
    (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return 0 if status in ('PASE_PASS', 'COMMISSIONING_PASS', 'FULL_PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
