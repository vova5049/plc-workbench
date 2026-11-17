"""Deterministic scan model for an explicit semantics subset; no physical transport.

Unsupported instructions fail at construction. Timers use virtual scan time and
can differ from the real CPU by a timer tick. Motion/modules/comms are not mocked.
"""

import math
import operator
import re
import struct

from . import experimental_codec as codec

BASES = {
    "LD",
    "LDI",
    "AND",
    "ANI",
    "OR",
    "ORI",
    "LDP",
    "LDF",
    "ANDP",
    "ANDF",
    "ORP",
    "ORF",
    "ORB",
    "ANB",
    "INV",
    "MPS",
    "MRD",
    "MPP",
    "OUT",
    "SET",
    "RST",
    "PLS",
    "PLF",
    "MOV",
    "DMOV",
    "ADD",
    "DADD",
    "SUB",
    "DSUB",
    "MUL",
    "DMUL",
    "DIV",
    "DDIV",
    "INC",
    "DINC",
    "DEC",
    "DDEC",
    "WAND",
    "DAND",
    "WOR",
    "DOR",
    "WXOR",
    "DXOR",
    "NEG",
    "DNEG",
    "CMP",
    "DCMP",
    "BCD",
    "DBCD",
    "BIN",
    "DBIN",
    "ROL",
    "DROL",
    "ROR",
    "DROR",
    "FMOV",
    "ZRST",
    "SFTL",
    "SFTR",
    "DEADD",
    "DESUB",
    "DEMUL",
    "DEDIV",
    "DSIN",
    "DCOS",
    "DTAN",
    "NOP",
    "END",
}
COMPARE = re.compile(r"^(LD|AND|OR)(D?)(>=|<=|<>|=|>|<)$")
DWORD = {
    "DMOV",
    "DADD",
    "DSUB",
    "DMUL",
    "DDIV",
    "DINC",
    "DDEC",
    "DAND",
    "DOR",
    "DXOR",
    "DNEG",
    "DCMP",
    "DBCD",
    "DBIN",
    "DROL",
    "DROR",
}


def signed(n, width):
    n &= (1 << width) - 1
    return n - (1 << width) if n & (1 << (width - 1)) else n


