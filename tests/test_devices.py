"""Device protocol boundary and uncertain-write recovery tests."""

import json
import threading

import pytest

from fx3u.client import frame, payload
from fx3u.devices import device, edit, edit_request, monitor


class Memory:
    def __init__(self, *, lose_ack=False, fail_recovery=False, overwrite=False):
        self.lock = threading.RLock()
        self.memory = bytearray(0x2000)
        self.run = True
        self.writes = []
        self.reads = []
        self.lose_ack = lose_ack
        self.fail_recovery = fail_recovery
        self.overwrite = overwrite

    def running(self):
        return self.run

    def set_running(self, run):
        self.run = run
        if run and self.overwrite:
            self.memory[0x10F0:0x10F2] = b"\x63\x00"

    def read(self, address, count):
        self.reads.append((address, count))
        return bytes(self.memory[address : address + count])

    def ack(self, request):
        assert not self.run
        body = payload(request).decode()
        if self.writes and self.fail_recovery:
            raise OSError("Disconnected during recovery")
        self.writes.append(request)
        if body[0] in "78":
            bit_addr = int.from_bytes(bytes.fromhex(body[1:]), "little") - 0x800
            byte_addr, bit = 0x100 + bit_addr // 8, bit_addr % 8
            if body[0] == "7":
                self.memory[byte_addr] |= 1 << bit
            else:
                self.memory[byte_addr] &= ~(1 << bit)
        elif body[0] == "1":
            address = int(body[1:5], 16)
            self.memory[address : address + 2] = bytes.fromhex(body[7:])
        else:
            raise AssertionError(body)
        if self.lose_ack:
            self.lose_ack = False
            raise TimeoutError("Write applied but ACK lost")


def test_literal_protocol_and_octal_boundaries():
    assert edit_request("M0", 1)[1] == frame("70008")
    assert edit_request("M255", 0)[1] == frame("8FF08")
    assert edit_request("D120", 0x1234)[1] == frame("110F0023412")
    assert device("Y10").address == 0xA1 and device("Y27").bit == 7
    assert device("D127").address == 0x10FE


@pytest.mark.parametrize(
    "label",
    [
        "Y30",
        "X8",
        "M256",
        "M8000",
        "D128",
        "D8000",
        "T49",
        "C1",
        "S0",
        "D0Z0",
        "M-1",
        "M",
    ],
)
def test_unknown_ranges_fail_before_io(label):
    with pytest.raises(ValueError):
        device(label)


@pytest.mark.parametrize(
    "label,value",
    [("Y0", 1), ("X0", 1), ("T0", 1), ("C0", 1), ("M0", 2), ("D0", -1), ("D0", 65536)],
)
def test_unqualified_edits_rejected(label, value):
    with pytest.raises(ValueError):
        edit_request(label, value)


def test_monitor_coalesces_and_preserves_signed_raw_words():
    s = Memory()
    s.memory[0xA1] = 0x81
    s.memory[0x1000:0x1004] = b"\xff\xff\x01\x80"
    result = monitor(s, ["Y10", "Y17", "D0", "D1", "D0"])
    assert result["values"] == {"Y10": True, "Y17": True, "D0": 65535, "D1": 32769}
    assert s.reads == [(0xA1, 1), (0x1000, 4)]
    assert result["atomic"] is False


@pytest.mark.parametrize("label,value", [("M0", 1), ("M7", 0), ("D120", 0x1234)])
def test_edit_backups_and_resumes(tmp_path, label, value):
    s = Memory()
    s.memory[0x100] = 0xAA
    dev = device(label)
    before = bytes(s.memory)
    expected = int(dev.decode(s.read(dev.address, dev.size)))
    report = edit(s, label, value, expected, tmp_path / "edit")
    assert report["ok"] and s.run and report["after_resume"] == value
    assert (tmp_path / "edit/before.bin").read_bytes() == before[
        dev.address : dev.address + dev.size
    ]
    changed = {i for i, (a, b) in enumerate(zip(before, s.memory)) if a != b}
    assert changed <= set(range(dev.address, dev.address + dev.size))


def test_expected_mismatch_writes_nothing_and_resumes(tmp_path):
    s = Memory()
    with pytest.raises(ValueError, match="Expected device"):
        edit(s, "D120", 123, 99, tmp_path / "edit")
    assert s.run and not s.writes


@pytest.mark.parametrize("fail_recovery", [False, True])
def test_uncertain_ack_restores_or_leaves_stop(tmp_path, fail_recovery):
    s = Memory(lose_ack=True, fail_recovery=fail_recovery)
    with pytest.raises(TimeoutError):
        edit(s, "M0", 1, 0, tmp_path / "edit")
    report = json.loads((tmp_path / "edit/result.json").read_text())
    if fail_recovery:
        assert not s.run and "recovery_error" in report
    else:
        assert s.run and s.memory[0x100] == 0 and report["recovered"]


def test_ladder_overwrite_reported_separately(tmp_path):
    s = Memory(overwrite=True)
    report = edit(s, "D120", 12, 0, tmp_path / "edit")
    assert report["ok"] and report["stopped_readback"] == 12
    assert report["after_resume"] == 99 and report["overwritten_after_resume"]


def test_dry_run_never_opens_port(tmp_path, monkeypatch, capsys):
    import sys

    from fx3u import cli

    def forbidden(*a, **kw):
        raise AssertionError("Dry-run opened a port")

    monkeypatch.setattr(cli.Session, "open", forbidden)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx3u",
            "device-write",
            "D120",
            "123",
            "--expected-value",
            "0",
            "--port",
            "COM254",
            "--wire-log",
            str(tmp_path / "wire.jsonl"),
            "--out-dir",
            str(tmp_path / "out"),
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["dry_run"]
    assert not list(tmp_path.iterdir())
