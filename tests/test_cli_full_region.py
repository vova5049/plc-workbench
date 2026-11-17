"""Full-window CLI integrity checks use synthetic headers and no transport."""
import json
import struct
import sys
import threading

import pytest

from fx3u import cli, layout
from fx3u.bytecode import assemble, parse_il
from fx3u.client import BASE, Session, frame


@pytest.fixture
def memory_session(monkeypatch):
    header = bytes(92)
    monkeypatch.setattr(layout, 'OBSERVED_16K', layout.ProgramLayout(
        'fixture', BASE, 32000, layout.static_header_digest(header)))

    class Fake(Session):
        def __init__(self, checksum_valid=True, layout_valid=True, small=False):
            self.lock = threading.RLock()
            self.data = assemble(parse_il('END'))[0] if small else (
                bytes.fromhex('0f00') + b'\xff' * 31998)
            self.checksum_valid, self.layout_valid = checksum_valid, layout_valid
            self.image_reads = self.header_reads = 0
            self.closed = False

        def exchange(self, tx):
            assert tx == frame('E0180005C')
            self.header_reads += 1
            value = sum(v for (v,) in struct.iter_unpack('<H', self.data)) & 65535
            if not self.checksum_valid:
                value ^= 1
            current = bytearray(header[:2] + struct.pack('<H', value) + header[4:])
            if not self.layout_valid:
                current[0] ^= 1
            return frame(current.hex().upper())

        def image(self):
            self.image_reads += 1
            return self.data

        def running(self):
            return True

        def close(self):
            self.closed = True

    return Fake


def invoke(monkeypatch, tmp_path, session, command, *, full=True, match=True):
    monkeypatch.setattr(Session, 'open', lambda *args: session)
    argv = ['fx3u', command, '--port', 'MOCK', '--wire-log', str(tmp_path/'wire.jsonl')]
    if full:
        argv.append('--full-region')
    if command == 'dump':
        argv.extend(['--out-dir', str(tmp_path/'dump')])
    else:
        expected = tmp_path/'expected.bin'
        expected.write_bytes(session.data if match else b'different')
        argv.append(str(expected))
    monkeypatch.setattr(sys, 'argv', argv)
    cli.main()


@pytest.mark.parametrize('command', ['dump', 'compare'])
@pytest.mark.parametrize('checksum_valid', [True, False])
def test_full_region_checksum_is_explicit(monkeypatch, tmp_path, capsys,
                                         memory_session, command, checksum_valid):
    session = memory_session(checksum_valid=checksum_valid)
    if checksum_valid:
        invoke(monkeypatch, tmp_path, session, command)
    else:
        with pytest.raises(SystemExit) as error:
            invoke(monkeypatch, tmp_path, session, command)
        assert error.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert session.closed
    assert result['layout_valid'] is True
    assert result['checksum_valid'] is checksum_valid
    assert (result['checksum_expected_sum16'] == result['checksum_actual_sum16']) is checksum_valid
    assert ('checksum_error' in result) is not checksum_valid
    if command == 'dump':
        directory = tmp_path/'dump'
        assert (directory/'program.bin').read_bytes() == session.data
        assert (directory/'program-header.bin').read_bytes() == session._program_header
        assert json.loads((directory/'manifest.json').read_text()) == result
        assert result['verified'] is checksum_valid
        assert (directory/'program.il').exists() is checksum_valid
    else:
        assert result['match'] is True
        assert result['success'] is checksum_valid


@pytest.mark.parametrize('command', ['dump', 'compare'])
def test_unknown_header_refuses_before_program_read(monkeypatch, tmp_path, memory_session, command):
    session = memory_session(layout_valid=False)
    with pytest.raises(ValueError, match='Unknown program header'):
        invoke(monkeypatch, tmp_path, session, command)
    assert session.closed and session.image_reads == 0
    assert not (tmp_path/'dump').exists()


def test_compare_unequal_bytes_with_valid_checksum_is_not_success(monkeypatch, tmp_path,
                                                                capsys, memory_session):
    session = memory_session()
    with pytest.raises(SystemExit) as error:
        invoke(monkeypatch, tmp_path, session, 'compare', match=False)
    assert error.value.code == 2
    result = json.loads(capsys.readouterr().out)
    assert result['checksum_valid'] is True
    assert result['match'] is False and result['success'] is False


@pytest.mark.parametrize('command', ['dump', 'compare'])
def test_default_small_window_keeps_original_result_fields(monkeypatch, tmp_path,
                                                          capsys, memory_session, command):
    session = memory_session(small=True)
    invoke(monkeypatch, tmp_path, session, command, full=False)
    result = json.loads(capsys.readouterr().out)
    expected = {'sha256', 'bytes', 'run', 'scope'} if command == 'dump' else {
        'match', 'live_sha256', 'run'}
    assert set(result) == expected
    assert session.header_reads == 0 and session.closed
