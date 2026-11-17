"""Recovery fault injection uses synthetic memory and never opens a port."""
import json
import struct
import threading

import pytest

from fx3u import layout
from fx3u.bytecode import parse_il
from fx3u.client import BASE, BLOCK, Session, digest, frame
from fx3u.experimental_codec import assemble, disassemble


@pytest.fixture
def recovery_session(monkeypatch):
    header = bytes(92)
    monkeypatch.setattr(layout, 'OBSERVED_16K', layout.ProgramLayout(
        'fixture', BASE, 32000, layout.static_header_digest(header)))
    original = bytes.fromhex('0f00') + b'\xff' * 31998
    candidate = assemble(parse_il('LDI M0\nMOV K7 D0\nEND'))[0].ljust(32000, b'\xff')

    class Fake(Session):
        def __init__(self, fault='checksum', stop_fault=None, unreadable=False):
            self.lock = threading.RLock()
            self.memory = bytearray(original)
            self._program_size = 32000
            self.run = True
            self.fault, self.stop_fault, self.unreadable = fault, stop_fault, unreadable
            self.headers = self.stops = 0
            self.writes, self.events = [], []
            self.write_error = TimeoutError('Candidate write happened; ACK lost')

        def exchange(self, tx):
            assert tx == frame('E0180005C')
            self.headers += 1
            checksum = sum(v for (v,) in struct.iter_unpack('<H', self.memory)) & 65535
            current = bytearray(header[:2] + struct.pack('<H', checksum) + header[4:])
            if self.headers > 1:
                if self.fault == 'checksum':
                    current[2] ^= 1
                elif self.fault == 'header':
                    current[0] ^= 1
            return frame(current.hex().upper())

        def image(self):
            data = bytes(self.memory)
            if self.fault == 'image' and len(self.writes) > 1:
                return bytes([data[0] ^ 1]) + data[1:]
            return data

        def running(self):
            self.events.append('status')
            if self.unreadable and self.stops >= 3:
                raise TimeoutError('Final CPU mode unreadable')
            return self.run

        def set_running(self, run):
            self.events.append('RUN' if run else 'STOP')
            if not run:
                self.stops += 1
                if self.stops >= 3 and self.stop_fault == 'before':
                    raise OSError('Safety STOP failed before execution')
            self.run = run
            if not run and self.stops >= 3 and self.stop_fault == 'after':
                raise TimeoutError('Safety STOP happened; ACK lost')
            if run and self.fault == 'run_ack':
                raise TimeoutError('Recovery RUN happened; ACK lost')

        def write_block(self, offset, data):
            self.writes.append(offset)
            self.memory[offset:offset+len(data)] = data
            if len(self.writes) == 1:
                raise self.write_error

        def read_block(self, offset):
            return bytes(self.memory[offset:offset+BLOCK])

    return Fake, original, candidate


def fail_deploy(session, original, candidate, directory):
    with pytest.raises(TimeoutError) as error:
        session.deploy(candidate, digest(original), directory, decoder=disassemble)
    assert error.value is session.write_error  # Preserve the original failure.
    assert (directory/'before.bin').read_bytes() == original
    assert session.writes == [0, 0]  # One candidate attempt, one original restore.
    assert session.events[-1] == 'status'  # Separate query after the STOP attempt.
    return json.loads((directory/'result.json').read_text())


@pytest.mark.parametrize('fault', ['checksum', 'header', 'run_ack'])
def test_post_run_recovery_failure_attempts_stop_and_reads_mode(recovery_session, tmp_path, fault):
    Fake, original, candidate = recovery_session
    session = Fake(fault=fault)
    report = fail_deploy(session, original, candidate, tmp_path/'trial')
    assert report['recovery_stop_attempted'] is True
    assert report['final_cpu_mode'] == 'STOP'
    assert 'recovered' not in report
    assert 'recovery_stop_error' not in report
    assert not session.run and bytes(session.memory) == original
    assert [e for e in session.events if e != 'status'] == ['STOP', 'STOP', 'RUN', 'STOP']
    assert (tmp_path/'trial/recovery.bin').read_bytes() == original


@pytest.mark.parametrize('stop_fault,unreadable,mode', [
    ('before', False, 'RUN'), ('after', False, 'STOP'), ('before', True, 'unknown'),
])
def test_failed_stop_reports_observed_or_unknown_mode(recovery_session, tmp_path,
                                                      stop_fault, unreadable, mode):
    Fake, original, candidate = recovery_session
    session = Fake(stop_fault=stop_fault, unreadable=unreadable)
    report = fail_deploy(session, original, candidate, tmp_path/'trial')
    assert report['final_cpu_mode'] == mode
    assert report['recovery_stop_error']
    assert ('final_cpu_mode_error' in report) is unreadable
    assert session.stops == 3  # No automatic repeat of the additional STOP.


def test_bad_recovery_bytes_never_resume_run(recovery_session, tmp_path):
    Fake, original, candidate = recovery_session
    session = Fake(fault='image')
    report = fail_deploy(session, original, candidate, tmp_path/'trial')
    assert report['recovery_error'] == 'Recovery image differs'
    assert report['final_cpu_mode'] == 'STOP'
    assert 'RUN' not in session.events
    assert (tmp_path/'trial/recovery.bin').read_bytes() != original
