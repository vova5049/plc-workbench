"""Candidate policy is checked offline before opening any PLC session."""
import hashlib
import json
import sys

import pytest

from fx3u import cli
from fx3u.client import REGION_SIZE, Session
from fx3u.experimental_codec import assemble, parse_il


def arguments(tmp_path, *, execute=False, full=False, pad=False, stopped=False):
    argv = ['fx3u', 'deploy', str(tmp_path/'p.bin'), '--port', 'MOCK',
            '--wire-log', str(tmp_path/'wire.jsonl'), '--out-dir', str(tmp_path/'backup'),
            '--expected-sha256', '0'*64, '--full-region' if full else '--experimental']
    if execute:
        argv.append('--execute')
    if pad:
        argv.append('--pad-to-region')
    if stopped:
        argv.append('--leave-stopped')
    return argv


@pytest.mark.parametrize('execute', [False, True])
@pytest.mark.parametrize('full', [False, True])
def test_short_image_requires_explicit_padding_before_io(tmp_path, monkeypatch, execute, full):
    (tmp_path/'p.bin').write_bytes(assemble(parse_il('END'))[0])
    monkeypatch.setattr(sys, 'argv', arguments(tmp_path, execute=execute, full=full))
    monkeypatch.setattr(Session, 'open', lambda *a: pytest.fail('Opened PLC before candidate approval'))
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert not (tmp_path/'wire.jsonl').exists()
    assert not (tmp_path/'backup').exists()


@pytest.mark.parametrize('full', [False, True])
def test_dry_run_reports_source_and_padded_hashes_without_io(tmp_path, monkeypatch, capsys, full):
    source = assemble(parse_il('END'))[0]
    (tmp_path/'p.bin').write_bytes(source)
    monkeypatch.setattr(sys, 'argv', arguments(tmp_path, full=full, pad=True, stopped=True))
    monkeypatch.setattr(Session, 'open', lambda *a: pytest.fail('Dry run opened a session'))
    cli.main()
    result = json.loads(capsys.readouterr().out)
    size = 32000 if full else REGION_SIZE
    assert result['source_bytes'] == len(source)
    assert result['candidate_bytes'] == size
    assert result['source_sha256'] == hashlib.sha256(source).hexdigest()
    assert result['candidate_sha256'] == hashlib.sha256(source.ljust(size, b'\xff')).hexdigest()
    assert result['padded'] and result['leave_stopped'] and result['dry_run']
    assert not (tmp_path/'wire.jsonl').exists()


def test_full_size_image_needs_no_padding_consent(tmp_path, monkeypatch, capsys):
    source = assemble(parse_il('END'))[0].ljust(REGION_SIZE, b'\xff')
    (tmp_path/'p.bin').write_bytes(source)
    monkeypatch.setattr(sys, 'argv', arguments(tmp_path))
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert not result['padded']
    assert result['source_sha256'] == result['candidate_sha256']


def test_execute_passes_stop_policy_and_preserves_source_provenance(tmp_path, monkeypatch, capsys):
    source = assemble(parse_il('END'))[0]
    (tmp_path/'p.bin').write_bytes(source)
    seen = {}

    class Fake:
        def deploy(self, candidate, expected, directory, **kwargs):
            seen.update(kwargs)
            seen['candidate'] = candidate
            directory.mkdir()
            return {'ok': True, 'final_cpu_mode': 'STOP'}

        def close(self):
            seen['closed'] = True

    monkeypatch.setattr(Session, 'open', lambda *a: Fake())
    monkeypatch.setattr(sys, 'argv', arguments(tmp_path, execute=True, pad=True, stopped=True))
    cli.main()
    result = json.loads(capsys.readouterr().out)
    assert seen['leave_stopped'] is True and seen['closed']
    assert result['source_bytes'] == len(source)
    assert result['candidate_sha256'] == hashlib.sha256(seen['candidate']).hexdigest()
    assert json.loads((tmp_path/'backup/result.json').read_text()) == result


def test_compile_csv_rejects_missing_header_without_partial_files(tmp_path, monkeypatch):
    source = tmp_path/'broken.csv'
    source.write_text('"MAIN"\n"PLC Information:"\t"FXCPU"\n"wrong header"\n"0"\t""\t"END"\t""\n')
    output = tmp_path/'p.bin'
    monkeypatch.setattr(sys, 'argv', ['fx3u', 'compile-csv', str(source), str(output)])
    with pytest.raises(SystemExit) as error:
        cli.main()
    assert error.value.code == 2
    assert not output.exists()
    assert not output.with_suffix('.il').exists()
    assert not output.with_suffix('.map.json').exists()


def test_input_identity_survives_failed_deployment_and_recovery(tmp_path, monkeypatch):
    source = assemble(parse_il('END'))[0]
    (tmp_path/'p.bin').write_bytes(source)

    class Fake:
        def deploy(self, candidate, expected, directory, **kwargs):
            directory.mkdir()
            (directory/'result.json').write_text('{"error": "fault injection", "recovered": true}')
            raise RuntimeError('original deployment failure')

        def close(self):
            pass

    monkeypatch.setattr(Session, 'open', lambda *a: Fake())
    monkeypatch.setattr(sys, 'argv', arguments(tmp_path, execute=True, pad=True))
    with pytest.raises(RuntimeError, match='original deployment failure'):
        cli.main()
    report = json.loads((tmp_path/'wire.jsonl.input.json').read_text())
    assert report['source_sha256'] == hashlib.sha256(source).hexdigest()
    assert report['source_bytes'] == len(source)
    assert report['candidate_bytes'] == REGION_SIZE
    assert report['padded'] is True
    assert json.loads((tmp_path/'backup/result.json').read_text())['recovered'] is True
