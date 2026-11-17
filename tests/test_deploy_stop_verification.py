"""Deployment state and image checks against synthetic memory; no hardware I/O."""
import json
import struct
import threading

import pytest

from fx3u import layout
from fx3u.bytecode import REGION_SIZE, assemble, parse_il
from fx3u.client import BASE, BLOCK, Session, digest, frame
from fx3u.experimental_codec import disassemble


class MemoryPLC(Session):
    def __init__(self, original, *, full_region=False, lose_write_ack=False,
                 corrupt_untouched=False, ignore_stop=False, unknown_stopped=False):
        self.lock = threading.RLock()
        self.memory = bytearray(original)
        self._program_size = len(original)
        self.full_region = full_region
        self.run = True
        self.lose_write_ack = lose_write_ack
        self.corrupt_untouched = corrupt_untouched
        self.ignore_stop = ignore_stop
        self.unknown_stopped = unknown_stopped
        self.events, self.writes, self.run_images = [], [], []
        self.cpu_checksum = self.checksum()

    def checksum(self):
        return sum(v for (v,) in struct.iter_unpack('<H', self.memory)) & 65535

    def exchange(self, tx):
        assert self.full_region and tx == frame('E0180005C')
        header = b'\0\0' + struct.pack('<H', self.cpu_checksum) + bytes(88)
        return frame(header.hex().upper())

    def image(self):
        self.events.append(('image', self.run))
        return bytes(self.memory)

    def running(self):
        if self.unknown_stopped and not self.run:
            raise TimeoutError('CPU mode unreadable')
        return self.run

    def set_running(self, run):
        self.events.append(('RUN' if run else 'STOP', self.run))
        if run:
            self.run_images.append(bytes(self.memory))
            self.cpu_checksum = self.checksum()
        if run or not self.ignore_stop:
            self.run = run

    def write_block(self, offset, data):
        self.writes.append(offset)
        self.memory[offset:offset+len(data)] = data
        if self.corrupt_untouched and len(self.writes) == 1:
            self.memory[BLOCK*2] ^= 1
        if self.lose_write_ack and len(self.writes) == 1:
            raise TimeoutError('Write happened; ACK lost')

    def read_block(self, offset):
        return bytes(self.memory[offset:offset+BLOCK])


@pytest.fixture(params=[False, True], ids=['baseline', 'full-region'])
def images(request, monkeypatch):
    full_region = request.param
    size = 32000 if full_region else REGION_SIZE
    if full_region:
        header = bytes(92)
        monkeypatch.setattr(layout, 'OBSERVED_16K', layout.ProgramLayout(
            'fixture', BASE, size, layout.static_header_digest(header)))
    original = assemble(parse_il('END'))[0].ljust(size, b'\xff')
    candidate = assemble(parse_il('LD M0\nOUT M1\nEND'))[0].ljust(size, b'\xff')
    return full_region, original, candidate


def test_untouched_corruption_is_detected_before_any_run(images, tmp_path):
    full_region, original, candidate = images
    plc = MemoryPLC(original, full_region=full_region, corrupt_untouched=True)
    with pytest.raises(ValueError, match='Stopped image differs'):
        plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble)
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert report['pre_run_image_verified'] is False
    assert report['recovery_error'] == 'Recovery image differs'
    assert plc.run_images == []
    assert report['final_cpu_mode'] == 'STOP'


def test_default_deploy_checks_full_image_in_stop_before_run(images, tmp_path):
    full_region, original, candidate = images
    plc = MemoryPLC(original, full_region=full_region)
    report = plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble)
    stop = plc.events.index(('STOP', True))
    run = plc.events.index(('RUN', False))
    assert ('image', False) in plc.events[stop+1:run]
    assert report['pre_run_image_verified'] is True
    assert report['final_cpu_mode'] == 'RUN'
    if full_region:
        assert report['checksum_status'] == 'verified_after_run'
    else:
        assert 'checksum_status' not in report


@pytest.mark.parametrize('same_image', [False, True])
def test_leave_stopped_verifies_mode_and_bytes_even_without_changes(images, tmp_path, same_image):
    full_region, original, candidate = images
    if same_image:
        candidate = original
    plc = MemoryPLC(original, full_region=full_region)
    report = plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble,
                        leave_stopped=True)
    assert report['ok'] and report['leave_stopped']
    assert report['final_cpu_mode'] == 'STOP'
    assert report['pre_run_image_verified'] is True
    assert bytes(plc.memory) == candidate
    assert not plc.run and plc.run_images == []
    assert any(event == ('image', False) for event in plc.events)
    if same_image:
        assert plc.writes == []
    if full_region:
        assert report['checksum_status'] == 'deferred_until_run'
        if not same_image:
            assert plc.cpu_checksum != plc.checksum()  # CPU sum remains stale in STOP.
    else:
        assert 'checksum_status' not in report


