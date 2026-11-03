"""Public, firmware-free evidence checks for the stock commissioning runner."""
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch


SCRIPT = Path(__file__).with_name('stock_commissioning_regression.py')
spec = importlib.util.spec_from_file_location('stock_commissioning_regression', SCRIPT)
stock = importlib.util.module_from_spec(spec)
spec.loader.exec_module(stock)


class EvidenceTests(unittest.TestCase):
    def test_u32_versions_are_runtime_values_not_guessed(self):
        self.assertEqual(stock.parse_scalar('CHIP:TOO: SpecificationVersion: 17039360\n',
                                            'SpecificationVersion'), 17039360)
        self.assertEqual(stock.parse_scalar('CHIP:TOO: SoftwareVersion: 0x0102\n',
                                            'SoftwareVersion'), 258)
        for text in ('', 'SoftwareVersion: not-set', 'SoftwareVersion: 4294967296',
                     'SoftwareVersion: 3\nSoftwareVersion: 4'):
            self.assertIsNone(stock.parse_scalar(text, 'SoftwareVersion'))

    def test_descriptor_list_requires_all_reported_entries(self):
        parts = 'CHIP:TOO: PartsList: 2 entries\nCHIP:TOO:   [1]: 2\nCHIP:TOO:   [2]: 5\n'
        servers = 'CHIP:TOO: ServerList: 2 entries\nCHIP:TOO:   [1]: 0x0006 (OnOff)\nCHIP:TOO:   [2]: 0x0102\n'
        self.assertEqual(stock.parse_id_list(parts, 'PartsList'), [2, 5])
        self.assertEqual(stock.parse_id_list(servers, 'ServerList'), [6, 258])
        self.assertEqual(stock.parse_id_list('CHIP:TOO: PartsList: 0 entries', 'PartsList'), [])
        self.assertIsNone(stock.parse_id_list(parts.replace('[2]: 5', '[3]: 5'), 'PartsList'))
        self.assertIsNone(stock.parse_id_list(parts.replace('2 entries', '3 entries'), 'PartsList'))
        self.assertIsNone(stock.parse_id_list('CHIP:TOO: [1]: 6', 'ServerList'))

    def test_onoff_only_when_discovered_and_complete(self):
        self.assertEqual(stock.onoff_endpoint([3, 2], {3: [258], 2: [6, 29]}), 2)
        self.assertIsNone(stock.onoff_endpoint([2, 3], {2: [258], 3: [258]}))  # curtain/sensor
        self.assertIsNone(stock.onoff_endpoint([2, 3], {2: [6]}))  # missing Descriptor
        self.assertIsNone(stock.onoff_endpoint([2, 2], {2: [6]}))
        self.assertIsNone(stock.onoff_endpoint(None, {}))
        self.assertIsNone(stock.onoff_endpoint([0], {0: [6]}))
        self.assertIsNone(stock.onoff_endpoint([], {}))

    def test_pairing_markers_keep_pase_and_case_distinct(self):
        pase = stock.pairing_evidence('PASE establishment successful')
        self.assertTrue(pase['PASE_established'])
        self.assertFalse(pase['CASE_established'])
        self.assertFalse(pase['network_enable_completed'])
        full = stock.pairing_evidence("PASE establishment successful\nStarting commissioning stage 'ThreadNetworkEnable'\n"
            "Successfully finished commissioning step 'ThreadNetworkEnable'\nReceived Sigma2 msg\nCASE establishment successful")
        self.assertTrue(all(full.values()))

    def test_phase_verdicts_do_not_claim_unsupported_operations(self):
        self.assertEqual(stock.classify_status('PASE_PASS', True, 'APPLICABLE', False), 'PASE_PASS')
        self.assertEqual(stock.classify_status('PASE_PASS', False, 'UNRESOLVED', False), 'PASE_READ_FAILED')
        self.assertEqual(stock.classify_status('COMMISSIONING_PASS', True, 'NOT_APPLICABLE', False), 'FULL_READ_PASS')
        self.assertEqual(stock.classify_status('COMMISSIONING_PASS', True, 'APPLICABLE_NOT_TESTED', False), 'FULL_READ_PASS')
        self.assertEqual(stock.classify_status('COMMISSIONING_PASS', True, 'APPLICABLE', True), 'FULL_PASS')
        self.assertEqual(stock.classify_status('COMMISSIONING_PASS', True, 'APPLICABLE', False), 'OPERATIONAL_FAILED')
        self.assertEqual(stock.classify_status('COMMISSIONING_PASS', False, 'APPLICABLE', True), 'ATTRIBUTE_READ_FAILED')
        self.assertEqual(stock.classify_status('PAIRING_TIMEOUT', True, 'APPLICABLE', True), 'PAIRING_TIMEOUT')

    def test_failure_log_or_incomplete_prompt_never_promotes_a_read(self):
        self.assertFalse(stock.command_succeeded('Run command failure: Timeout'))
        self.assertFalse(stock.command_succeeded('Response Failure: UNSUPPORTED_ATTRIBUTE'))
        self.assertFalse(stock.command_succeeded('SoftwareVersion: 2', completed=False))
        self.assertTrue(stock.command_succeeded('SoftwareVersion: 2'))

    def test_live_non_tty_without_prompt_is_not_pase_success(self):
        # Reproduces the first real smoke's failure mode: initialization logs
        # appear but libedit suppresses >>> when stdin/stdout are ordinary pipes.
        fake = "import sys; print('CHIP task running', flush=True); sys.stdin.readline()"
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'interactive.log'
            with log.open('w') as output:
                proc = subprocess.Popen([sys.executable, '-u', '-c', fake],
                                        stdin=subprocess.PIPE, stdout=output, text=True)
                try:
                    text, ready = stock.wait_for_prompt(proc, log, 0.3)
                    self.assertIn('CHIP task running', text)
                    self.assertFalse(ready)
                    self.assertIsNone(proc.poll())
                    self.assertFalse(stock.command_succeeded(text, ready))
                finally:
                    proc.stdin.close()
                    proc.wait(timeout=3)

    def test_process_exit_without_prompt_is_not_a_completed_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'interactive.log'
            with log.open('w') as output:
                proc = subprocess.Popen([sys.executable, '-c',
                    "print('CHIP:TOO: Command: basicinformation read software-version 1 0'); "
                    "print('CHIP:TOO: SoftwareVersion: 2')"], stdout=output)
                self.assertEqual(proc.wait(timeout=3), 0)
                text, completed = stock.wait_for_prompt(proc, log, 0.3, marker='Command: basicinformation')
                self.assertIn('SoftwareVersion: 2', text)
                self.assertFalse(completed)
                self.assertFalse(stock.command_succeeded(text, completed))

    def test_overlay_is_merged_before_instantiation(self):
        self.assertEqual(stock.platform_description('board.repl', []),
                         'machine LoadPlatformDescription @board.repl')
        self.assertEqual(stock.platform_description('board.repl', ['old-board.repl']),
                         'machine LoadPlatformDescriptionFromString """\nusing "board.repl"\nusing "old-board.repl"\n"""')

    def test_persistent_session_separates_pairing_from_actual_reads(self):
        # Fake editline output, not a mocked Matter success. The Python runner
        # must keep one process alive, wait for each prompt, and isolate logs.
        fake = '''import sys
sys.stdout.write('>>> '); sys.stdout.flush()
for line in sys.stdin:
 line=line.strip()
 if line=='quit': break
 sys.stdout.write('CHIP:TOO: Command: '+line+'\\n')
 if line.startswith('pairing '): sys.stdout.write('CHIP:TOO: PASE establishment successful\\n')
 else: sys.stdout.write('CHIP:TOO: SpecificationVersion: 17039360\\n')
 sys.stdout.write('>>> '); sys.stdout.flush()
'''
        with tempfile.TemporaryDirectory() as tmp:
            log = Path(tmp) / 'interactive.log'
            with log.open('w') as output:
                proc = subprocess.Popen([sys.executable, '-u', '-c', fake], stdin=subprocess.PIPE,
                                        stdout=output, stderr=subprocess.STDOUT, text=True)
                try:
                    _, ready = stock.wait_for_prompt(proc, log, 3)
                    self.assertTrue(ready)
                    pairing, paired = stock.interactive_command(proc, log, ['pairing', 'ble-thread', '1'], 3)
                    read, completed = stock.interactive_command(proc, log,
                        ['basicinformation', 'read', 'specification-version', '1', '0'], 3)
                    self.assertTrue(paired and completed)
                    self.assertIn('PASE establishment successful', pairing)
                    self.assertNotIn('SpecificationVersion:', pairing)
                    self.assertEqual(stock.parse_scalar(read, 'SpecificationVersion'), 17039360)
                    self.assertNotIn('PASE establishment successful', read)
                    self.assertIsNone(proc.poll())
                    proc.stdin.write('quit\n'); proc.stdin.flush()
                    self.assertEqual(proc.wait(timeout=3), 0)
                finally:
                    if proc.poll() is None:
                        proc.kill(); proc.wait()
                    proc.stdin.close()

    def test_pase_only_actual_runner_reads_in_one_interactive_process(self):
        """Simulated chip-tool prompt proves the runner sends all four commands."""
        interactive = '''import sys
if not sys.stdin.isatty(): sys.exit('editline requires a TTY')
sys.stdout.write('>>> '); sys.stdout.flush()
for line in sys.stdin:
 line=line.strip()
 if line=='quit': break
 sys.stdout.write('CHIP:TOO: Command: '+line+'\\n')
 if line.startswith('pairing '): sys.stdout.write('PASE establishment successful\\n')
 elif 'specification-version' in line: sys.stdout.write('CHIP:TOO: SpecificationVersion: 17039360\\n')
 elif 'software-version' in line: sys.stdout.write('CHIP:TOO: SoftwareVersion: 42\\n')
 elif 'parts-list' in line: sys.stdout.write('CHIP:TOO: PartsList: 1 entries\\nCHIP:TOO: [1]: 2\\n')
 elif 'server-list' in line: sys.stdout.write('CHIP:TOO: ServerList: 1 entries\\nCHIP:TOO: [1]: 258\\n')
 sys.stdout.write('>>> '); sys.stdout.flush()
'''
        with tempfile.TemporaryDirectory() as tmp:
            output = Path(tmp) / 'evidence'
            elf = Path(tmp) / 'synthetic.elf'
            elf.write_bytes(b'\x7fELFsynthetic-unit-test')
            real_popen = subprocess.Popen
            seen = []

            class FakeProcess:
                def poll(self):
                    return None

                def wait(self, timeout=None):
                    return 0

            def popen(command, stdout=None, **kwargs):
                if command[:2] == ['docker', 'run']:
                    (output / 'renode.log').write_text('CHIPoBLE GATT ready\n')
                    return FakeProcess()
                if command[-3:] == ['/host-tools/chip-tool', 'interactive', 'start']:
                    seen.append(command)
                    return real_popen([sys.executable, '-u', '-c', interactive], stdout=stdout,
                                      stdin=kwargs['stdin'], stderr=subprocess.STDOUT, text=True)
                return FakeProcess()

            def run(command, **kwargs):
                return SimpleNamespace(returncode=0, stdout=b'true', stderr=b'')

            argv = [str(SCRIPT), '--bundled-image', 'synthetic-image', '--pase-only',
                    '--elf', str(elf), '--output', str(output), '--timeout', '1']
            with patch.object(sys, 'argv', argv), patch.object(stock.subprocess, 'Popen', side_effect=popen), \
                 patch.object(stock.subprocess, 'run', side_effect=run), \
                 patch.object(stock.subprocess, 'check_output', return_value='a' * 64 + '  chip-tool\n'), \
                 patch('builtins.print'):
                self.assertEqual(stock.main(), 0)
            result = json.loads((output / 'results.json').read_text())
            self.assertEqual(result['status'], 'PASE_PASS')
            self.assertTrue(result['PASE_established'])
            self.assertFalse(result['CASE_established'])
            self.assertFalse(result['commissioning_verified'])
            self.assertTrue(result['reads_verified'])
            self.assertIsNone(result['pairing_returncode'])
            self.assertEqual(result['attribute_reads']['software_version']['value'], 42)
            self.assertEqual(result['onoff_applicability'], 'NOT_APPLICABLE')
            self.assertEqual(len(seen), 1)
            self.assertIn('-t', seen[0])
            self.assertIn('--pase-only 1', (output / 'pairing.log').read_text())
            self.assertIn('SpecificationVersion:', (output / 'basic_specification_version.log').read_text())

    def test_full_flow_uses_discovered_endpoint_or_skips_onoff(self):
        """Exercise the actual runner with synthetic responses; no Docker or ELF assets."""
        for endpoint, clusters, expected in ((2, [258], 'FULL_READ_PASS'),
                                             (3, [6, 258], 'FULL_PASS')):
            with self.subTest(endpoint=endpoint), tempfile.TemporaryDirectory() as tmp:
                output = Path(tmp) / 'evidence'
                elf = Path(tmp) / 'synthetic.elf'
                elf.write_bytes(b'\x7fELFsynthetic-unit-test')
                commands = []
                state = {'thread_started': False}

                class FakeProcess:
                    def __init__(self, stdout=None, payload='', live=False):
                        self.live = live
                        self.returncode = None if live else 0
                        if stdout is not None:
                            stdout.write(payload)
                            stdout.flush()

                    def poll(self):
                        return None if self.live else self.returncode

                    def wait(self, timeout=None):
                        self.live = False
                        self.returncode = 0
                        return 0

                    def kill(self):
                        self.wait()

                def run(command, **kwargs):
                    if command[:2] == ['docker', 'inspect']:
                        return SimpleNamespace(returncode=0, stdout=b'true', stderr=b'')
                    if '/host-tools/ot-ctl' in command:
                        args = command[command.index('wpan0') + 1:]
                        if args == ['thread', 'start']:
                            state['thread_started'] = True
                        text = ('leader\n' if state['thread_started'] else 'disabled\n') if args == ['state'] else ''
                        return SimpleNamespace(returncode=0, stdout=text, stderr='')
                    return SimpleNamespace(returncode=0, stdout=b'', stderr=b'')

                def popen(command, stdout=None, **kwargs):
                    if command[:2] == ['docker', 'run']:
                        state['docker_run'] = command
                        (output / 'renode.log').write_text('CHIPoBLE GATT ready\n')
                        return FakeProcess(stdout, live=True)
                    if command[:2] == ['docker', 'exec'] and '/host-tools/chip-tool' not in command:
                        return FakeProcess(stdout, live=True)
                    args = command[command.index('/host-tools/chip-tool') + 1:]
                    commands.append(args)
                    if args[0] == 'pairing':
                        text = ('PASE establishment successful\nReceived Sigma2 msg\n'
                                'CASE establishment successful\nDevice commissioning completed with success\n')
                    elif args[:2] == ['basicinformation', 'read']:
                        text = ('CHIP:TOO: SpecificationVersion: 17039360\n' if args[2] == 'specification-version'
                                else 'CHIP:TOO: SoftwareVersion: 42\n')
                    elif args[:2] == ['descriptor', 'read']:
                        text = ('CHIP:TOO: PartsList: 1 entries\nCHIP:TOO: [1]: %s\n' % endpoint if args[2] == 'parts-list'
                                else 'CHIP:TOO: ServerList: %s entries\n%s' %
                                (len(clusters), ''.join('CHIP:TOO: [%s]: %s\n' % (i, value)
                                                        for i, value in enumerate(clusters, 1))))
                    elif args[:3] == ['onoff', 'read', 'on-off']:
                        text = 'CHIP:TOO: OnOff: %s\n' % ('TRUE' if commands[-2][1] == 'toggle' else 'FALSE')
                    else:
                        text = 'CHIP:TOO: Toggle response SUCCESS\n'
                    return FakeProcess(stdout, text)

                argv = [str(SCRIPT), '--bundled-image', 'synthetic-image', '--elf', str(elf),
                        '--output', str(output), '--timeout', '1', '--leader-timeout', '1']
                with patch.object(sys, 'argv', argv), patch.object(stock.subprocess, 'run', side_effect=run), \
                     patch.object(stock.subprocess, 'Popen', side_effect=popen), \
                     patch.object(stock.subprocess, 'check_output', return_value='a' * 64 + '  chip-tool\n'), \
                     patch.object(stock.time, 'sleep'), patch('builtins.print'):
                    self.assertEqual(stock.main(), 0)
                result = json.loads((output / 'results.json').read_text())
                self.assertEqual(result['status'], expected)
                self.assertEqual(result['onoff_endpoint'], endpoint if clusters[0] == 6 else None)
                self.assertEqual(result['onoff_applicability'], 'APPLICABLE' if clusters[0] == 6 else 'NOT_APPLICABLE')
                self.assertTrue(result['CASE_established'])
                self.assertTrue(result['commissioning_verified'])
                self.assertTrue(result['reads_verified'])
                self.assertEqual(result['attribute_reads']['specification_version']['value'], 17039360)
                onoff = [args for args in commands if args[0] == 'onoff']
                self.assertEqual(len(onoff), 3 if clusters[0] == 6 else 0)
                self.assertTrue(all(args[-1] == str(endpoint) for args in onoff))
                self.assertTrue(result['operational_verified'] if onoff else not result['operational_verified'])
                self.assertEqual(state['docker_run'][state['docker_run'].index('--memory') + 1], '3g')


if __name__ == '__main__':
    unittest.main()
