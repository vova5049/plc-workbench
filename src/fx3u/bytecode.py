"""Fail-closed encoder/decoder for the instruction forms observed on our FX3U.

Not a complete Mitsubishi assembler. Byte encodings are regression checked
against the captured 1585-instruction lighting program, not a published ISA.
"""
import re
import struct
from dataclasses import dataclass

REGION_SIZE = 6096


@dataclass(frozen=True)
class Instruction:
    mnemonic: str
    operands: tuple = ()

    def __str__(self):
        return ' '.join((self.mnemonic, *self.operands))


# Single-word forms actually present in the reference program.
SIMPLE = {
    ('LD', 'X'): 0x2400, ('LD', 'Y'): 0x2500, ('LD', 'T'): 0x2600,
    ('LD', 'M'): 0x2800, ('LDI', 'M'): 0x3800,
    ('ANI', 'Y'): 0x5500, ('ANI', 'T'): 0x5600, ('ANI', 'M'): 0x5800,
    ('OR', 'M'): 0x6800, ('OUT', 'Y'): 0xC500, ('OUT', 'M'): 0xC800,
    ('SET', 'M'): 0xD800, ('RST', 'M'): 0xE800,
    # New forms: external FX coding reference, then instruction-lab CPU trial.
    ('LDI', 'T'): 0x3600, ('LD', 'C'): 0x2E00,
}
LIMITS = {'X': 23, 'Y': 23, 'M': 255, 'T': 48, 'C': 0, 'D': 127, 'K': 255}


def operand(text, kind=None):
    match = re.fullmatch(r'([XYMTCDK])([0-9]+)', text)
    if not match:
        raise ValueError(f'Unsupported operand: {text}')
    typ, digits = match.groups()
    try:
        value = int(digits, 8 if typ in 'XY' else 10)
    except ValueError:
        raise ValueError(f'Invalid octal device: {text}') from None
    if (kind is not None and typ != kind) or value > LIMITS[typ]:
        raise ValueError(f'Operand outside proven profile: {text} (expected {kind})')
    return typ, value


def device(typ, value):
    text = typ + (f'{value:03o}' if typ in 'XY' else str(value))
    operand(text)
    return text


def extended(text, kind):
    _, value = operand(text, kind)
    if kind == 'D':
        return [0x8600 + value * 2, 0x8600]
    if kind == 'M':
        addr = 0x800 + value
        return [0x8400 + (addr & 255), 0x8000 + (addr >> 8)]
    if kind == 'K':
        return [0x8000 + value, 0x8000]
    raise ValueError(f'Unsupported extended operand kind: {kind}')


def words(ins):
    m, args = ins.mnemonic, ins.operands
    if not args and m in ('END', 'ORB'):
        return [0x000F if m == 'END' else 0xFFF9]
    if len(args) == 1:
        typ, value = operand(args[0])
        if (m, typ) in SIMPLE:
            return [SIMPLE[m, typ] + value]
        if m == 'PLS' and typ == 'M':
            return [0x0008, 0x8800 + value]
        if m == 'RST' and typ in ('T', 'C'):
            return [0x000C, (0x8600 if typ == 'T' else 0x8E00) + value]
        if m == 'INC' and typ == 'D':
            return [0x0040, *extended(args[0], 'D')]
    if len(args) == 2:
        if m == 'OUT' and args[0][0] in ('T', 'C'):
            typ, value = operand(args[0])
            return [(0x0600 if typ == 'T' else 0x0E00) + value, *extended(args[1], 'K')]
        if m == 'ROL':
            if not 1 <= operand(args[1], 'K')[1] <= 16:
                raise ValueError('ROL count must be 1..16')
            return [0x004E, *extended(args[0], 'D'), *extended(args[1], 'K')]
        if m == 'MOV':
            return [0x0028, *extended(args[0], 'K'), *extended(args[1], 'D')]
        if m in ('AND=', 'AND>='):
            return [0x01E0 if m == 'AND=' else 0x01EC,
                    *extended(args[0], 'D'), *extended(args[1], 'K')]
    if m == 'SFTL' and len(args) == 4:
        source, dest = operand(args[0], 'M')[1], operand(args[1], 'M')[1]
        length, shift = operand(args[2], 'K')[1], operand(args[3], 'K')[1]
        if not 1 <= shift <= length or dest+length > 256 or source+shift > 256:
            raise ValueError('SFTL spans outside qualified devices or has invalid counts')
        return [0x0056, *extended(args[0], 'M'), *extended(args[1], 'M'),
                *extended(args[2], 'K'), *extended(args[3], 'K')]
    raise ValueError(f'Unsupported instruction: {ins}')


