"""Independent experimental FX3U codec calibrated from offline oracle vectors.

No MELSOFT library, process, or serial transport is used. This profile is not
hardware qualification and is deliberately separate from the proven deploy path.
"""

import json
import math
import re
import struct
from importlib.resources import files

from .bytecode import Instruction

PROFILE = json.loads(files("fx3u").joinpath("experimental_profile.json").read_text())
LOGIC = {"LD": 0, "LDI": 1, "AND": 2, "ANI": 3, "OR": 4, "ORI": 5}
EDGES = {
    "LDP": 0x1CA,
    "LDF": 0x1CB,
    "ANDP": 0x1CC,
    "ANDF": 0x1CD,
    "ORP": 0x1CE,
    "ORF": 0x1CF,
}


def _device(text):
    m = re.fullmatch(r"([XYMSTDCRVZ])(\d+)(?:([VZ])([0-7]))?", text)
    if not m:
        raise ValueError(f"Unsupported device: {text}")
    kind, digits, index, index_n = m.groups()
    try:
        value = int(digits, 8 if kind in "XY" else 10)
    except ValueError:
        raise ValueError(f"Invalid octal device: {text}") from None
    return kind, value, index, int(index_n or 0)


def _bit_address(kind, n):
    if kind in "XY" and 0 <= n <= 255:
        return (0x400 if kind == "X" else 0x500) + n
    if kind == "T" and 0 <= n <= 511:
        return 0x600 + n
    if kind == "C" and 0 <= n <= 255:
        return 0xE00 + n
    if kind == "M":
        if 0 <= n <= 1535:
            return 0x800 + n
        if 1536 <= n <= 7679:
            return 0x2200 + n
        if 8000 <= n <= 8511:
            return 0xF00 + n - 8000
    if kind == "S" and 0 <= n <= 4095:
        return n if n < 1024 else 0x1000 + n
    raise ValueError(f"Bit device outside calibrated address ranges: {kind}{n}")


def _word_address(kind, n):
    if kind == "D":
        if 0 <= n <= 999:
            return 0x60000 + 2 * n
        if 1000 <= n <= 7999:
            return 0x80000 + 2 * (n - 1000)
        if 8000 <= n <= 8511:
            return 2 * (n - 8000)
    if kind == "T" and 0 <= n <= 511:
        return 0x20000 + 2 * n
    if kind == "C":
        if 0 <= n <= 199:
            return 0x40000 + 2 * n
        if 200 <= n <= 255:
            return 0x40200 + 4 * (n - 200)
    if kind in "VZ" and 0 <= n <= 7:
        return (0x38 if n == 0 else 0x16C + 4 * (n - 1)) + (2 if kind == "V" else 0)
    if kind == "R" and 0 <= n <= 32767:
        return 0x180000 + 2 * n
    raise ValueError(f"Word device outside calibrated address ranges: {kind}{n}")


def _form(text):
    if re.fullmatch(r"U[0-7]\\G[0-9]+", text):
        return "module"
    if re.fullmatch(r'"[^"]+"', text):
        return "string"
    grouped = re.fullmatch(r"K([1-8])([XYMS]\d+(?:[VZ][0-7])?)", text)
    if grouped:
        kind, n, index, _ = _device(grouped[2])
        return (
            "group:"
            + kind
            + (":wide" if int(grouped[1]) > 4 else "")
            + (":index:" + index if index else "")
        )
    if re.fullmatch(r"[XYMSTDCRVZ]\d+(?:[VZ][0-7])?", text):
        kind, n, index, _ = _device(text)
        family = "bit" if kind in "XYMS" else "word"
        if kind == "M" and n >= 8000:
            kind = "MS"
        if kind == "D" and n >= 8000:
            kind = "DS"
        if kind == "C" and n >= 200:
            kind = "C32"
        return family + ":" + kind + (":index:" + index if index else "")
    if re.fullmatch(r"K-?\d+", text):
        return "constant:K"
    if re.fullmatch(r"H[0-9A-F]+", text):
        return "constant:H"
    if re.fullmatch(r"E[-+0-9.E]+", text):
        return "constant:E"
    if re.fullmatch(r"[PIN]\d+", text):
        return "number:" + text[0]
    raise ValueError(f"Unsupported operand syntax: {text}")


def _address_words(tag, address, index=None, index_n=0, group=0):
    low = tag | (address & 255)
    high = 0x8000 | (address >> 8)
    if group:
        high |= group << 9
    if index:
        # The index selector occupies bits 6..8 of the high word.
        if high & 0x1C0:
            raise ValueError("Indexed address exceeds the calibrated page range")
        low |= 0x2000 if index == "Z" else 0x1000
        high |= index_n << 6
    return [low, high]


