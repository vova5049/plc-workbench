"""Allow-listed writes to a RUNning CPU: FORCE of one M or Y bit, or a write of D words / M bytes.

SELECTED FORMS QUALIFIED ON ONE BENCH; not blanket hardware qualification.
See docs/bench-qualification-20261114.md for M250 ON/OFF, D44/D45 word writes,
M240..M255 byte writes and Y0 ON/OFF with independent readbacks/restoration.
Earlier evidence also included Y1 ON. Other addresses/spans, CPU variants and
physical signal/load behaviour remain unqualified. Generic result flags remain
false: they do not attest the identity of the particular connected controller.

Frames, as built here:

    FORCE ON / OFF   '7' / '8' + (0x0800 + n) as 4 hex digits, little endian      (bit M<n>)
                     '7' / '8' + (0x0500 + i) as 4 hex digits, little endian      (bit Y<octal i>)
    write            '1' + address (4 hex) + byte count (2 hex) + data hex          (D words LE, M bytes)
    read-back        '0' + address (4 hex) + byte count (2 hex)       (Y image: 0x00A0 + i // 8)
    D<n> = 0x1000 + 2*n,  M byte for M<8k..8k+7> = 0x0100 + k

Guarantees enforced here, before the first byte is transmitted (except the RUN/STOP status read,
which is a read and happens after the allow-list check):

* an allow-list is mandatory both when planning and executing a write;
* execution rebuilds and compares the entire plan before any session I/O;
* every written element must be inside the allow-list (a span that exceeds it by one is refused);
* only M0..M255, Y0..Y27 (octal; FORCE only) and D0..D127 can be targeted: no X/T/C/S, no
  special devices (M8000+, D8000+), no word/byte write to Y;
* the CPU must report RUN; a CPU in STOP is refused after the status read (no write is sent).

The module never sends STOP or RUN and never retries. A write that is not confirmed (lost ACK,
garbage reply, read-back that differs or fails) is reported as ``uncertain``; the caller decides.
A NAK is the only reply that proves the write was not executed (``nak``).
"""

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .client import frame

M_MAX, D_MAX, Y_MAX = 255, 127, 23  # devices.device ranges; Y0..Y27 octal = indices 0..23
MAX_UNITS = 32  # words (D) or bytes (M) per write
M_BASE, D_BASE, FORCE_BASE, Y_FORCE_BASE, Y_IMAGE = 0x0100, 0x1000, 0x0800, 0x0500, 0x00A0
NAK_HEX = "15"


class LiveWriteRefused(ValueError):
    """Refusal; nothing was written (the RUN/STOP status read may have happened)."""


# --------------------------------------------------------------------------- allow-list

_ENTRY = re.compile(r"([MDY])(0|[1-9][0-9]*)(?:-([MDY])(0|[1-9][0-9]*))?", re.IGNORECASE)


def _limit(kind):
    return {"M": M_MAX, "D": D_MAX, "Y": Y_MAX}[kind]


def _number(kind, digits):
    """Device number as an index; Y is octal (Y10 = index 8), M and D are decimal."""
    if kind == "Y":
        if any(c in "89" for c in digits):
            raise LiveWriteRefused(f"Y{digits}: Y numbers are octal")
        return int(digits, 8)
    return int(digits)


def _name(kind, index):
    return f"Y{index:o}" if kind == "Y" else f"{kind}{index}"


class AllowList:
    """Immutable set of permitted devices, e.g. ``["M240-M255", "D24-D49", "M250", "Y0-Y7"]``.

    Y numbers are octal, as on the PLC (``Y0-Y27`` = 24 outputs)."""

    def __init__(self, entries):
        if isinstance(entries, (str, bytes)) or not isinstance(entries, (list, tuple)) or not entries:
            raise LiveWriteRefused("allow-list must be a non-empty list of M/D ranges")
        ranges = []
        for entry in entries:
            if not isinstance(entry, str):
                raise LiveWriteRefused(f"allow-list entry is not a string: {entry!r}")
            match = _ENTRY.fullmatch(entry.strip())
            if not match:
                raise LiveWriteRefused(
                    f"allow-list entry {entry!r}: use M<n>, D<n>, Y<n> or a range such as M<a>-M<b> "
                    "(only M0..M255, D0..D127, Y0..Y27 octal)"
                )
            kind, first, kind2, last = match.groups()
            kind = kind.upper()
            first = _number(kind, first)
            last = first if last is None else _number(kind, last)
            if kind2 is not None and kind2.upper() != kind:
                raise LiveWriteRefused(f"allow-list entry {entry!r}: mixed device kinds")
            if first > last:
                raise LiveWriteRefused(f"allow-list entry {entry!r}: range is reversed")
            if last > _limit(kind):
                raise LiveWriteRefused(
                    f"allow-list entry {entry!r}: outside the qualified range "
                    f"{kind}0..{_name(kind, _limit(kind))}"
                )
            ranges.append((kind, first, last))
        self.ranges = tuple(sorted(set(ranges)))

    @classmethod
    def load(cls, path):
        try:
            data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
        except (OSError, ValueError) as exc:
            raise LiveWriteRefused(f"cannot read allow-list {path}: {exc}") from None
        if not isinstance(data, dict) or set(data) != {"allow"}:
            raise LiveWriteRefused('allow-list file must be a JSON object {"allow": [...]} and nothing else')
        return cls(data["allow"])

    def permits(self, kind, first, count=1):
        """True when every element ``kind``<first>..<first+count-1> lies inside some entry."""
        return count >= 1 and all(
            any(k == kind and lo <= n <= hi for k, lo, hi in self.ranges)
            for n in range(first, first + count)
        )

    def entries(self):
        return [
            _name(k, lo) if lo == hi else f"{_name(k, lo)}-{_name(k, hi)}" for k, lo, hi in self.ranges
        ]

    def __repr__(self):
        return f"AllowList({self.entries()!r})"


