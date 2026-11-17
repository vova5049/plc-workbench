"""Synthetic clocks expose deployment timing boundaries without hardware I/O."""
import json
import sys
import threading

import pytest

from fx3u import cli
from fx3u.bytecode import REGION_SIZE, assemble, parse_il
from fx3u.client import BLOCK, Session, digest, image_readback_lower_bound


@pytest.mark.parametrize('size,minimum', [(6096, 12.7), (32000, 200/3)])
def test_response_minimum_accounts_for_ascii_hex_and_7e1(size, minimum):
    info = image_readback_lower_bound(size)
    assert info['response_data_min_seconds'] == pytest.approx(minimum)
    assert info['ascii_hex_characters'] == size * 2
    assert info['bits_per_character'] == 10 and info['serial_format'] == '7E1'
    assert not info['hardware_stop_timing_qualified']
    assert 'excludes framing, requests, writes and delays' in info['scope']


@pytest.mark.parametrize('full_region', [False, True])
def test_cli_dry_run_reports_response_minimum_before_open(tmp_path, monkeypatch, capsys,
                                                        full_region):
    size = 32000 if full_region else REGION_SIZE
    source = tmp_path/'candidate.bin'
    source.write_bytes(assemble(parse_il('END'))[0].ljust(size, b'\xff'))
    argv = ['fx3u', 'deploy', str(source), '--port', 'MOCK', '--wire-log',
            str(tmp_path/'wire.jsonl'), '--out-dir', str(tmp_path/'backup'),
            '--expected-sha256', '0'*64]
    if full_region:
        argv.append('--full-region')
    monkeypatch.setattr(sys, 'argv', argv)
    monkeypatch.setattr(Session, 'open', lambda *a: pytest.fail('dry run opened PLC'))
    cli.main()
    report = json.loads(capsys.readouterr().out)
    assert report['verification_policy'] == 'full_image_in_stop'
    assert report['full_image_readback'] == image_readback_lower_bound(size)
    assert not (tmp_path/'wire.jsonl').exists()


def test_execute_exposes_response_minimum_before_connection(tmp_path, monkeypatch, capsys):
    source = tmp_path/'candidate.bin'
    source.write_bytes(assemble(parse_il('END'))[0])
    wire = tmp_path/'wire.jsonl'
    monkeypatch.setattr(sys, 'argv', ['fx3u', 'deploy', str(source), '--execute',
                                    '--port', 'MOCK', '--wire-log', str(wire),
                                    '--out-dir', str(tmp_path/'backup'),
                                    '--expected-sha256', '0'*64])

    def refuse_connection(*args):
        message = capsys.readouterr().err
        assert 'response data alone needs at least 12.70 s' in message
        assert 'actual STOP duration is not hardware-qualified' in message
        report = json.loads(wire.with_name(wire.name + '.input.json').read_text())
        assert report['full_image_readback'] == image_readback_lower_bound(REGION_SIZE)
        raise RuntimeError('synthetic connection refusal')

    monkeypatch.setattr(Session, 'open', refuse_connection)
    with pytest.raises(RuntimeError, match='synthetic connection refusal'):
        cli.main()


@pytest.fixture
def timed_plc(monkeypatch):
    class Clock:
        seconds = 0

        def advance(self, seconds):
            self.seconds += seconds

    clock = Clock()
    monkeypatch.setattr('fx3u.client.time.monotonic', lambda: clock.seconds)
    original = assemble(parse_il('END'))[0]
    candidate = assemble(parse_il('LD M0\nOUT M1\nEND'))[0]

    class PLC(Session):
        def __init__(self, fault=None):
            self.lock = threading.RLock()
            self.memory = bytearray(original)
            self.run = True
            self.fault = fault
            self.writes = []
            self.read_modes = []

        def image(self):
            clock.advance(13)
            self.read_modes.append(self.run)
            return bytes(self.memory)

        def running(self):
            clock.advance(1)
            if self.fault == 'stop_unknown':
                if not self.run:
                    raise TimeoutError('unreadable CPU mode')
            return self.run

        def set_running(self, run):
            clock.advance(3)
            if not run and self.fault == 'stop':
                raise TimeoutError('STOP ACK lost')
            self.run = run

        def write_block(self, offset, data):
            clock.advance(2)
            self.writes.append(offset)
            self.memory[offset:offset+len(data)] = data
            if self.fault == 'write' and len(self.writes) == 1:
                raise TimeoutError('write ACK lost')

        def read_block(self, offset):
            clock.advance(1)
            return bytes(self.memory[offset:offset+BLOCK])

    return PLC, original, candidate


