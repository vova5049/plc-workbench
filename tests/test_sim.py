import json
import os
from pathlib import Path

import pytest

from fx3u.experimental_codec import disassemble, parse_il
from fx3u.sim import ScanSimulator


def model(source):
    return ScanSimulator(parse_il(source))


def test_real_internal_bench_program():
    sim = model(
        (Path(__file__).parents[1] / "examples/experimental/internal_codec_bench.il").read_text()
    )
    for _ in range(50):
        sim.step(dt_ms=10)
    expected = {
        0: 1234,
        1: 123,
        2: 1300,
        3: 1200,
        4: 65404,
        5: 65535,
        6: 11,
        7: 1,
        12: 0xF00,
        13: 0x5A0,
        14: 0xFF0,
        15: 0x1234,
        16: 1234,
        17: 0x8009,
        28: 5678,
    }
    for n, value in expected.items():
        assert sim.read("D" + str(n)) == value
    for n in [33, 40, 41, 42, 43]:
        assert sim.bit("M" + str(n))
    assert sim.read("D8", 32) == 12345678
    assert sim.read("D10", 32) == 12345700
    assert sim.read("D22", 32) == 0x40700000


def test_self_hold_and_bus_narrowing():
    sim = model("LD X0\nOR Y0\nANI X1\nOUT Y0\nAND X2\nOUT Y1\nEND")
    sim.step({"X0": True, "X2": False})
    assert sim.bit("Y0") and not sim.bit("Y1")
    sim.step({"X0": False, "X2": True})
    assert sim.bit("Y0") and sim.bit("Y1")
    sim.step({"X1": True})
    assert not sim.bit("Y0")


def test_pulses_are_per_instruction():
    sim = model("LD X0\nPLS M0\nPLS M1\nEND")
    sim.step({"X0": True})
    assert sim.bit("M0") and sim.bit("M1")
    sim.step()
    assert not sim.bit("M0") and not sim.bit("M1")
    sim.step({"X0": False})
    sim.step({"X0": True})
    assert sim.bit("M0") and sim.bit("M1")


def test_branch_stack_is_not_if_else():
    sim = model(
        "LD X0\nMPS\nAND X1\nOUT Y0\nMRD\nAND X2\nOUT Y1\nMPP\nAND X3\nOUT Y2\nEND"
    )
    sim.step({"X0": True, "X1": False, "X2": True, "X3": True})
    assert not sim.bit("Y0") and sim.bit("Y1") and sim.bit("Y2")


def test_counter_counts_edges_and_same_scan_reset_has_priority():
    sim = model("LD X1\nRST C0\nLD X0\nOUT C0 K2\nEND")
    sim.step({"X1": True, "X0": True})
    assert sim.read("C0") == 0
    sim.step({"X1": False})
    assert sim.read("C0") == 0
    sim.step({"X0": False})
    sim.step({"X0": True})
    assert sim.read("C0") == 1
    sim.step()
    assert sim.read("C0") == 1
    sim.step({"X0": False})
    sim.step({"X0": True})
    assert sim.bit("C0")


@pytest.mark.parametrize(
    "timer,dt,preset", [("T48", 50, 2), ("T200", 5, 2), ("T256", 0.5, 2)]
)
def test_timer_time_base_and_reset(timer, dt, preset):
    sim = model(f"LD X0\nOUT {timer} K{preset}\nEND")
    for _ in range(3):
        sim.step({"X0": True}, dt_ms=dt)
    assert not sim.bit(timer)
    sim.step(dt_ms=dt)
    assert sim.bit(timer)
    sim.step({"X0": False}, dt_ms=dt)
    assert not sim.bit(timer) and sim.read(timer) == 0


def test_rotation_shift_and_signed_division():
    sim = model(
        "LD M8002\nMOV H8001 D0\nROL D0 K1\nDIV K-100 K9 D1\nSET M10\nSFTL M10 M0 K4 K1\nEND"
    )
    sim.step()
    assert sim.read("D0") == 3 and sim.read("D1") == 65525 and sim.read("D2") == 65535
    assert sim.bit("M0") and not sim.bit("M1")


@pytest.mark.parametrize(
    "source",
    [
        "LD M8000\nADPRW K1 K3 K0 K1 D0\nEND",
        "LD M8000\nOUT C235 K1\nEND",
        "LD M8012\nOUT M0\nEND",
    ],
)
def test_unsupported_semantics_fail_before_scanning(source):
    with pytest.raises(NotImplementedError):
        model(source)


def test_runtime_value_errors_are_explicit():
    sim = model("LD M8000\nDIV K1 K0 D0\nEND")
    with pytest.raises(ValueError, match="Division by zero"):
        sim.step()
    with pytest.raises(ValueError):
        model("END").step(dt_ms=0)


def test_optional_differential_against_cpu_samples():
    directory = os.environ.get("FX3U_EXPERIMENTAL_CORPUS")
    if not directory:
        pytest.skip("Set FX3U_EXPERIMENTAL_CORPUS for captured CPU samples")
    for name in ["native-codec-bench-2", "native-codec-bench-3"]:
        root = Path(directory) / name
        observed = json.loads((root / "result.json").read_text())["after"]
        sim = ScanSimulator(disassemble((root / "candidate.bin").read_bytes()))
        for _ in range(50):
            sim.step(dt_ms=10)
        assert [sim.read(f"D{i}") for i in range(32)] == observed["D"]
        assert sim.read("D120") == observed["D120"]
        assert sim.read("C0") == observed["C0"] and sim.read("T48") == observed["T48"]
        actual = int.from_bytes(bytes.fromhex(observed["M0_47"]), "little")
        assert sum(int(sim.bit("M" + str(i))) << i for i in range(48)) == actual


def test_simulate_cli_with_input_trace(tmp_path, monkeypatch, capsys):
    import sys

    from fx3u import cli

    program = tmp_path / "pulse.il"
    program.write_text("LD X0\nPLS M1\nEND")
    trace = tmp_path / "trace.json"
    trace.write_text(
        json.dumps(
            [
                {"inputs": {"X0": True}},
                {"inputs": {"X0": True}},
                {"inputs": {"X0": False}},
            ]
        )
    )

    def forbidden(*a, **kw):
        raise AssertionError("Simulator opened a port")

    monkeypatch.setattr(cli.Session, "open", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        ["fx3u", "simulate", str(program), "--trace", str(trace), "--watch", "M1"],
    )
    cli.main()
    rows = [json.loads(line) for line in capsys.readouterr().out.splitlines()]
    assert [row["values"]["M1"] for row in rows] == [True, False, False]