def operand_words(text, width, bit=False):
    if width not in (2, 4):
        raise ValueError("Unsupported operand width")
    group = re.fullmatch(r"K([1-8])([XYMS]\d+(?:[VZ][0-7])?)", text)
    module = re.fullmatch(r"U([0-7])\\G([0-9]+)", text)
    if module:
        unit, n = map(int, module.groups())
        if n > 32767:
            raise ValueError("Buffer memory address out of range")
        return [0x8C00 | (n & 255), 0x8000 | (unit << 10) | (n >> 8)] + [0x8000] * (
            width - 2
        )
    if text.startswith(chr(34)) and text.endswith(chr(34)):
        data = text[1:-1].encode("ascii")
        if not 1 <= len(data) <= 63:
            raise ValueError("String length outside profile")
        pairs = (len(data) + 1) // 2
        data = data.ljust(pairs * 2, b"\0")
        result = []
        for i in range(pairs):
            result.extend([0x8A00 | data[2 * i], 0x8000 | data[2 * i + 1]])
        result[1] |= (pairs - 1) << 9
        return result
    if group:
        count = int(group[1])
        kind, n, index, ix = _device(group[2])
        if count > 8:
            raise ValueError("Bit group exceeds profile")
        address = _bit_address(kind, n)
        _bit_address(kind, n + count * 4 - 1)
        result = _address_words(0x8400, address, index, ix, count)
    elif re.fullmatch(r"K-?\d+|H[0-9A-F]+", text):
        value = int(text[1:], 16 if text[0] == "H" else 10)
        bits = width * 8
        if text[0] == "K":
            if not -(1 << (bits - 1)) <= value < 1 << (bits - 1):
                raise ValueError("Decimal constant outside signed instruction width")
        elif not 0 <= value < 1 << bits:
            raise ValueError("Hexadecimal constant outside instruction width")
        value &= (1 << bits) - 1
        result = [0x8000 | ((value >> (8 * i)) & 255) for i in range(width)]
        if text[0] == "H":
            result[0] |= 0x200
        return result
    elif text.startswith("E"):
        if width != 4:
            raise ValueError("Float constant requires a 32-bit instruction")
        value = float(text[1:])
        if not math.isfinite(value):
            raise ValueError("Nonfinite constant")
        try:
            data = struct.pack("<f", value)
        except OverflowError:
            raise ValueError("Float constant out of range") from None
        result = [0x8000 | x for x in data]
        result[1] |= 0x1000
        return result
    elif re.fullmatch(r"P\d+", text):
        n = int(text[1:])
        if n > 4095:
            raise ValueError("Pointer outside profile")
        n *= 2
        result = [0x8800 | (n & 255), 0x8000 | (n >> 8)]
    elif re.fullmatch(r"I[0-9]+", text):
        n = int(text[1:]) * 2
        if n > 8190:
            raise ValueError("Interrupt pointer out of range")
        result = [0x8800 | (n & 255), 0x8200 | (n >> 8)]
    else:
        kind, n, index, ix = _device(text)
        if bit or kind in "XYMS":
            result = _address_words(0x8400, _bit_address(kind, n), index, ix)
        else:
            result = _address_words(0x8600, _word_address(kind, n), index, ix)
    return result + [0x8000] * (width - len(result))


def _bit_words(m, text):
    if _form(text) not in PROFILE["bit_forms"].get(m, []):
        raise ValueError(f"Uncalibrated {m} operand form: {text}")
    kind, n, index, ix = _device(text)
    address = _bit_address(kind, n)
    short = not index and address < 0x3000
    payload = (
        [0x8000 | address] if short else _address_words(0x8400, address, index, ix)
    )
    if m in LOGIC:
        if not index and address < 0x1000:
            return [0x2000 + LOGIC[m] * 0x1000 + address]
        return [(0x1C2 + LOGIC[m]) | (0 if short else 0x200), *payload]
    if m in EDGES:
        return [EDGES[m] | (0 if short else 0x200), *payload]
    if m in ("OUT", "SET", "RST"):
        if m == "RST" and kind in "TC":
            return [0xC | (0 if short else 0x200), *payload]
        if kind != "S" and not index and address < 0xF00:
            return [{"OUT": 0xC000, "SET": 0xD000, "RST": 0xE000}[m] + address]
        code = (
            {"OUT": 5, "SET": 6, "RST": 7}[m]
            if kind == "S"
            else {"OUT": 2, "SET": 3, "RST": 4}[m]
        )
        return [code | (0 if short else 0x200), *payload]
    if m in ("PLS", "PLF"):
        return [({"PLS": 8, "PLF": 9}[m]) | (0 if short else 0x200), *payload]
    if m == "STL":
        if not index and n < 1022:
            return [0xF000 + n]
        return [0xE | (0 if short else 0x200), *payload]
    raise ValueError(f"Unsupported bit instruction: {m}")


