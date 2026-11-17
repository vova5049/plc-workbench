"""Example validation must finish before publishing any station's artifacts."""
import hashlib
import importlib.util
from pathlib import Path
import sys

import pytest


EXAMPLE = Path(__file__).resolve().parents[1] / 'examples/lighting-case-study'


@pytest.fixture
def generator(monkeypatch):
    monkeypatch.syspath_prepend(str(EXAMPLE))
    # Keep the example's generic sibling module names local to this fixture.
    for name in ('config', 'build_station'):
        spec = importlib.util.spec_from_file_location(name, EXAMPLE / f'{name}.py')
        module = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, name, module)
        spec.loader.exec_module(module)
    spec = importlib.util.spec_from_file_location('lighting_generator', EXAMPLE / 'generate.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize('failure_station', [2, 3])
@pytest.mark.parametrize('existing', [False, True])
def test_later_validation_failure_leaves_destination_unchanged(
        generator, monkeypatch, tmp_path, failure_station, existing):
    destination = tmp_path / 'result'
    original = {}
    if existing:
        destination.mkdir()
        for station in ('PLC1', 'PLC2', 'PLC3'):
            for suffix in ('.csv', '_COMMENT.csv', '.bin', '.il'):
                name = station + suffix
                original[name] = f'previous artifact {name}'.encode()
                (destination / name).write_bytes(original[name])
        original['unrelated.txt'] = b'keep this too'
        (destination / 'unrelated.txt').write_bytes(original['unrelated.txt'])
    real_validation = generator.validate_compiled_steps
    calls = 0

    def reject_later_station(rows, mapping):
        nonlocal calls
        calls += 1
        real_validation(rows, mapping)
        if calls == failure_station:
            raise ValueError('injected later-station validation failure')

    monkeypatch.setattr(generator, 'validate_compiled_steps', reject_later_station)
    monkeypatch.setattr(sys, 'argv', ['generate.py', '--out', str(destination)])
    with pytest.raises(ValueError, match='later-station validation failure'):
        generator.main()
    assert calls == failure_station
    if existing:
        assert {p.name: p.read_bytes() for p in destination.iterdir()} == original
    else:
        assert not destination.exists()


def test_success_publishes_all_three_stations_with_unchanged_images(
        generator, monkeypatch, tmp_path):
    destination = tmp_path / 'result'
    monkeypatch.setattr(sys, 'argv', ['generate.py', '--out', str(destination)])
    generator.main()
    expected_names = {station + suffix for station in ('PLC1', 'PLC2', 'PLC3')
                      for suffix in ('.csv', '_COMMENT.csv', '.bin', '.il')}
    assert {p.name for p in destination.iterdir()} == expected_names
    expected_hash = '499f966aef619067b1ce6aaed78ee74eb392bc519d92442119c67d7f5429a38c'
    for station in ('PLC1', 'PLC2', 'PLC3'):
        image = (destination / f'{station}.bin').read_bytes()
        assert len(image) == 5954
        assert hashlib.sha256(image).hexdigest() == expected_hash