def test_stop_interval_includes_stop_request_and_run_confirmation(timed_plc, tmp_path):
    PLC, original, candidate = timed_plc
    plc = PLC()
    report = plc.deploy(candidate, digest(original), tmp_path/'trial')
    assert report['stop_seconds'] == 23
    assert report['stop_intervals'] == [{'phase': 'deploy', 'seconds': 23,
                                        'stop_confirmed': True, 'end': 'run_confirmed',
                                        'run_confirmed': True}]
    assert plc.read_modes == [True, False, True]
    assert report['stop_request_to_run_seconds'] == [23]
    assert 'unresumed_stop_elapsed_seconds' not in report
    assert report['full_image_readback'] == image_readback_lower_bound(REGION_SIZE)


@pytest.mark.parametrize('unchanged', [False, True])
def test_leave_stopped_measures_through_final_status_without_duplicate_image(timed_plc, tmp_path,
                                                                           unchanged):
    PLC, original, candidate = timed_plc
    if unchanged:
        candidate = original
    plc = PLC()
    report = plc.deploy(candidate, digest(original), tmp_path/'trial', leave_stopped=True)
    assert report['stop_verification_seconds'] == (18 if unchanged else 21)
    assert 'stop_seconds' not in report
    interval = report['stop_intervals'][0]
    assert interval['seconds'] == report['stop_verification_seconds']
    assert interval['end'] == 'stopped_at_return' and not interval['run_confirmed']
    assert interval['stop_confirmed']
    assert report['stop_request_to_run_seconds'] == []
    assert report['unresumed_stop_elapsed_seconds'] == report['stop_verification_seconds']
    assert plc.read_modes == [True, False]
    assert not plc.run
    assert (tmp_path/'trial/after.bin').read_bytes() == candidate
    assert (tmp_path/'trial/stopped.bin').read_bytes() == candidate
    assert plc.writes == ([] if unchanged else [0])


def test_unchanged_normal_deploy_has_no_stop_or_stop_measurement(timed_plc, tmp_path):
    PLC, original, _ = timed_plc
    plc = PLC()
    report = plc.deploy(original, digest(original), tmp_path/'trial')
    assert report['stop_intervals'] == []
    assert 'stop_seconds' not in report and 'stop_verification_seconds' not in report
    assert plc.run and plc.writes == [] and plc.read_modes == [True, True]


@pytest.mark.parametrize('leave_stopped', [False, True])
def test_failed_write_preserves_attempt_and_recovery_timings(timed_plc, tmp_path, leave_stopped):
    PLC, original, candidate = timed_plc
    plc = PLC(fault='write')
    with pytest.raises(TimeoutError, match='write ACK lost'):
        plc.deploy(candidate, digest(original), tmp_path/'trial', leave_stopped=leave_stopped)
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert report['stop_attempt_seconds'] == 6
    assert report['recovered'] and bytes(plc.memory) == original
    first, recovery = report['stop_intervals']
    assert first['phase'] == 'deploy' and first['end'] == 'failure'
    assert first['stop_confirmed'] and not first['run_confirmed']
    assert recovery['phase'] == 'recovery' and recovery['stop_confirmed']
    if leave_stopped:
        assert report['recovery_stop_verification_seconds'] == 20
        assert recovery['end'] == 'stopped_at_return' and not recovery['run_confirmed']
        assert report['unresumed_stop_elapsed_seconds'] == 27
        assert report['stop_request_to_run_seconds'] == []
    else:
        assert report['recovery_stop_seconds'] == 22
        assert recovery['end'] == 'run_confirmed' and recovery['run_confirmed']
        assert report['stop_request_to_run_seconds'] == [28]
        assert 'unresumed_stop_elapsed_seconds' not in report
    assert plc.run != leave_stopped


@pytest.mark.parametrize('fault,final_mode', [('stop', 'RUN'), ('stop_unknown', 'unknown')])
def test_failed_stop_keeps_all_observed_intervals_without_claiming_downtime(timed_plc, tmp_path,
                                                                         fault, final_mode):
    PLC, original, candidate = timed_plc
    plc = PLC(fault=fault)
    with pytest.raises(TimeoutError):
        plc.deploy(candidate, digest(original), tmp_path/'trial')
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert report['final_cpu_mode'] == final_mode
    assert report['stop_attempt_seconds'] >= 3
    assert report['recovery_stop_attempt_seconds'] >= 3
    assert [i['phase'] for i in report['stop_intervals']] == ['deploy', 'recovery', 'final_stop']
    assert all(not i['stop_confirmed'] for i in report['stop_intervals'])
    assert all(not i['run_confirmed'] for i in report['stop_intervals'][:2])
    assert report['stop_intervals'][-1]['run_confirmed'] == (final_mode == 'RUN')
    if final_mode == 'RUN':
        assert report['stop_request_to_run_seconds'] == [10]
        assert 'unresumed_stop_elapsed_seconds' not in report
    else:
        assert report['stop_request_to_run_seconds'] == []
        assert report['unresumed_stop_elapsed_seconds'] == 12
    assert report['stop_intervals'][-1]['seconds'] == 4
    assert plc.writes == []