def words(ins):
    m, args = ins.mnemonic, ins.operands
    if m == "NOP" and not args:
        return [0xFFFF]
    label = re.fullmatch(r"([PI])([0-9]+)", m)
    if label and not args:
        n = int(label[2])
        if label[1] == "P" and n <= 4095:
            return [0xB000 + n] if n < 256 else [0xB100 | (n & 255), 0xB200 | (n >> 8)]
        if label[1] == "I" and n <= 999:
            return [0xB800 + n]
        raise ValueError("Label out of profile range")
    if m == "ASC" and len(args) == 2:
        literal = args[0]
        if (
            not re.fullmatch(r"[A-Z0-9?.+\\-]+", literal)
            or len(literal) > 8
            or _form(args[1]) not in ["word:D", "word:DS"]
        ):
            raise ValueError("Uncalibrated ASC form")
        data = literal.encode("ascii").ljust(8, b" ")
        out = [0xA8]
        for i in range(4):
            out.extend([0x8A00 | data[2 * i], 0x8000 | data[2 * i + 1]])
        return out + operand_words(args[1], 2)
    if (
        m in PROFILE["bit_forms"]
        and len(args) == 1
        and m != "RST"
        or (m == "RST" and len(args) == 1 and args[0][0] in "XYMSTC")
    ):
        return _bit_words(m, args[0])
    if m == "OUT" and len(args) == 2:
        kind, n, index, _ix = _device(args[0])
        if kind not in "TC" or index:
            raise ValueError("Uncalibrated timer/counter destination")
        _bit_address(kind, n)
        width = 4 if kind == "C" and n >= 200 else 2
        if (
            args[1].startswith("K-")
            or _form(args[1])
            not in [
                "constant:K",
                "word:D",
                "word:DS",
                "word:R",
                "word:D:index:Z",
                "word:D:index:V",
            ]
            or (width == 4 and re.search(r"[VZ][0-7]$", args[1]))
        ):
            raise ValueError("Uncalibrated preset")
        return [(0x600 if kind == "T" else 0xE00) + n, *operand_words(args[1], width)]
    if m in ("MC", "MCR"):
        if len(args) != (2 if m == "MC" else 1) or not re.fullmatch(r"N[0-7]", args[0]):
            raise ValueError("Invalid master-control form")
        out = [0xA if m == "MC" else 0xB, 0x8000 + int(args[0][1:])]
        if m == "MC":
            kind, n, index, _ = _device(args[1])
            address = _bit_address(kind, n)
            if (
                kind not in "MY"
                or index
                or address >= 0x3000
                or (kind == "M" and n >= 8000)
            ):
                raise ValueError("Uncalibrated master-control device")
            out.append(0x8000 | address)
        return out
    spec = PROFILE["instructions"].get(m)
    if spec is None or len(args) != spec["operands"]:
        raise ValueError(f"Unsupported experimental instruction: {ins}")
    out = [spec["opcode"]]
    for i, operand in enumerate(args):
        if _form(operand) not in spec["forms"][i]:
            raise ValueError(f"Uncalibrated operand {i + 1} of {m}: {operand}")
        out.extend(operand_words(operand, spec["width"]))
    return out + spec.get("trailer", [])


def encode_instruction(ins):
    w = words(ins)
    return struct.pack("<" + "H" * len(w), *w)


def assemble(instructions, limit=32000):
    instructions = list(instructions)
    if not instructions or instructions[-1] != Instruction("END"):
        raise ValueError("Program must terminate with END")
    if any(i.mnemonic == "END" for i in instructions[:-1]):
        raise ValueError("Instructions after END")
    data = bytearray()
    mapping = []
    for line, ins in enumerate(instructions, 1):
        code = encode_instruction(ins)
        mapping.append(
            {
                "line": line,
                "step": len(data) // 2,
                "words": len(code) // 2,
                "instruction": str(ins),
            }
        )
        data.extend(code)
    if len(data) > limit:
        raise ValueError("Program exceeds experimental profile capacity")
    return bytes(data), mapping