def _require_allow(allow):
    if not isinstance(allow, AllowList):
        raise LiveWriteRefused("an allow-list (AllowList) is mandatory for live writes")
    return allow


# --------------------------------------------------------------------------- planning

@dataclass(frozen=True)
class Plan:
    """Prepared write data, not an authorization token; execution revalidates it."""

    kind: str  # 'force' | 'write'
    device: str
    request: bytes
    read_request: bytes
    expected: object  # bool for force, list[int] for write
    read_address: int
    read_length: int
    unit: str  # 'bit' | 'word' | 'byte'
    bit: int = 0  # bit position inside the read-back byte (unit == 'bit')

    def decode(self, raw):
        if self.unit == "bit":
            return bool(raw[0] & (1 << self.bit))
        if self.unit == "word":
            return [int.from_bytes(raw[i : i + 2], "little") for i in range(0, len(raw), 2)]
        return list(raw)


def _target(label):
    if not isinstance(label, str):
        raise LiveWriteRefused("device label must be a string")
    match = re.fullmatch(r"([A-Za-z])(0|[1-9][0-9]*)", label)
    if not match:
        raise LiveWriteRefused(f"{label!r}: use M<n> or D<n>")
    kind, number = match.group(1).upper(), int(match.group(2))
    if kind not in "MDY":
        raise LiveWriteRefused(f"{label!r}: live writes accept only M, D and Y devices (no X/T/C/S)")
    if kind == "Y":
        number = _number("Y", match.group(2))
    if number > _limit(kind):
        raise LiveWriteRefused(
            f"{label!r}: outside the qualified range {kind}0..{_name(kind, _limit(kind))} "
            "(special devices refused)"
        )
    return kind, number


def _read_frame(address, count):
    return frame(f"0{address:04X}{count:02X}")


def plan_force(label, on, allow):
    allow = _require_allow(allow)
    kind, number = _target(label)
    if kind == "D":
        raise LiveWriteRefused(f"{label!r}: FORCE applies to M and Y bits; use write_units for D words")
    if type(on) not in (bool, int) or on not in (0, 1):
        raise LiveWriteRefused("FORCE value must be 0/1 (False/True)")
    name = _name(kind, number)
    if not allow.permits(kind, number):
        raise LiveWriteRefused(f"{name} is not in the allow-list {allow.entries()}")
    if kind == "M":
        force_address, address = FORCE_BASE + number, M_BASE + number // 8
    else:
        force_address, address = Y_FORCE_BASE + number, Y_IMAGE + number // 8
    request = frame(("7" if on else "8") + force_address.to_bytes(2, "little").hex().upper())
    return Plan("force", name, request, _read_frame(address, 1), bool(on), address, 1, "bit", number % 8)


def plan_write(label, values, allow):
    allow = _require_allow(allow)
    kind, number = _target(label)
    if kind == "Y":
        raise LiveWriteRefused(f"{label!r}: outputs can only be FORCEd (no word or byte writes to Y)")
    if isinstance(values, (str, bytes)) or not isinstance(values, (list, tuple)):
        raise LiveWriteRefused("values must be a list of integers")
    if not 1 <= len(values) <= MAX_UNITS:
        raise LiveWriteRefused(f"write 1..{MAX_UNITS} values at a time")
    top = 0xFFFF if kind == "D" else 0xFF
    if any(type(v) is not int or not 0 <= v <= top for v in values):
        raise LiveWriteRefused(f"{kind} values must be integers 0..{top}")
    values = list(values)
    if kind == "D":
        if not allow.permits("D", number, len(values)):
            raise LiveWriteRefused(
                f"D{number}..D{number + len(values) - 1} is not entirely in the allow-list {allow.entries()}"
            )
        address, data, unit = D_BASE + 2 * number, b"".join(v.to_bytes(2, "little") for v in values), "word"
    else:
        if number % 8:
            raise LiveWriteRefused(f"M byte writes must start at a multiple of 8 (got M{number})")
        if not allow.permits("M", number, 8 * len(values)):
            raise LiveWriteRefused(
                f"M{number}..M{number + 8 * len(values) - 1} is not entirely in the allow-list {allow.entries()}"
            )
        address, data, unit = M_BASE + number // 8, bytes(values), "byte"
    request = frame(f"1{address:04X}{len(data):02X}{data.hex().upper()}")
    return Plan("write", f"{kind}{number}", request, _read_frame(address, len(data)), values, address, len(data), unit)


