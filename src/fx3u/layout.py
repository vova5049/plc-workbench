"""Fail-closed layout qualification. Header words 1..1 are the CPU-maintained sum16."""

import hashlib
import struct
from dataclasses import dataclass


@dataclass(frozen=True)
class ProgramLayout:
    name: str
    base: int
    capacity: int
    static_header_sha256: str


OBSERVED_16K = ProgramLayout(
    name="fx3u_3_11_observed_16k",
    base=0x1805C,
    capacity=32000,
    static_header_sha256="b4341b6b7ec4800e72757c47a2284d3ed0b309fab9dbf9bb2669679b2ec3fff4",
)


def static_header_digest(header):
    if len(header) != 92:
        raise ValueError("Wrong program-header length")
    return hashlib.sha256(header[:2] + b"\0\0" + header[4:]).hexdigest()


def qualify_program_header(header):
    if static_header_digest(header) != OBSERVED_16K.static_header_sha256:
        raise ValueError(
            "Unknown program header/layout; extended program access refused"
        )
    return OBSERVED_16K


def validate_program_checksum(header, program):
    qualify_program_header(header)
    if len(program) != OBSERVED_16K.capacity:
        raise ValueError("Wrong program-window length")
    actual = sum(value for (value,) in struct.iter_unpack("<H", program)) & 0xFFFF
    expected = struct.unpack_from("<H", header, 2)[0]
    if actual != expected:
        raise ValueError("Program/header checksum mismatch; no write permitted")
    return actual
