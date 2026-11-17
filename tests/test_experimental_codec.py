"""Independent literal encoding fixtures plus optional full private oracle campaign."""

import json
import os
from pathlib import Path

import pytest

from fx3u import experimental_codec as codec


@pytest.mark.parametrize(
    "il,expected",
    [
        ("END", "0f00"),
        ("LD M4095\nEND", "c203ff8431800f00"),
        ("LD M8511\nEND", "c201ff900f00"),
        ("MOV D1000 D0\nEND", "280000860088008600860f00"),
        ("MOV D8000 D0\nEND", "280000860080008600860f00"),
        ("DMOV K-1 D10\nEND", "2900ff80ff80ff80ff8014860086008000800f00"),
        ("MOV U7\\G32767 D0\nEND", "2800ff8c7f9c008600860f00"),
        ("MOV K1S1024 D0\nEND", "280000841482008600860f00"),
        ("OUT S0\nEND", "050000800f00"),
        ("RST T0Z0\nEND", "0c0200a406800f00"),
        ("STL S1022\nEND", "0e00fe830f00"),
        ("CJ P256\nEND", "1000008802800f00"),
        ("P256\nNOP\nEND", "00b101b2ffff0f00"),
        ("I620\nNOP\nEND", "6cbaffff0f00"),
        (
            "DEADD E1.5 D0 D0\nEND",
            "010100800090c0803f80008600860080008000860086008000800f00",
        ),
        ('$MOV "A B" D0\nEND', "b201418a2082428a0080008600860f00"),
        ("ASC K-1 D0\nEND", "a8004b8a2d80318a2080208a2080208a2080008600860f00"),
    ],
)
def test_independent_fixtures(il, expected):
    data, _ = codec.assemble(codec.parse_il(il))
    assert data.hex() == expected
    assert codec.assemble(codec.disassemble(data))[0] == data


@pytest.mark.parametrize(
    "il",
    [
        "MOV K8M0 D0\nEND",
        "DMOV D0V0 D10\nEND",
        "OUT T0 K-1\nEND",
        "MC N0 M8000\nEND",
        "MOV X378 D0\nEND",
        "LD M7680\nEND",
        "MOV D8512 D0\nEND",
        "MOV U8\\G0 D0\nEND",
        "CJ P4096\nEND",
        "MOV K32768 D0\nEND",
        "LD M0",
        "END\nNOP",
    ],
)
def test_rejected_forms(il):
    with pytest.raises(ValueError):
        codec.assemble(codec.parse_il(il))


@pytest.mark.parametrize(
    "data",
    [
        b"",
        b"\x0f",
        bytes.fromhex("28000086"),
        bytes.fromhex("0f000000"),
        bytes.fromhex("00b100000f00"),
    ],
)
def test_malformed_images(data):
    with pytest.raises(ValueError):
        codec.disassemble(data)


def test_optional_private_campaign():
    location = os.environ.get("FX3U_EXPERIMENTAL_CORPUS")
    if not location:
        pytest.skip("Set FX3U_EXPERIMENTAL_CORPUS to private campaign directory")
    root = Path(location)
    matched = 0
    rejected = 0
    names = [
        "matrix-corpus-final.jsonl",
        "operand-corpus.jsonl",
        "form-corpus.jsonl",
        "all-device-corpus.jsonl",
        "extra-corpus.jsonl",
        "pointer-corpus.jsonl",
        "missing-corpus.jsonl",
        "group-boundary-corpus.jsonl",
    ]
    for name in names:
        for line in (root / name).read_text().splitlines():
            row = json.loads(line)
            if row["status"] != "ok":
                with pytest.raises(ValueError):
                    codec.assemble(codec.parse_il(row["il"]))
                rejected += 1
            else:
                expected = bytes.fromhex(row["cpu"])
                data, _ = codec.assemble(codec.parse_il(row["il"]))
                assert data == expected, row["id"]
                assert codec.assemble(codec.disassemble(data))[0] == data, row["id"]
                matched += 1
    assert matched > 70000 and rejected > 29000


