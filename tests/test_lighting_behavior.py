"""Offline scan regressions; virtual timings do not qualify physical PLC behavior."""
from pathlib import Path
import importlib.util

import pytest

from fx3u.experimental_codec import parse_il
from fx3u.sim import ScanSimulator

EXAMPLE = Path(__file__).parents[1] / 'examples/lighting-case-study'
PROGRAM = parse_il((EXAMPLE / 'program.il').read_text())
LEGACY_PROGRAM = parse_il((EXAMPLE / 'legacy-reference.il').read_text())
spec = importlib.util.spec_from_file_location('lighting_station', EXAMPLE / 'build_station.py')
station_builder = importlib.util.module_from_spec(spec)
spec.loader.exec_module(station_builder)


def address(family, index):
    return family + format(index, 'o')


@pytest.mark.parametrize('button', range(24))
@pytest.mark.parametrize('gesture,clicks,code', [('single', 1, 1), ('double', 2, 2),
                                               ('triple', 3, 3), ('long', 1, 4)])
def test_active_gestures_on_each_input(button, gesture, clicks, code):
    sim = ScanSimulator(PROGRAM)
    source = address('X', button)
    sim.step({source: False})
    for click in range(clicks):
        sim.step({source: True}, dt_ms=2000 if gesture == 'long' else 10)
        sim.step({source: False}, dt_ms=50)
    sim.step(dt_ms=350)
    assert sim.read('D100') == 1
    assert sim.read('D101') == button * 10 + code
    assert [i for i in range(24) if sim.bit(address('Y', i))] == ([button] if gesture == 'single' else [])
    sim.step(dt_ms=500)
    assert sim.read('D100') == 1  # no delayed second command


@pytest.mark.parametrize('silence,events', [(399, 0), (400, 1), (401, 1)])
def test_silence_boundary_in_virtual_time(silence, events):
    sim = ScanSimulator(PROGRAM)
    sim.step({'X0': True}, dt_ms=10)
    sim.step({'X0': False}, dt_ms=silence)
    assert sim.read('D100') == events
    assert sim.bit('Y0') == bool(events)


@pytest.mark.parametrize('held,events', [(1999, 0), (2000, 1), (2001, 1)])
def test_long_boundary_in_virtual_time(held, events):
    sim = ScanSimulator(PROGRAM)
    sim.step({'X0': True}, dt_ms=held)
    assert sim.read('D100') == events
    assert not sim.bit('Y0')
    if events:
        assert sim.read('D101') == 4
        sim.step({'X0': False}, dt_ms=500)
        assert sim.read('D100') == 1


def test_long_after_short_does_not_leave_a_delayed_single():
    sim = ScanSimulator(PROGRAM)
    sim.step({'X0': True}, dt_ms=10)
    sim.step({'X0': False}, dt_ms=50)
    sim.step({'X0': True}, dt_ms=2000)
    sim.step({'X0': False}, dt_ms=500)
    assert sim.read('D100') == 1
    assert sim.read('D101') == 4
    assert not sim.bit('Y0')


@pytest.mark.parametrize('pulse_ms', [1, 5, 10, 20, 30])
def test_short_repeated_edges_are_not_debounced_by_active_logic(pulse_ms):
    # Documents the existing limitation, without asserting a physical filter setting.
    sim = ScanSimulator(PROGRAM)
    for _ in range(2):
        sim.step({'X0': True}, dt_ms=pulse_ms)
        sim.step({'X0': False}, dt_ms=pulse_ms)
    sim.step(dt_ms=400 - pulse_ms)
    assert sim.read('D100') == 1
    assert sim.read('D101') == 2
    assert not sim.bit('Y0')


@pytest.mark.parametrize('off_button,zone', [(0, 1), (1, 0)])
def test_unqualified_off_toggle_conflicts_are_refused_in_either_order(off_button, zone):
    with pytest.raises(ValueError, match='OFF/TOGGLE'):
        station_builder.build_station('LAB', 2, 'K4', 'K20',
                             lambda station, button: [button],
                             lambda station, button: [],
                             lambda station, button: [zone] if button == off_button else [])