def plan_label(label, value, allow):
    """CLI helper: M<n> / Y<n> -> FORCE, D<n> -> single word write."""
    kind, _ = _target(label)
    return plan_write(label, [value], allow) if kind == "D" else plan_force(label, value, allow)


# --------------------------------------------------------------------------- execution

@dataclass
class LiveResult:
    ok: bool
    status: str  # 'ok' | 'nak' | 'uncertain'
    device: str
    kind: str
    request_hex: str
    acked: bool
    expected: object
    readback: object = None
    detail: str = ""
    retried: bool = False  # always False: this module never retries
    qualified_on_hardware: bool = False  # flip only with a hardware trace behind it

    def as_dict(self):
        return dict(self.__dict__)


def _nak(exc):
    text = str(exc)
    prefix = "NAK/unexpected reply: "
    return text.startswith(prefix) and text[len(prefix) :].strip() == NAK_HEX


def _validate_plan(plan, allow):
    """Rebuild from intent and policy, never trust stored wire/readback fields."""
    allow = _require_allow(allow)
    if type(plan) is not Plan:
        raise LiveWriteRefused("execution requires a Plan from the live-write builders")
    if plan.kind == "force":
        validated = plan_force(plan.device, plan.expected, allow)
    elif plan.kind == "write":
        validated = plan_write(plan.device, plan.expected, allow)
    else:
        raise LiveWriteRefused("unknown live-write plan kind")
    if plan != validated:
        raise LiveWriteRefused("plan differs from its validated intent; rebuild it with the execution allow-list")
    return validated


def run_plan(session, plan, allow):
    """Revalidate before I/O, check RUN, send ONE write and read-back; no retry.

    The execution allow-list is mandatory even for a previously built Plan.
    This bounded helper is not a sandbox for code holding a raw Session.
    """
    plan = _validate_plan(plan, allow)
    with session.lock:
        try:
            running = session.running()
        except Exception as exc:  # noqa: BLE001 -- nothing was written yet
            raise LiveWriteRefused(f"cannot confirm RUN before writing: {type(exc).__name__}: {exc}") from None
        if not running:
            raise LiveWriteRefused("CPU reports STOP: live writes need RUN; no write was sent")
        result = LiveResult(False, "uncertain", plan.device, plan.kind, plan.request.hex(), False, plan.expected)
        try:
            reply = session.exchange(plan.request)
        except Exception as exc:  # noqa: BLE001 -- the write may or may not have executed
            if isinstance(exc, ValueError) and _nak(exc):
                result.status, result.detail = "nak", str(exc)
            else:
                result.detail = f"{type(exc).__name__}: {exc}"
            return result
        if reply != b"\x06":
            result.detail = f"unexpected reply {bytes(reply).hex()}"
            return result
        result.acked = True
        try:
            result.readback = plan.decode(session.read(plan.read_address, plan.read_length))
        except Exception as exc:  # noqa: BLE001 -- acknowledged but unverified
            result.detail = f"read-back failed: {type(exc).__name__}: {exc}"
            return result
        if result.readback == plan.expected:
            result.ok, result.status = True, "ok"
        else:
            result.detail = "read-back differs (ladder may have changed the device, or the write failed)"
        return result


def force_bit(session, label, on, allow):
    """FORCE ON/OFF of one M bit (``M250``) or one output (``Y1``, octal numbering) in RUN."""
    return run_plan(session, plan_force(label, on, allow), allow)


def write_units(session, label, values, allow):
    """Write D words (``D44``, ``[0xA5A5]``) or whole M bytes (``M248``, ``[0xA5]``, byte = 8 bits) in RUN."""
    return run_plan(session, plan_write(label, values, allow), allow)


@dataclass(frozen=True)
class YBlock:
    """Output image Y0..Y27 (octal) as read in one transaction; not atomic with the scan."""

    raw: bytes
    on: list

    def as_dict(self):
        return {"raw_hex": self.raw.hex(), "on": list(self.on)}


def read_y_block(session):
    """One plain read of the Y image (3 bytes = Y0..Y27 octal). A read: no allow-list, any CPU state.

    The image shows what the CPU drives, whether it comes from ladder logic or from a FORCE.
    """
    raw = bytes(session.read(Y_IMAGE, 3))
    if len(raw) != 3:
        raise ValueError("Incomplete Y image")
    return YBlock(raw, [f"Y{i:o}" for i in range(Y_MAX + 1) if raw[i // 8] >> (i % 8) & 1])