def test_offline_cli_never_opens_a_port(tmp_path, monkeypatch):
    import sys

    from fx3u import cli

    def forbidden(*args, **kwargs):
        raise AssertionError("Offline codec opened a serial port")

    monkeypatch.setattr(cli.Session, "open", forbidden)
    source = tmp_path / "source.il"
    source.write_text("DADD K1 D1000 D2000\nEND\n")
    binary = tmp_path / "image.bin"
    decoded = tmp_path / "decoded.il"
    monkeypatch.setattr(
        sys, "argv", ["fx3u", "compile-experimental", str(source), str(binary)]
    )
    cli.main()
    monkeypatch.setattr(
        sys, "argv", ["fx3u", "disassemble-experimental", str(binary), str(decoded)]
    )
    cli.main()
    assert codec.assemble(codec.parse_il(decoded.read_text()))[0] == binary.read_bytes()


def test_experimental_deploy_dry_run_is_offline(tmp_path, monkeypatch, capsys):
    import sys

    from fx3u import cli

    def forbidden(*a, **kw):
        raise AssertionError("Dry-run opened port")

    monkeypatch.setattr(cli.Session, "open", forbidden)
    image = tmp_path / "candidate.bin"
    image.write_bytes(codec.assemble(codec.parse_il("DADD K1 D0 D2\nEND"))[0])
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "fx3u",
            "deploy",
            str(image),
            "--experimental",
            "--pad-to-region",
            "--port",
            "COM254",
            "--wire-log",
            str(tmp_path / "wire.jsonl"),
            "--out-dir",
            str(tmp_path / "out"),
            "--expected-sha256",
            "0" * 64,
        ],
    )
    cli.main()
    assert json.loads(capsys.readouterr().out)["dry_run"] is True
    assert not (tmp_path / "wire.jsonl").exists()


@pytest.mark.parametrize("lose_ack", [False, True])
def test_explicit_decoder_keeps_backup_and_rollback(tmp_path, lose_ack):
    import threading

    from fx3u.client import BLOCK, REGION_SIZE, Session, digest

    class MemorySession(Session):
        def __init__(self, initial):
            self.memory = bytearray(initial)
            self.lock = threading.RLock()
            self.run = True
            self.fail = lose_ack

        def image(self):
            return bytes(self.memory)

        def running(self):
            return self.run

        def set_running(self, run):
            self.run = run

        def read_block(self, offset):
            return bytes(self.memory[offset : offset + BLOCK])

        def write_block(self, offset, data):
            self.memory[offset : offset + BLOCK] = data
            if self.fail:
                self.fail = False
                raise TimeoutError("Write succeeded; ACK lost")

    original = codec.assemble(codec.parse_il("END"))[0].ljust(REGION_SIZE, b"\xff")
    candidate = codec.assemble(codec.parse_il("DADD K1 D0 D2\nEND"))[0].ljust(
        REGION_SIZE, b"\xff"
    )
    session = MemorySession(original)
    if lose_ack:
        with pytest.raises(TimeoutError):
            session.deploy(
                candidate,
                digest(original),
                tmp_path / "deploy",
                decoder=codec.disassemble,
            )
        assert session.image() == original
    else:
        report = session.deploy(
            candidate, digest(original), tmp_path / "deploy", decoder=codec.disassemble
        )
        assert report["ok"] and session.image() == candidate
    assert (tmp_path / "deploy/before.bin").read_bytes() == original
    assert session.running()


def test_inventory_has_a_canonical_form_for_every_base_entry():
    from importlib.resources import files

    atlas = json.loads(files("fx3u").joinpath("capabilities.json").read_text())
    names = (
        set(codec.PROFILE["instructions"])
        | set(codec.PROFILE["bit_forms"])
        | {"MC", "MCR", "ASC", "NOP"}
    )
    assert len(atlas["instructions"]) == 250
    for row in atlas["instructions"]:
        evidence = row["experimental_encoder"]
        assert evidence["canonical_mnemonics"], row["mnemonic"]
        assert set(evidence["canonical_mnemonics"]) <= names
        assert evidence["all_forms_hardware_qualified"] is False


def test_experimental_catalog_exposes_modbus_encoding(monkeypatch, capsys):
    import sys

    from fx3u import cli

    monkeypatch.setattr(
        sys, "argv", ["fx3u", "catalog", "--experimental", "--mnemonic", "ADPRW"]
    )
    cli.main()
    row = json.loads(capsys.readouterr().out)[0]
    assert row["encoding_profile"]["ADPRW"]["opcode"] == 0x238
    assert row["encoding_profile"]["ADPRW"]["operands"] == 5