GESTURES = [('single', 1, 1), ('double', 2, 2), ('triple', 3, 3), ('long', 1, 4)]


def perform_gesture(sim, source, gesture, clicks):
    for click in range(clicks):
        sim.step({source: True}, dt_ms=10)
        if gesture == 'long':
            sim.step(dt_ms=1990)
        sim.step({source: False}, dt_ms=(10 if gesture == 'long' else
                                        400 if click == clicks - 1 else 50))


@pytest.mark.parametrize('button', range(24))
@pytest.mark.parametrize('first,first_clicks,first_code', GESTURES)
@pytest.mark.parametrize('second,second_clicks,second_code', GESTURES)
def test_successive_gestures_without_an_idle_scan(button, first, first_clicks, first_code,
                                                second, second_clicks, second_code):
    sim = ScanSimulator(PROGRAM)
    source = address('X', button)
    perform_gesture(sim, source, first, first_clicks)
    assert sim.read('D100') == 1
    assert sim.read('D101') == button * 10 + first_code
    # The next edge follows the previous gesture's finalization/release scan.
    perform_gesture(sim, source, second, second_clicks)
    assert sim.read('D100') == 2
    assert sim.read('D101') == button * 10 + second_code
    expected_on = bool((first == 'single') + (second == 'single') == 1)
    assert sim.bit(address('Y', button)) == expected_on
    sim.step(dt_ms=500)
    assert sim.read('D100') == 2


def test_legacy_reference_retains_reproduced_click_loss():
    sim = ScanSimulator(LEGACY_PROGRAM)
    perform_gesture(sim, 'X0', 'single', 1)
    perform_gesture(sim, 'X0', 'single', 1)
    assert sim.read('D100') == 1
    assert sim.bit('Y0')


def test_active_il_changes_only_the_edge_priority_rung_in_each_button():
    assert len(PROGRAM) == len(LEGACY_PROGRAM) == 1585
    differences = [i for i, pair in enumerate(zip(PROGRAM, LEGACY_PROGRAM))
                   if pair[0] != pair[1]]
    assert len(differences) == 3 * 24
    for button in range(24):
        # Each station button keeps the same 50 instructions and allocations.
        start = button * 50 + 4
        assert differences[button * 3:button * 3 + 3] == list(range(start, start + 3))
        assert [PROGRAM[start + i].mnemonic for i in range(3)] == ['LD', 'ANI', 'OR']


@pytest.mark.parametrize('kind', ['single', 'double', 'long_off'])
@pytest.mark.parametrize('zone', [-24, -1, False, True, 1.0, '0', None, 24, 999])
def test_invalid_zone_indices_are_refused_before_emitting_il(monkeypatch, kind, zone):
    callbacks = {key: (lambda station, button: []) for key in ['single', 'double', 'long_off']}
    callbacks[kind] = lambda station, button: [zone] if button == 23 else []

    def forbidden_builder():
        pytest.fail('IL builder created before complete mapping validation')

    monkeypatch.setattr(station_builder, 'Prog', forbidden_builder)
    with pytest.raises(ValueError, match='Invalid zone index.*' + kind):
        station_builder.build_station('LAB', 24, 'K4', 'K20', callbacks['single'],
                                      callbacks['double'], callbacks['long_off'])


@pytest.mark.parametrize('kind', ['single', 'double', 'long_off'])
@pytest.mark.parametrize('zone', [0, 23])
def test_valid_zone_boundary_indices_are_accepted(kind, zone):
    callbacks = {key: (lambda station, button: []) for key in ['single', 'double', 'long_off']}
    callbacks[kind] = lambda station, button: iter([zone]) if button == 0 else iter([])
    program, zones = station_builder.build_station('LAB', 24, 'K4', 'K20',
                                                   callbacks['single'], callbacks['double'],
                                                   callbacks['long_off'])
    target = program.sym[zones[zone]]
    assert any(row[:2] == ('RST' if kind == 'long_off' else 'OUT', target)
               for row in program.code)
