"""Exercise stock firmware with real chip-tool PASE reads or Thread/CASE.

All processes, sockets and controller storage belong to the disposable container.
The supplied firmware is mounted read-only. PASE reads are not commissioning;
only discovered OnOff servers are candidates for Read/Toggle/Read.
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
ONOFF_CLUSTER_ID = 0x0006
MAX_ENDPOINTS = 32  # Bound untrusted Descriptor fan-out in an automated qualification run.
FAILURE_MARKERS = ('Run command failure:', 'Response Failure:', 'Error: ', 'InitArgs:')


def platform_description(platform, overlays):
    if not overlays:
        return f'machine LoadPlatformDescription @{platform}'
    return ('machine LoadPlatformDescriptionFromString """\n' +
            '\n'.join(f'using "{path}"' for path in [platform, *overlays]) + '\n"""')


def command_succeeded(log, completed=True):
    """Interactive commands have no per-command process return code; require the next prompt."""
    return completed and not any(marker in log for marker in FAILURE_MARKERS)


def parse_scalar(log, label):
    """Return one reported unsigned 32-bit attribute value, never a build-time constant."""
    values = re.findall(r'\b' + re.escape(label) + r':\s*(0x[0-9a-fA-F_]+|[0-9]+)\b', log)
    if len(values) != 1:
        return None
    value = int(values[0].replace('_', ''), 0 if values[0].startswith('0x') else 10)
    return value if 0 <= value <= 0xffffffff else None


def parse_id_list(log, label):
    """Parse a complete chip-tool Descriptor list (including an empty list)."""
    header = re.findall(r'\b' + re.escape(label) + r':\s*(\d+) entries\b', log)
    if len(header) != 1:
        return None
    count = int(header[0])
    if count > MAX_ENDPOINTS and label == 'PartsList':
        return None
    # List elements have one-based indexes and may append a cluster name in parentheses.
    tail = log[re.search(r'\b' + re.escape(label) + r':\s*\d+ entries\b', log).end():]
    items = re.findall(r'\[(\d+)\]:\s*(0x[0-9a-fA-F_]+|\d+)\b', tail)
    if len(items) < count or any(int(index) != i for i, (index, _) in enumerate(items[:count], 1)):
        return None
    return [int(value.replace('_', ''), 0 if value.startswith('0x') else 10) for _, value in items[:count]]


def onoff_endpoint(parts, servers):
    """Only a non-root endpoint *listed by Descriptor* with OnOff is applicable."""
    if parts is None or any(endpoint <= 0 or endpoint >= 0xffff for endpoint in parts) or len(parts) != len(set(parts)):
        return None
    if set(parts) != set(servers) or any(clusters is None for clusters in servers.values()):
        return None
    return next((endpoint for endpoint in sorted(parts) if ONOFF_CLUSTER_ID in servers[endpoint]), None)