def parse_il(text):
    result = []
    for line in text.splitlines():
        quoted = False
        stop = len(line)
        for i, ch in enumerate(line):
            if ch == '"':
                quoted = not quoted
            elif ch == ";" and not quoted:
                stop = i
                break
        if quoted:
            raise ValueError("Unterminated string")
        parts = re.findall(r'"[^"\n]*"|[^\s]+', line[:stop].strip().upper())
        if parts:
            result.append(Instruction(parts[0], tuple(parts[1:])))
    return result


def _bit_name(address):
    if 0 <= address < 0x400:
        return "S" + str(address)
    if 0x400 <= address < 0x500:
        return "X" + format(address - 0x400, "o")
    if 0x500 <= address < 0x600:
        return "Y" + format(address - 0x500, "o")
    if 0x600 <= address < 0x800:
        return "T" + str(address - 0x600)
    if 0x800 <= address < 0xE00:
        return "M" + str(address - 0x800)
    if 0xE00 <= address < 0xF00:
        return "C" + str(address - 0xE00)
    if 0xF00 <= address <= 0x10FF:
        return "M" + str(8000 + address - 0xF00)
    if 0x1400 <= address <= 0x1FFF:
        return "S" + str(address - 0x1000)
    if 0x2800 <= address <= 0x3FFF:
        return "M" + str(address - 0x2200)
    raise ValueError("Unknown bit address")


