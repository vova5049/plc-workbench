import threading

import pytest

from fx3u import layout
from fx3u.client import BASE, BLOCK, Session, frame


def test_unknown_layout_refuses_extended_access():
    with pytest.raises(ValueError):
        layout.qualify_program_header(bytes(92))
    with pytest.raises(ValueError):
        layout.qualify_program_header(bytes(91))


def test_qualification_and_final_partial_block(monkeypatch):
    header = bytes(92)
    observed = layout.ProgramLayout(
        "fixture", BASE, 32000, layout.static_header_digest(header)
    )
    monkeypatch.setattr(layout, "OBSERVED_16K", observed)

    class Fake(Session):
        def __init__(self):
            self.lock = threading.RLock()
            self.tx = []
            self.acks = []

        def exchange(self, tx):
            self.tx.append(tx)
            if tx == frame("E0180005C"):
                return frame(header.hex().upper())
            if tx == frame("E01FC62FA"):
                return frame("FF" * 250)
            raise AssertionError(tx)

        def ack(self, tx):
            self.acks.append(tx)

    s = Fake()
    with pytest.raises(ValueError):
        s.read_block(31750)
    assert s.qualify_full_region() == observed
    assert s.region_size == 32000
    assert s.read_block(31750) == b"\xff" * 250
    s.write_block(31750, b"\xff" * 250)
    assert s.acks == [frame("E11FC62FA" + "FF" * 250)]
    with pytest.raises(ValueError):
        s.write_block(31750, b"\xff" * BLOCK)
    with pytest.raises(ValueError):
        s.read_block(32000)
    with pytest.raises(ValueError):
        s.read_block(1)


def test_full_region_last_block_rollback(tmp_path, monkeypatch):
    from fx3u.client import digest
    from fx3u.experimental_codec import disassemble

    header = bytes(92)
    monkeypatch.setattr(
        layout,
        "OBSERVED_16K",
        layout.ProgramLayout(
            "fixture", BASE, 32000, layout.static_header_digest(header)
        ),
    )
    original = bytes.fromhex("0f00") + b"\xff" * 31998
    candidate = b"\xff" * 31998 + bytes.fromhex("0f00")

    class Fake(Session):
        def __init__(self):
            self.lock = threading.RLock()
            self.memory = bytearray(original)
            self.run = True
            self.fail = True
            self.offsets = []

        def exchange(self, tx):
            assert tx == frame("E0180005C")
            import struct

            checksum = sum(v for (v,) in struct.iter_unpack("<H", self.memory)) & 0xFFFF
            current_header = header[:2] + struct.pack("<H", checksum) + header[4:]
            return frame(current_header.hex().upper())

        def image(self):
            return bytes(self.memory)

        def running(self):
            return self.run

        def set_running(self, run):
            self.run = run

        def read_block(self, offset):
            return bytes(self.memory[offset : offset + BLOCK])

        def write_block(self, offset, data):
            self.offsets.append((offset, len(data)))
            self.memory[offset : offset + len(data)] = data
            if offset == 31750 and self.fail:
                self.fail = False
                raise TimeoutError("Final write ACK lost")

    s = Fake()
    s.qualify_full_region()
    with pytest.raises(TimeoutError):
        s.deploy(candidate, digest(original), tmp_path / "deploy", decoder=disassemble)
    assert s.image() == original and s.running()
    assert (31750, 250) in s.offsets
    assert layout.static_header_digest(
        (tmp_path / "deploy/program-header.bin").read_bytes()
    ) == layout.static_header_digest(header)
    assert (tmp_path / "deploy/before.bin").read_bytes() == original


def test_dynamic_sum_is_checked_separately_from_static_fields(monkeypatch):
    import struct

    header = bytes(92)
    monkeypatch.setattr(
        layout,
        "OBSERVED_16K",
        layout.ProgramLayout(
            "fixture", BASE, 32000, layout.static_header_digest(header)
        ),
    )
    program = bytes.fromhex("0f00") + b"\xff" * 31998
    checksum = sum(v for (v,) in struct.iter_unpack("<H", program)) & 65535
    good = header[:2] + struct.pack("<H", checksum) + header[4:]
    assert layout.qualify_program_header(good) == layout.OBSERVED_16K
    assert layout.validate_program_checksum(good, program) == checksum
    bad = good[:2] + struct.pack("<H", checksum ^ 1) + good[4:]
    assert layout.qualify_program_header(bad) == layout.OBSERVED_16K
    with pytest.raises(ValueError, match="checksum"):
        layout.validate_program_checksum(bad, program)
    for offset in [0, 1, 4, 91]:
        damaged = bytearray(good)
        damaged[offset] ^= 1
        with pytest.raises(ValueError, match="Unknown"):
            layout.qualify_program_header(damaged)