def wait_for_prompt(process, log_path, timeout, start=0, marker=None):
    """Wait for the *next* editline prompt, after a command's own echo if provided."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        text = log_path.read_text(errors='replace') if log_path.exists() else ''
        chunk = text[start:]
        echoed = chunk.find(marker) if marker is not None else 0
        if echoed >= 0:
            prompt = chunk.find('>>> ', echoed + len(marker) if marker else 0)
            if prompt >= 0:
                return chunk[:prompt], True
        if process.poll() is not None:
            break
        time.sleep(0.1)
    text = log_path.read_text(errors='replace') if log_path.exists() else ''
    return text[start:], False


def interactive_command(process, log_path, arguments, timeout):
    start = len(log_path.read_text(errors='replace'))
    line = ' '.join(arguments)
    process.stdin.write(line + '\n')
    process.stdin.flush()
    return wait_for_prompt(process, log_path, timeout, start, 'Command: ' + line)


def pairing_evidence(log):
    """Markers describe observed protocol stages, not inferred firmware identity."""
    return {
        'PASE_established': 'PASE establishment successful' in log,
        'network_enable_started': "Starting commissioning stage 'ThreadNetworkEnable'" in log,
        'network_enable_completed': "Successfully finished commissioning step 'ThreadNetworkEnable'" in log,
        'UDP_CASE_Sigma2_received': 'Received Sigma2 msg' in log,
        'CASE_established': 'CASE establishment successful' in log,
    }


def classify_status(pairing_status, reads_verified, applicability, operational_verified):
    if pairing_status not in ('PASE_PASS', 'COMMISSIONING_PASS'):
        return pairing_status
    if not reads_verified:
        return 'PASE_READ_FAILED' if pairing_status == 'PASE_PASS' else 'ATTRIBUTE_READ_FAILED'
    if pairing_status == 'PASE_PASS':
        return 'PASE_PASS'  # Legacy name; now requires verified PASE attribute reads.
    if applicability in ('NOT_APPLICABLE', 'APPLICABLE_NOT_TESTED'):
        return 'FULL_READ_PASS'  # Commissioning + reads; no operational command claim.
    if applicability == 'APPLICABLE':
        return 'FULL_PASS' if operational_verified else 'OPERATIONAL_FAILED'
    return 'DESCRIPTOR_UNRESOLVED'


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
    parser.add_argument('--overlay', action='append', default=[],
                        help='Additive .repl path relative to the runtime; merged before instantiation')
    parser.add_argument('--load-base', type=lambda s: int(s, 0), default=0x08006000)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--timeout', type=int, default=180)
    parser.add_argument('--leader-timeout', type=int, default=45)
    parser.add_argument('--read-timeout', type=int, default=60)
    parser.add_argument('--pase-only', action='store_true')
    parser.add_argument('--image', default='renode-matter-groundtruth:latest')
    parser.add_argument('--passcode', type=int, default=20202021)
    parser.add_argument('--discriminator', type=int, default=3840)
    parser.add_argument('--hostmailbox-compensation', choices=('on', 'off', 'default'), default='on')
    args = parser.parse_args()
    if args.timeout <= 0 or args.leader_timeout <= 0 or args.read_timeout <= 0:
        parser.error('timeouts must be positive')
    platforms = [args.platform, *args.overlay]
    for path in platforms:
        if not re.fullmatch(r'[\w./-]+\.repl', path) or '..' in Path(path).parts or Path(path).is_absolute():
            parser.error('platform/overlay must be relative .repl paths without traversal')
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
    if root:
        for platform in platforms:
            if not (root / platform).resolve(strict=True).is_relative_to(root):
                parser.error('platform/overlay must resolve inside the runtime root')
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
    # Merging via `using` lets overlay registrations cancel board registrations.
    platform_command = platform_description(args.platform, args.overlay)
    scenario = f'''mach create "commissioning"
{platform_command}
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
    command = ['docker', 'run', '--rm', '--name', name, '--user', '0', '--memory', '3g', '--pids-limit', '512',
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
    operational, attribute_reads = {}, {}
    endpoint_servers = {}
    applicability, selected_endpoint = 'UNRESOLVED', None
    pairing_status = 'NOT_RUN'
    reads_verified = False
    read_sequence_valid = True
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
    def chip_read(label, filename, arguments, value_label, list_value=False, session=None):
        """Collect a dedicated log and only promote a successfully decoded response."""
        nonlocal read_sequence_valid
        if not read_sequence_valid:
            attribute_reads[label] = {'status': 'NOT_RUN', 'value': None,
                                      'command': arguments, 'returncode': None, 'log_file': None}
            return None
        if session is not None:
            if session.poll() is not None:
                read_sequence_valid = False
                attribute_reads[label] = {'status': 'SESSION_EXITED', 'value': None,
                                          'command': arguments, 'returncode': None, 'log_file': None}
                return None
            try:
                text, completed = interactive_command(session, out / 'interactive.log', arguments, args.read_timeout + 10)
            except BrokenPipeError:
                read_sequence_valid = False
                attribute_reads[label] = {'status': 'SESSION_EXITED', 'value': None,
                                          'command': arguments, 'returncode': None, 'log_file': None}
                return None
            (out / filename).write_text(text)
            code = None  # No subprocess exit code for an interactive subcommand.
            success = command_succeeded(text, completed)
        else:
            operation = subprocess.Popen(['docker', 'exec', name, '/host-tools/chip-tool', *arguments],
                                         stdout=stream(filename), stderr=subprocess.STDOUT)
            processes.append(operation)
            try:
                code = operation.wait(timeout=args.read_timeout + 10)
            except subprocess.TimeoutExpired:
                code = None
            text = read(filename)
            completed = code is not None
            success = code == 0 and command_succeeded(text)
        if not completed:
            # Never queue a second command on a stalled interactive PASE
            # session (or start another chip-tool against a timed-out read).
            read_sequence_valid = False
        value = (parse_id_list(text, value_label) if list_value else parse_scalar(text, value_label)) if success else None
        record = {'status': 'PASS' if value is not None else ('TIMEOUT' if not completed else 'FAILED'),
                  'value': value, 'command': arguments, 'returncode': code, 'log_file': filename}
        attribute_reads[label] = record
        return value
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
        chip_exec = ['docker', 'exec', '-e', 'CHIP_FAKE_BLE_PORT=3500',
                     '-e', 'CHIP_BLE_BTP_CONN_RSP_TIMEOUT_MS=90000',
                     '-e', 'CHIP_BLE_BTP_ACK_TIMEOUT_MS=90000']
        pairing_args = ['pairing', 'ble-thread', '1', 'hex:' + DATASET,
                        str(args.passcode), str(args.discriminator),
                        '--bypass-attestation-verifier', 'true', '--timeout', str(args.timeout)]
        if args.pase_only:
            # The same chip-tool process retains GetDeviceBeingCommissioned's PASE
            # proxy for subsequent reads; exiting chip-tool discards that session.
            client = subprocess.Popen([*chip_exec[:2], '-i', *chip_exec[2:], '-w', '/tmp', name,
                                       '/host-tools/chip-tool', 'interactive', 'start'],
                                      stdin=subprocess.PIPE, stdout=stream('interactive.log'),
                                      stderr=subprocess.STDOUT, text=True)
            processes.append(client)
            _, ready = wait_for_prompt(client, out / 'interactive.log', 15)
            if not ready:
                raise RuntimeError('chip-tool interactive prompt unavailable; inspect interactive.log')
            pairing_text, completed = interactive_command(client, out / 'interactive.log',
                                                          [*pairing_args, '--pase-only', '1'], args.timeout + 15)
            (out / 'pairing.log').write_text(pairing_text)
            pairing_status = ('PASE_PASS' if command_succeeded(pairing_text, completed) and
                              'PASE establishment successful' in pairing_text else
                              'PAIRING_TIMEOUT' if not completed and client.poll() is None else 'PAIRING_FAILED')
            # pairing_returncode stays null: interactive subcommands do not exit.
        else:
            client = subprocess.Popen([*chip_exec, name, '/host-tools/chip-tool', *pairing_args],
                                      stdout=stream('pairing.log'), stderr=subprocess.STDOUT)
            processes.append(client)
            try:
                pairing_code = client.wait(timeout=args.timeout + 15)
            except subprocess.TimeoutExpired:
                pairing_status = 'PAIRING_TIMEOUT'
            else:
                pairing_text = read('pairing.log')
                pairing_status = ('COMMISSIONING_PASS' if pairing_code == 0 and
                                  'Device commissioning completed with success' in pairing_text else 'PAIRING_FAILED')
        status = pairing_status
        if args.ot_cli and leader.poll() is None:
            leader.stdin.write('state\nchild table\nneighbor table\n')
            leader.stdin.flush()
            time.sleep(0.5)
        if use_otbr:
            for instruction in (['state'], ['child', 'table'], ['neighbor', 'table'], ['router', 'table'],
                                ['netdata', 'show'], ['srp', 'server', 'host'], ['srp', 'server', 'service']):
                ctl(*instruction, check=False)
        if pairing_status in ('PASE_PASS', 'COMMISSIONING_PASS'):
            session = client if args.pase_only else None
            spec = chip_read('specification_version', 'basic_specification_version.log',
                             ['basicinformation', 'read', 'specification-version', '1', '0'],
                             'SpecificationVersion', session=session)
            software = chip_read('software_version', 'basic_software_version.log',
                                 ['basicinformation', 'read', 'software-version', '1', '0'],
                                 'SoftwareVersion', session=session)
            parts = chip_read('parts_list', 'descriptor_parts_list.log',
                              ['descriptor', 'read', 'parts-list', '1', '0'],
                              'PartsList', list_value=True, session=session)
            if parts is not None and len(parts) == len(set(parts)) and all(0 < ep < 0xffff for ep in parts):
                for endpoint in parts:
                    endpoint_servers[endpoint] = chip_read(
                        f'server_list_{endpoint}', f'descriptor_server_list_{endpoint}.log',
                        ['descriptor', 'read', 'server-list', '1', str(endpoint)],
                        'ServerList', list_value=True, session=session)
                if all(value is not None for value in endpoint_servers.values()):
                    selected_endpoint = onoff_endpoint(parts, endpoint_servers)
                    applicability = 'APPLICABLE' if selected_endpoint is not None else 'NOT_APPLICABLE'
            reads_verified = (spec is not None and software is not None and parts is not None and
                              applicability != 'UNRESOLVED')
            if pairing_status == 'COMMISSIONING_PASS' and not use_otbr and applicability == 'APPLICABLE':
                applicability = 'APPLICABLE_NOT_TESTED'  # CLI-only Thread path does not run operations.
            if pairing_status == 'COMMISSIONING_PASS' and use_otbr and reads_verified and selected_endpoint is not None:
                for label, instruction in (
                    ('read_before', ['onoff', 'read', 'on-off', '1', str(selected_endpoint)]),
                    ('invoke_toggle', ['onoff', 'toggle', '1', str(selected_endpoint)]),
                    ('read_after', ['onoff', 'read', 'on-off', '1', str(selected_endpoint)]),
                ):
                    operation = subprocess.Popen(['docker', 'exec', name, '/host-tools/chip-tool', *instruction],
                                                 stdout=stream(label + '.log'), stderr=subprocess.STDOUT)
                    processes.append(operation)
                    try:
                        code = operation.wait(timeout=args.read_timeout + 10)
                    except subprocess.TimeoutExpired:
                        code = None
                    operational[label] = {'returncode': code, 'command': instruction, 'log_file': label + '.log'}
                    if code != 0:
                        break
            if args.pase_only and client.poll() is None:
                client.stdin.write('quit\n'); client.stdin.flush()
                client.stdin.close()
                try:
                    client.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
    except Exception as error:
        (out / 'harness-error.log').write_text(str(error) + '\n')
        status = 'HARNESS_OR_BOOT_ERROR'
    finally:
        subprocess.run(['docker', 'stop', '-t', '2', name], capture_output=True)
        for process in processes:
            stdin = getattr(process, 'stdin', None)
            if stdin is not None and not stdin.closed:
                stdin.close()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        for handle in streams:
            handle.close()
        subprocess.run(['docker', 'network', 'rm', network], capture_output=True)
    log = read('pairing.log')
    evidence = pairing_evidence(log)
    before = re.findall(r'OnOff: (TRUE|FALSE)', read('read_before.log'))
    after = re.findall(r'OnOff: (TRUE|FALSE)', read('read_after.log'))
    operational_verified = (applicability == 'APPLICABLE' and len(operational) == 3 and
                            all(v['returncode'] == 0 and command_succeeded(read(v['log_file']))
                                for v in operational.values()) and len(before) == len(after) == 1 and
                            before[0] != after[0])
    commissioning_verified = pairing_status == 'COMMISSIONING_PASS'
    if status != 'HARNESS_OR_BOOT_ERROR':
        status = classify_status(pairing_status, reads_verified, applicability, operational_verified)
    phase_results = {
        'PASE': 'PASS' if evidence['PASE_established'] else 'NOT_OBSERVED',
        'commissioning': ('PASS' if commissioning_verified else
                          'NOT_RUN' if args.pase_only or pairing_status == 'NOT_RUN' else 'FAILED'),
        'CASE': ('PASS' if evidence['CASE_established'] else
                 'NOT_RUN' if args.pase_only or pairing_status == 'NOT_RUN' else 'NOT_OBSERVED'),
        'attribute_reads': 'PASS' if reads_verified else 'FAILED' if attribute_reads else 'NOT_RUN',
        'onoff': ('PASS' if operational_verified else 'NOT_APPLICABLE' if applicability == 'NOT_APPLICABLE'
                  else 'NOT_RUN' if not operational else 'FAILED'),
    }
    result = {'status': status, 'phase_results': phase_results,
              'elapsed_seconds': round(time.monotonic() - started, 2), 'pairing_returncode': pairing_code,
              'pairing_status': pairing_status, 'pairing_log_file': 'pairing.log',
              'elf_sha256': hashlib.sha256(elf.read_bytes()).hexdigest(), 'chip_tool_sha256': chip_sha,
              'platform': args.platform, 'overlays': args.overlay, 'docker_memory': '3g',
              **evidence,
              'hostmailbox_compensation': args.hostmailbox_compensation, 'BTP_timeout_overrides_ms': 90000,
              'pase_only': args.pase_only, 'thread_host': 'OTBR' if use_otbr else ('CLI' if args.ot_cli else None),
              'attribute_reads': attribute_reads, 'reads_verified': reads_verified,
              'onoff_applicability': applicability, 'onoff_endpoint': selected_endpoint, 'operational': operational,
              'image': args.bundled_image or args.image, 'commissioning_verified': commissioning_verified,
              'onoff_before': before, 'onoff_after': after, 'operational_verified': operational_verified,
              'scope': ('Unmodified ELF; PASE-only reads use one persistent chip-tool process, without '
                        'Thread/CASE commissioning. Full flow reads BasicInformation and Descriptor after '
                        'commissioning; FULL_PASS additionally requires a discovered OnOff server and '
                        'Read/Toggle/Read state change. FULL_READ_PASS makes no operational-control claim. '
                        'Hostmailbox compensation, test passcode, attestation bypass and timeouts remain explicit.')}
    (out / 'results.json').write_text(json.dumps(result, indent=2) + '\n')
    print(json.dumps(result), flush=True)
    return 0 if status in ('PASE_PASS', 'FULL_READ_PASS', 'FULL_PASS') else 1


if __name__ == '__main__':
    raise SystemExit(main())