def _word_names(address):
    result = []
    for kind in ["Z", "V"]:
        for n in range(8):
            if _word_address(kind, n) == address:
                result.append(kind + str(n))
    for kind, lo, hi, start, stride in [
        ("D", 0x60000, 0x607CE, 0, 2),
        ("D", 0x80000, 0x836AE, 1000, 2),
        ("D", 0, 0x3FE, 8000, 2),
        ("T", 0x20000, 0x203FE, 0, 2),
        ("C", 0x40000, 0x4018E, 0, 2),
        ("C", 0x40200, 0x402DC, 200, 4),
        ("R", 0x180000, 0x18FFFE, 0, 2),
    ]:
        if lo <= address <= hi and (address - lo) % stride == 0:
            result.append(kind + str(start + (address - lo) // stride))
    if not result:
        raise ValueError("Unknown word address")
    return result


def disassemble(data):
    if len(data) % 2 or not data or len(data) > 32000:
        raise ValueError("Invalid image length")
    stream = list(struct.unpack("<" + "H" * (len(data) // 2), data))
    pos = 0
    result = []
    reverse = {s["opcode"]: (m, s) for m, s in PROFILE["instructions"].items()}

    def take(n):
        nonlocal pos
        if pos + n > len(stream):
            raise ValueError("Truncated instruction")
        w = stream[pos : pos + n]
        pos += n
        return w

    def decode_operand(width, allowed=None, bit_payload=False):
        if bit_payload:
            w = take(2)
            width = 2
        else:
            if pos >= len(stream):
                raise ValueError("Truncated operand")
            if stream[pos] & 0xE00 == 0xA00:
                if pos + 1 >= len(stream):
                    raise ValueError("Truncated string")
                pairs = 1 + ((stream[pos + 1] >> 9) & 31)
                w = take(pairs * 2)
                raw = bytes(x & 255 for x in w).rstrip(b"\0")
                value = '"' + raw.decode("ascii") + '"'
                if allowed is not None and "string" not in allowed:
                    raise ValueError("Unexpected string")
                return value
            w = take(width)
        first, second = w[:2]
        tag = first & 0xE00
        index = "Z" if first & 0x2000 else "V" if first & 0x1000 else None
        ix = (second >> 6) & 7 if index else 0
        high = second & 0x7FFF
        if index:
            high &= ~0x1C0
        suffix = (index + str(ix)) if index else ""
        candidates = []
        if tag in [0, 0x200]:
            raw = bytes(x & 255 for x in w)
            if width == 4 and second & 0xFF00 == 0x9000:
                value = struct.unpack("<f", raw)[0]
                if not math.isfinite(value):
                    raise ValueError("Nonfinite encoded float")
                candidates = ["E" + repr(value)]
            elif tag == 0x200:
                candidates = ["H" + format(int.from_bytes(raw, "little"), "X")]
            else:
                candidates = ["K" + str(int.from_bytes(raw, "little", signed=True))]
        elif tag == 0x400:
            group = (high >> 9) & 15
            address = ((high & 0x3F) << 8) | (first & 255)
            name = _bit_name(address) + suffix
            candidates = [("K" + str(group) if group else "") + name]
        elif tag == 0x600:
            address = (high << 8) | (first & 255)
            candidates = [x + suffix for x in _word_names(address)]
        elif tag == 0x800:
            address = ((high & 0x1FF) << 8) | (first & 255)
            if address % 2:
                raise ValueError("Unaligned pointer")
            candidates = [("I" if high & 0x200 else "P") + str(address // 2)]
        elif tag == 0xC00:
            candidates = [
                f"U{(second >> 10) & 7}\\G{((second & 127) << 8) | (first & 255)}"
            ]
        else:
            raise ValueError("Unknown operand representation")
        for value in candidates:
            if allowed is None or _form(value) in allowed:
                return value
        raise ValueError("Operand is outside calibrated instruction form")

    logic_rev = {0x1C2 + v: k for k, v in LOGIC.items()}
    logic_rev.update({v: k for k, v in EDGES.items()})
    bit_rev = {
        2: "OUT",
        3: "SET",
        4: "RST",
        5: "OUT",
        6: "SET",
        7: "RST",
        8: "PLS",
        9: "PLF",
        12: "RST",
        14: "STL",
    }
    while pos < len(stream):
        start = pos
        code = take(1)[0]
        if code == 15:
            ins = Instruction("END")
            result.append(ins)
            if any(w != 0xFFFF for w in stream[pos:]):
                raise ValueError("Unknown data after END")
            return result
        if code == 0xFFFF:
            ins = Instruction("NOP")
        elif code in reverse:
            m, spec = reverse[code]
            args = []
            for forms in spec["forms"]:
                args.append(decode_operand(spec["width"], forms))
            trailer = spec.get("trailer", [])
            if take(len(trailer)) != trailer:
                raise ValueError("Unknown instruction trailer")
            ins = Instruction(m, tuple(args))
        elif 0xB000 <= code <= 0xB0FF:
            ins = Instruction("P" + str(code & 255))
        elif 0xB100 <= code <= 0xB1FF:
            hi = take(1)[0]
            if hi & 0xFF00 != 0xB200:
                raise ValueError("Invalid pointer label")
            ins = Instruction("P" + str(((hi & 255) << 8) | (code & 255)))
        elif 0xB800 <= code <= 0xBBE7:
            ins = Instruction("I" + str(code - 0xB800))
        elif 0x2000 <= code <= 0x7FFF:
            m = list(LOGIC)[(code >> 12) - 2]
            ins = Instruction(m, (_bit_name(code & 0xFFF),))
        elif 0xC000 <= code <= 0xEFFF:
            m = {12: "OUT", 13: "SET", 14: "RST"}[code >> 12]
            ins = Instruction(m, (_bit_name(code & 0xFFF),))
        elif 0xF000 <= code <= 0xF3FD:
            ins = Instruction("STL", ("S" + str(code - 0xF000),))
        elif code & 0xFE00 in [0x600, 0xE00] and code < 0x1000:
            kind = "T" if code < 0xE00 else "C"
            n = code - (0x600 if kind == "T" else 0xE00)
            if kind == "C" and n > 255:
                raise ValueError("Counter outside profile")
            ins = Instruction(
                "OUT",
                (kind + str(n), decode_operand(4 if kind == "C" and n >= 200 else 2)),
            )
        elif code in [0xA, 0xB]:
            n = take(1)[0] - 0x8000
            if not 0 <= n <= 7:
                raise ValueError("Invalid MC nesting")
            args = ["N" + str(n)]
            if code == 0xA:
                args.append(_bit_name(take(1)[0] & 0x7FFF))
            ins = Instruction("MC" if code == 0xA else "MCR", tuple(args))
        elif code & ~0x200 in logic_rev or code & ~0x200 in bit_rev:
            base = code & ~0x200
            m = logic_rev.get(base) or bit_rev[base]
            if code & 0x200:
                value = decode_operand(2, bit_payload=True)
            else:
                value = _bit_name(take(1)[0] & 0x7FFF)
            ins = Instruction(m, (value,))
        elif code == 0xA8:
            literal_words = take(8)
            literal = bytes(x & 255 for x in literal_words).decode("ascii").rstrip(" ")
            ins = Instruction(
                "ASC", (literal, decode_operand(2, ["word:D", "word:DS"]))
            )
        else:
            raise ValueError(f"Unknown opcode {code:04x} at step {start}")
        if words(ins) != stream[start:pos]:
            raise ValueError(f"Noncanonical instruction at step {start}")
        result.append(ins)
    raise ValueError("Missing END")