def parse_il(text):
    result = []
    for line in text.splitlines():
        parts = line.split(';', 1)[0].strip().upper().split()
        if parts:
            result.append(Instruction(parts[0], tuple(parts[1:])))
    return result


def assemble(instructions, padded=True):
    instructions = list(instructions)
    if not instructions or instructions[-1] != Instruction('END'):
        raise ValueError('Program must terminate with END')
    if any(i.mnemonic == 'END' for i in instructions[:-1]):
        raise ValueError('Instructions after END')
    output, source_map = bytearray(), []
    for line, ins in enumerate(instructions, 1):
        code = words(ins)
        source_map.append({'line': line, 'step': len(output)//2,
                           'words': len(code), 'instruction': str(ins)})
        output.extend(struct.pack('<' + 'H'*len(code), *code))
    if len(output) > REGION_SIZE:
        raise ValueError('Program exceeds proven memory region')
    if padded:
        output.extend(b'\xff'*(REGION_SIZE-len(output)))
    return bytes(output), source_map


def disassemble(data):
    if len(data) % 2 or len(data) > REGION_SIZE:
        raise ValueError('Invalid program size')
    stream = struct.unpack('<'+'H'*(len(data)//2), data)
    result, pos = [], 0
    def take(count):
        nonlocal pos
        if pos+count > len(stream):
            raise ValueError('Truncated operand')
        chunk = stream[pos:pos+count]
        pos += count
        return chunk
    def ext(kind):
        low, high = take(2)
        if kind == 'M':
            if not 0x8400 <= low <= 0x84FF or high != 0x8008:
                raise ValueError('Unsupported bit operand encoding')
            return device('M', low-0x8400)
        base = 0x8600 if kind == 'D' else 0x8000
        if high != base or not base <= low <= base+255:
            raise ValueError('Unsupported extended operand encoding')
        value = low-base
        if kind == 'D':
            if value % 2:
                raise ValueError('Unaligned D operand')
            value //= 2
        return device(kind, value)
    while pos < len(stream):
        start = pos
        code, = take(1)
        if code == 0x000F:
            result.append(Instruction('END'))
            if any(w != 0xFFFF for w in stream[pos:]):
                raise ValueError('Unknown data after END')
            return result
        if code == 0xFFF9:
            ins = Instruction('ORB')
        elif code in (0x0008, 0x000C):
            value, = take(1)
            typ, base = ('M', 0x8800) if code == 8 else (
                ('C', 0x8E00) if value & 0xFF00 == 0x8E00 else ('T', 0x8600))
            ins = Instruction('PLS' if code == 8 else 'RST', (device(typ, value-base),))
        elif code & 0xFF00 in (0x0600, 0x0E00):
            ins = Instruction('OUT', (device('T' if code & 0xFF00 == 0x0600 else 'C', code & 255), ext('K')))
        elif code == 0x004E:
            ins = Instruction('ROL', (ext('D'), ext('K')))
        elif code == 0x0056:
            ins = Instruction('SFTL', (ext('M'), ext('M'), ext('K'), ext('K')))
        elif code == 0x0040:
            ins = Instruction('INC', (ext('D'),))
        elif code in (0x0028, 0x01E0, 0x01EC):
            if code == 0x0028:
                ins = Instruction('MOV', (ext('K'), ext('D')))
            else:
                ins = Instruction('AND=' if code == 0x01E0 else 'AND>=', (ext('D'), ext('K')))
        else:
            forms = [(m, t) for (m, t), base in SIMPLE.items() if code & 0xFF00 == base]
            if len(forms) != 1:
                raise ValueError(f'Unknown opcode {code:04X} at step {start}')
            m, typ = forms[0]
            ins = Instruction(m, (device(typ, code & 255),))
        # Reject alternate/noncanonical representations as well as bad addresses.
        if tuple(words(ins)) != stream[start:pos]:
            raise ValueError(f'Noncanonical encoding at step {start}')
        result.append(ins)
    raise ValueError('Missing END')