def test_leave_stopped_recovery_never_runs_original_or_candidate(images, tmp_path):
    full_region, original, candidate = images
    plc = MemoryPLC(original, full_region=full_region, lose_write_ack=True)
    with pytest.raises(TimeoutError, match='ACK lost'):
        plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble,
                   leave_stopped=True)
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert report['recovered'] is True
    assert report['final_cpu_mode'] == 'STOP'
    assert plc.run_images == [] and plc.writes == [0, 0]
    assert bytes(plc.memory) == original
    if full_region:
        assert report['recovery_checksum_status'] == 'deferred_until_run'


@pytest.mark.parametrize('same_image', [False, True])
@pytest.mark.parametrize('fault,mode', [('ignore_stop', 'RUN'), ('unknown_stopped', 'unknown')])
def test_leave_stopped_refuses_unconfirmed_stop_and_recovery(images, tmp_path, fault, mode,
                                                           same_image):
    full_region, original, candidate = images
    if same_image:
        candidate = original
    plc = MemoryPLC(original, full_region=full_region, **{fault: True})
    with pytest.raises((ValueError, TimeoutError)):
        plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble,
                   leave_stopped=True)
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert not report['ok'] and 'recovered' not in report
    assert report['final_cpu_mode'] == mode
    assert plc.writes == [] and plc.run_images == []
    assert not any(event == ('image', False) for event in plc.events)


def test_default_deploy_refuses_ignored_stop_before_any_write(images, tmp_path):
    full_region, original, candidate = images
    plc = MemoryPLC(original, full_region=full_region, ignore_stop=True)
    with pytest.raises(ValueError, match='STOP verification failed before program access'):
        plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble)
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert plc.writes == [] and plc.run_images == []
    assert not report['pre_run_image_verified'] and 'recovered' not in report
    assert report['final_cpu_mode'] == 'RUN'


def test_recovery_refuses_writes_when_stop_is_ignored(images, tmp_path):
    full_region, original, candidate = images

    class IgnoredRecoveryStopPLC(MemoryPLC):
        def write_block(self, offset, data):
            try:
                super().write_block(offset, data)
            finally:
                self.run = True  # Independent CPU state change after uncertain write.

        def set_running(self, run):
            if self.writes and not run:
                self.events.append(('STOP', self.run))
            else:
                super().set_running(run)

    plc = IgnoredRecoveryStopPLC(original, full_region=full_region, lose_write_ack=True)
    with pytest.raises(TimeoutError, match='ACK lost'):
        plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble,
                   leave_stopped=True)
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert plc.writes == [0]  # No restoration write without confirmed STOP.
    assert report['recovery_error'] == 'Recovery STOP verification failed before program access'
    assert report['final_cpu_mode'] == 'RUN' and 'recovered' not in report


@pytest.mark.parametrize('final_mode', ['RUN', 'unknown'])
def test_recovered_is_not_reported_when_final_stop_is_unconfirmed(images, tmp_path, final_mode):
    full_region, original, candidate = images

    class LostStopPLC(MemoryPLC):
        stopped_queries = 0

        def running(self):
            if not self.run:
                self.stopped_queries += 1
                if self.stopped_queries > 3:
                    if final_mode == 'unknown':
                        raise TimeoutError('Final CPU mode unreadable')
                    return True
            return self.run

    plc = LostStopPLC(original, full_region=full_region, lose_write_ack=True)
    with pytest.raises(TimeoutError, match='ACK lost'):
        plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble,
                   leave_stopped=True)
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert report['final_cpu_mode'] == final_mode
    assert 'recovered' not in report
    assert plc.run_images == []


@pytest.mark.parametrize('images', [True], indirect=True)
def test_default_still_requires_dynamic_cpu_checksum_after_run(images, tmp_path):
    full_region, original, candidate = images

    class StaleChecksumPLC(MemoryPLC):
        def set_running(self, run):
            saved_checksum = self.cpu_checksum
            super().set_running(run)
            self.cpu_checksum = saved_checksum

    plc = StaleChecksumPLC(original, full_region=True)
    with pytest.raises(ValueError, match='checksum mismatch'):
        plc.deploy(candidate, digest(original), tmp_path/'trial', decoder=disassemble)
    report = json.loads((tmp_path/'trial/result.json').read_text())
    assert report['pre_run_image_verified'] is True
    assert report['checksum_status'] == 'not_verified'
    assert report['recovery_checksum_status'] == 'verified_after_run'
    assert report['recovered'] is True
    assert bytes(plc.memory) == original and plc.run