class ScanSimulator:
    def __init__(self, instructions):
        self.program = list(instructions)
        for ins in self.program:
            m = ins.mnemonic
            if (
                m not in BASES
                and not COMPARE.fullmatch(m)
                and not (m.endswith("P") and m[:-1] in BASES)
            ):
                raise NotImplementedError(f"Scan semantics not implemented: {m}")
            for operand in ins.operands:
                if operand.startswith(("U", '"')):
                    raise NotImplementedError(
                        "Module/string semantics are outside this scan model"
                    )
            if (
                m == "OUT"
                and len(ins.operands) == 2
                and ins.operands[0].startswith("C")
                and int(ins.operands[0][1:]) >= 200
            ):
                raise NotImplementedError(
                    "32-bit/directional counter scan model is not qualified"
                )
            for operand in ins.operands:
                special = re.fullmatch(r"M(8[0-9]{3})", operand)
                if special and int(special[1]) > 8003:
                    raise NotImplementedError(
                        "Special relay scan model is not implemented"
                    )
        codec.assemble(self.program)
        self.bits = {}
        self.words = {}
        self.timers = {}
        self.counters = {}
        self.contacts = {}
        self.previous = {}
        self.counter_edges = {}
        self.scans = 0
        self.time_ms = 0.0

    def _resolved(self, text):
        kind, n, index, ix = codec._device(text)
        if index:
            n += signed(self.read(index + str(ix)), 16)
        return kind, n

    def bit(self, text):
        kind, n = self._resolved(text)
        if kind == "M" and n >= 8000:
            if n == 8000:
                return True
            if n == 8001:
                return False
            if n == 8002:
                return self.scans == 0
            if n == 8003:
                return self.scans != 0
            raise NotImplementedError(f"Special relay model not implemented: M{n}")
        if kind in "TC":
            return bool(self.contacts.get((kind, n), False))
        if kind not in "XYMS":
            raise ValueError("Expected a bit device")
        codec._bit_address(kind, n)
        return bool(self.bits.get((kind, n), False))

    def set_bit(self, text, value):
        kind, n = self._resolved(text)
        if kind == "M" and n >= 8000:
            raise ValueError("Cannot assign special relay in scan model")
        if kind not in "XYMS":
            raise ValueError("Expected writable bit device")
        codec._bit_address(kind, n)
        self.bits[kind, n] = bool(value)

    def read(self, text, width=16):
        grouped = re.fullmatch(r"K([1-8])([XYMS]\d+(?:[VZ][0-7])?)", text)
        if grouped:
            count = int(grouped[1]) * 4
            kind, n = self._resolved(grouped[2])
            return sum(
                int(
                    self.bit(
                        kind + (format(n + i, "o") if kind in "XY" else str(n + i))
                    )
                )
                << i
                for i in range(count)
            )
        if re.fullmatch(r"K-?\d+", text):
            return int(text[1:]) & ((1 << width) - 1)
        if re.fullmatch(r"H[0-9A-F]+", text):
            return int(text[1:], 16) & ((1 << width) - 1)
        if text.startswith("E"):
            return int.from_bytes(struct.pack("<f", float(text[1:])), "little")
        kind, n = self._resolved(text)
        if kind == "T":
            return self.timers.get(n, 0)
        if kind == "C":
            return self.counters.get(n, 0)
        if kind in "XYMS":
            return int(self.bit(text))
        address = codec._word_address(kind, n)
        return sum(
            self.words.get(address + 2 * i, 0) << (16 * i) for i in range(width // 16)
        )

    def write(self, text, value, width=16):
        value &= (1 << width) - 1
        grouped = re.fullmatch(r"K([1-8])([XYMS]\d+(?:[VZ][0-7])?)", text)
        if grouped:
            kind, n = self._resolved(grouped[2])
            for i in range(int(grouped[1]) * 4):
                self.set_bit(
                    kind + (format(n + i, "o") if kind in "XY" else str(n + i)),
                    bool(value & (1 << i)),
                )
            return
        kind, n = self._resolved(text)
        if kind in "TC":
            raise NotImplementedError(
                "Direct timer/counter word assignment is not modeled"
            )
        address = codec._word_address(kind, n)
        for i in range(width // 16):
            self.words[address + 2 * i] = (value >> (16 * i)) & 65535

    def _edge(self, key, value, falling=False):
        old = self.previous.get(key, False)
        self.previous[key] = bool(value)
        return old and not value if falling else value and not old

    @staticmethod
    def _name(kind, n):
        return kind + (format(n, "o") if kind in "XY" else str(n))

    def _timer(self, n, enable, preset, dt):
        if not 0 <= n <= 511 or not 0 <= preset <= 32767:
            raise ValueError("Invalid timer")
        base = 100 if n < 200 or 250 <= n <= 255 else 10 if n <= 245 else 1
        retentive = 246 <= n <= 255
        elapsed = self._elapsed.get(n, 0.0)
        if enable:
            elapsed = min(elapsed + dt, preset * base)
        elif not retentive:
            elapsed = 0.0
        self._elapsed[n] = elapsed
        self.timers[n] = int(elapsed // base)
        self.contacts["T", n] = bool(self.timers[n] >= preset and (enable or retentive))

    def step(self, inputs=None, dt_ms=10):
        if not math.isfinite(dt_ms) or dt_ms <= 0:
            raise ValueError("dt_ms must be finite and positive")
        for name, value in (inputs or {}).items():
            if not name.startswith("X"):
                raise ValueError("Scan inputs must be X devices")
            if not isinstance(value, bool):
                raise TypeError("Input states must be booleans")
            self.set_bit(name, value)
        if not hasattr(self, "_elapsed"):
            self._elapsed = {}
        reset_counters = set()
        acc = False
        history = []
        branches = []
        for pc, ins in enumerate(self.program):
            m = ins.mnemonic
            a = ins.operands
            if m == "END":
                break
            if m == "NOP":
                continue
            comparison = COMPARE.fullmatch(m)
            if comparison:
                logic, double, relation = comparison.groups()
                width = 32 if double else 16
                lhs, rhs = (signed(self.read(x, width), width) for x in a)
                value = {
                    "=": lhs == rhs,
                    "<>": lhs != rhs,
                    ">": lhs > rhs,
                    "<": lhs < rhs,
                    ">=": lhs >= rhs,
                    "<=": lhs <= rhs,
                }[relation]
                if logic == "LD":
                    history = (history + [acc])[-8:]
                    acc = value
                elif logic == "AND":
                    acc = acc and value
                else:
                    acc = acc or value
                continue
            if m in {
                "LD",
                "LDI",
                "AND",
                "ANI",
                "OR",
                "ORI",
                "LDP",
                "LDF",
                "ANDP",
                "ANDF",
                "ORP",
                "ORF",
            }:
                value = self.bit(a[0])
                if m in {"LDI", "ANI", "ORI"}:
                    value = not value
                if m.endswith(("P", "F")):
                    value = self._edge(("contact", pc), value, m.endswith("F"))
                if m.startswith("LD"):
                    history = (history + [acc])[-8:]
                    acc = value
                elif m.startswith("AND") or m == "ANI":
                    acc = acc and value
                else:
                    acc = acc or value
                continue
            if m in {"ORB", "ANB"}:
                if not history:
                    raise ValueError("Empty expression stack")
                prior = history.pop()
                acc = (prior or acc) if m == "ORB" else (prior and acc)
                continue
            if m == "INV":
                acc = not acc
                continue
            if m == "MPS":
                branches.append(acc)
                continue
            if m in {"MRD", "MPP"}:
                if not branches:
                    raise ValueError("Empty branch stack")
                acc = branches[-1]
                if m == "MPP":
                    branches.pop()
                continue
            if m == "OUT":
                if len(a) == 1:
                    self.set_bit(a[0], acc)
                else:
                    kind, n = self._resolved(a[0])
                    preset = self.read(a[1], 32 if kind == "C" and n >= 200 else 16)
                    if kind == "T":
                        self._timer(n, acc, preset, dt_ms)
                    elif kind == "C":
                        if n >= 200:
                            raise NotImplementedError(
                                "32-bit/directional counters need a separate qualified model"
                            )
                        old = self.counter_edges.get(n, False)
                        self.counter_edges[n] = acc
                        count = self.counters.get(n, 0)
                        if n not in reset_counters and acc and not old:
                            count = min(count + 1, preset)
                        self.counters[n] = count
                        self.contacts["C", n] = count >= preset
                    else:
                        raise ValueError("Invalid timed coil")
                continue
            if m in {"PLS", "PLF"}:
                self.set_bit(a[0], self._edge(("coil", pc), acc, m == "PLF"))
                continue
            if m in {"SET", "RST"}:
                if acc:
                    kind, n = self._resolved(a[0])
                    if kind == "T":
                        self.timers[n] = 0
                        self._elapsed[n] = 0.0
                        self.contacts["T", n] = False
                    elif kind == "C":
                        self.counters[n] = 0
                        self.contacts["C", n] = False
                        reset_counters.add(n)
                    elif kind in "XYMS":
                        self.set_bit(a[0], m == "SET")
                    elif m == "RST":
                        self.write(a[0], 0)
                continue
            pulse = m not in BASES and m.endswith("P")
            if pulse:
                m = m[:-1]
            enabled = self._edge(("instruction", pc), acc) if pulse else acc
            if not enabled:
                continue
            width = 32 if m in DWORD else 16
            op = m[1:] if m in DWORD else m
            mask = (1 << width) - 1
            if op == "MOV":
                self.write(a[1], self.read(a[0], width), width)
            elif op in {
                "ADD",
                "SUB",
                "MUL",
                "DIV",
                "WAND",
                "AND",
                "WOR",
                "OR",
                "WXOR",
                "XOR",
            }:
                x, y = (signed(self.read(v, width), width) for v in a[:2])
                if op == "DIV":
                    if y == 0:
                        raise ValueError("Division by zero")
                    q = (abs(x) // abs(y)) * (-1 if (x < 0) != (y < 0) else 1)
                    r = x - q * y
                    self.write(a[2], (q & mask) | ((r & mask) << width), width * 2)
                elif op == "MUL":
                    self.write(a[2], x * y, width * 2)
                else:
                    value = {
                        "ADD": operator.add,
                        "SUB": operator.sub,
                        "WAND": operator.and_,
                        "AND": operator.and_,
                        "WOR": operator.or_,
                        "OR": operator.or_,
                        "WXOR": operator.xor,
                        "XOR": operator.xor,
                    }[op](x, y)
                    self.write(a[2], value, width)
            elif op in {"INC", "DEC", "NEG"}:
                value = self.read(a[0], width)
                self.write(
                    a[0],
                    value + 1 if op == "INC" else value - 1 if op == "DEC" else -value,
                    width,
                )
            elif op == "CMP":
                x, y = (signed(self.read(v, width), width) for v in a[:2])
                kind, n = self._resolved(a[2])
                for offset, value in enumerate([x > y, x == y, x < y]):
                    self.set_bit(self._name(kind, n + offset), value)
            elif op in {"BCD", "BIN"}:
                value = self.read(a[0], width)
                digits = width // 4
                if op == "BCD":
                    if value >= 10**digits:
                        raise ValueError("BCD overflow")
                    result = int(str(value), 16)
                else:
                    text = f"{value:0{digits}X}"
                    if any(c not in "0123456789" for c in text):
                        raise ValueError("Invalid BCD digit")
                    result = int(text)
                self.write(a[1], result, width)
            elif op in {"ROL", "ROR"}:
                value = self.read(a[0], width)
                count = self.read(a[1])
                if not 1 <= count <= width:
                    raise ValueError("Invalid rotate count")
                result = (
                    ((value << count) | (value >> (width - count)))
                    if op == "ROL"
                    else ((value >> count) | (value << (width - count)))
                )
                self.write(a[0], result, width)
            elif op == "FMOV":
                count = self.read(a[2])
                kind, n = self._resolved(a[1])
                if not 0 <= count <= 32000:
                    raise ValueError("Invalid block length")
                values = [self.read(a[0])] * count
                for i, value in enumerate(values):
                    self.write(self._name(kind, n + i), value)
            elif op == "ZRST":
                kind, n = self._resolved(a[0])
                end_kind, end = self._resolved(a[1])
                if kind != end_kind or end < n:
                    raise ValueError("Invalid reset range")
                for i in range(n, end + 1):
                    if kind in "XYMS":
                        self.set_bit(self._name(kind, i), False)
                    elif kind in "DVRZ":
                        self.write(self._name(kind, i), 0)
                    else:
                        raise NotImplementedError(
                            "Timer/counter range reset is not modeled"
                        )
            elif op in {"SFTL", "SFTR"}:
                source, si = self._resolved(a[0])
                dest, di = self._resolved(a[1])
                length = self.read(a[2])
                shift = self.read(a[3])
                if not 1 <= shift <= length:
                    raise ValueError("Invalid bit shift")
                old = [self.bit(self._name(dest, di + i)) for i in range(length)]
                new = [self.bit(self._name(source, si + i)) for i in range(shift)]
                values = (
                    (new + old[: length - shift])
                    if op == "SFTL"
                    else (old[shift:] + new)
                )
                for i, value in enumerate(values):
                    self.set_bit(self._name(dest, di + i), value)
            elif m in {"DEADD", "DESUB", "DEMUL", "DEDIV", "DSIN", "DCOS", "DTAN"}:
                floating = lambda operand: struct.unpack(
                    "<f", self.read(operand, 32).to_bytes(4, "little")
                )[0]
                x = floating(a[0])
                if m in {"DSIN", "DCOS", "DTAN"}:
                    value = {"DSIN": math.sin, "DCOS": math.cos, "DTAN": math.tan}[m](x)
                    dest = a[1]
                else:
                    y = floating(a[1])
                    dest = a[2]
                    if m == "DEDIV" and y == 0:
                        raise ValueError("Floating division by zero")
                    value = {
                        "DEADD": operator.add,
                        "DESUB": operator.sub,
                        "DEMUL": operator.mul,
                        "DEDIV": operator.truediv,
                    }[m](x, y)
                self.write(dest, int.from_bytes(struct.pack("<f", value), "little"), 32)
            else:
                raise NotImplementedError(f"Semantics not implemented: {m}")
        self.scans += 1
        self.time_ms += dt_ms
        return self
