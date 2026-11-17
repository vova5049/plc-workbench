#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Parser eksportu listy instrukcji (IL) z GX Works2 (Mitsubishi FX).

Format wejścia (eksport GX Works2 -> CSV listy instrukcji):
  - kodowanie UTF-16LE (z BOM) lub UTF-8, separator TAB, pola w cudzysłowie, CRLF
  - wiersz 0: nazwa danych (np. "MAIN")
  - wiersz 1: "PLC Information:" \\t "FXCPU FX3U/FX3UC"
  - wiersz 2: nagłówek: Step No. | Line Statement | Instruction | I/O(Device) | Blank | PI Statement | Note
  - dane: wiersz instrukcji ma niepusty Step + Instruction + 1. operand;
          kolejne operandy tej samej instrukcji są w wierszach z pustym Step i Instruction.

Użycie:
  python -m fx3u.dsl.gxworks_csv <plik.csv> --summary          # metadane + liczby urządzeń/instrukcji
  python -m fx3u.dsl.gxworks_csv <plik.csv> --devices          # unikalne X/Y/M/S/T/C/D + liczba wystąpień
  python -m fx3u.dsl.gxworks_csv <plik.csv> --il               # zrekonstruowana lista instrukcji
  python -m fx3u.dsl.gxworks_csv <plik.csv> --xref M710        # kroki, w których występuje urządzenie
  python -m fx3u.dsl.gxworks_csv <plik.csv> --stl              # szkielet sekwencji STL (stany S)
  python -m fx3u.dsl.gxworks_csv <plik.csv> --instr MOV        # wszystkie wystąpienia instrukcji

Inspekcja: parse(path)/parse_lenient(path) -> (meta, instrs).
Kompilacja: parse_strict(path) -> (meta, instrs), bez częściowego wyniku.
"""
import sys, csv, io, json, argparse
from collections import Counter, defaultdict
from ..analysis import operand_devices, query_usage


PROGRAM_HEADER = ('Step No.', 'Line Statement', 'Instruction', 'I/O(Device)',
                  'Blank', 'PI Statement', 'Note')


class CSVParseError(ValueError):
    """Strict import refusal; counters describe the processed data prefix.

    Metadata/header records are excluded. The first rejected record ends the
    import, so records_rejected is 1 and later records are not inspected.
    """

    def __init__(self, message, *, line_number, records_imported=0):
        self.line_number = line_number
        self.records_imported = records_imported
        self.records_rejected = 1
        super().__init__(f'CSV line {line_number}: {message} '
                         f'(records_imported={records_imported}, records_rejected=1)')


def read_rows(path):
    data = open(path, 'rb').read()
    if data[:2] in (b'\xff\xfe', b'\xfe\xff'):
        text = data.decode('utf-16')
    else:
        text = data.decode('utf-8-sig')
    return list(csv.reader(io.StringIO(text), delimiter='\t', quotechar='"'))


def parse_lenient(path):
    """Best-effort inspection of legacy exports; never use for compilation."""
    rows = read_rows(path)
    meta = {'title': '', 'plc': ''}
    header_idx = None
    for i, r in enumerate(rows[:8]):
        if not r:
            continue
        c0 = (r[0] or '').strip()
        if i == 0:
            meta['title'] = c0
        if c0.startswith('PLC Information') and len(r) > 1:
            meta['plc'] = r[1].strip()
        if c0 == 'Step No.':
            header_idx = i
            break
    if header_idx is None:
        header_idx = 2
    instrs = []
    cur = None
    for r in rows[header_idx + 1:]:
        r = list(r) + [''] * (4 - len(r)) if len(r) < 4 else r
        step = (r[0] or '').strip()
        ins = (r[2] or '').strip()
        dev = (r[3] or '').strip()
        if ins:
            cur = {'step': step, 'mnemonic': ins, 'operands': ([dev] if dev else [])}
            instrs.append(cur)
        elif dev and cur is not None and not step:
            cur['operands'].append(dev)
    return meta, instrs


def parse(path):
    """Backward-compatible alias for the lenient inspection parser."""
    return parse_lenient(path)


def parse_strict(path):
    """Read GX Works2 IL record structure without silently dropping data.

    Require the exact seven-column header, optional title/PLC metadata, decimal
    increasing steps, and complete instruction/operand-continuation records.
    Instruction support and arity belong to the selected encoder, not here.
    This is structural validation only. Compilation callers must also call
    validate_compiled_steps with the selected encoder's complete source map,
    before using compiled output: increasing steps alone do not prove continuity.
    Success returns the usual (meta, instructions) pair, adding records_imported
    and records_rejected to meta. Counts cover logical CSV data records, including
    operand continuations (not physical lines inside quoted multiline fields).
    An error raises CSVParseError with prefix counters and no partial result.
    """
    meta = {'title': '', 'plc': '', 'records_imported': 0, 'records_rejected': 0}
    try:
        with open(path, 'rb') as source:
            data = source.read()
        text = data.decode('utf-16' if data[:2] in (b'\xff\xfe', b'\xfe\xff') else 'utf-8-sig')
    except UnicodeError as exc:
        raise CSVParseError(f'invalid text encoding: {exc}', line_number=1) from exc
    reader = csv.reader(io.StringIO(text, newline=''), delimiter='\t',
                        quotechar='"', strict=True)
    header_seen = False
    title_seen = plc_seen = False
    instrs = []
    current = None
    previous_step = -1

    def refuse(message):
        raise CSVParseError(message, line_number=reader.line_num,
                            records_imported=meta['records_imported'])

    try:
        for row in reader:
            if not header_seen:
                if tuple(row) == PROGRAM_HEADER:
                    header_seen = True
                elif (len(row) == 1 and row[0].strip() and not title_seen
                      and not plc_seen and row[0] != PROGRAM_HEADER[0]):
                    meta['title'] = row[0]
                    title_seen = True
                elif (len(row) == 2 and row[0] == 'PLC Information:'
                      and row[1].strip() and not plc_seen):
                    meta['plc'] = row[1]
                    plc_seen = True
                else:
                    refuse('expected known seven-column GX Works2 header or title/PLC metadata')
                continue
            if len(row) != len(PROGRAM_HEADER):
                refuse(f'expected seven columns, got {len(row)}')
            step, mnemonic, operand = (row[i].strip() for i in (0, 2, 3))
            if row[4]:
                refuse('reserved Blank column must be empty')
            if mnemonic:
                if not step.isascii() or not step.isdecimal():
                    refuse('instruction requires a nonnegative decimal step')
                number = int(step)
                if number <= previous_step:
                    refuse('instruction steps must strictly increase')
                previous_step = number
                current = {'step': step, 'mnemonic': mnemonic,
                           'operands': [operand] if operand else []}
                instrs.append(current)
            elif step:
                refuse('step without instruction')
            elif operand:
                if current is None or not current['operands']:
                    refuse('orphan operand continuation (missing instruction or first operand)')
                current['operands'].append(operand)
            else:
                refuse('record has neither instruction nor continuation operand')
            meta['records_imported'] += 1
    except csv.Error as exc:
        refuse(f'malformed CSV: {exc}')
    if not header_seen:
        refuse('missing known seven-column GX Works2 header')
    if not instrs:
        refuse('no instruction records')
    return meta, instrs


def validate_compiled_steps(instrs, mapping):
    """Require every CSV instruction to retain its selected encoder step.

    Call after strict parsing and assembly, before writing compiled artifacts.
    Steps start at zero; multiword instructions, explicit NOPs and labels use
    the encoder's actual map rather than an independent mnemonic-size table.
    A gap is refused, never filled with invented instructions. Partial exports
    and GX constructions unsupported by the encoder remain outside this import.
    """
    if len(instrs) != len(mapping):
        raise ValueError('CSV instruction count differs from compiler step map')
    for index, (source, compiled) in enumerate(zip(instrs, mapping), 1):
        source_step = int(source['step'])
        expected_step = compiled['step']
        if source_step != expected_step:
            raise ValueError(
                f'CSV instruction {index} ({source["mnemonic"]}): '
                f'source step {source_step} differs from compiler step {expected_step}; '
                'missing instructions or unsupported step layout')


def devices(instrs):
    """Count explicit operand anchors, not all occupied device addresses."""
    cnt = Counter()
    for it in instrs:
        for op in it['operands']:
            for device in operand_devices(op):
                cnt[device] += 1
    return cnt


def sortkey(d):
    return ('XYMSTCDRVZ'.index(d[0]), int(d[1:], 8 if d[0] in 'XY' else 10))


def main():
    ap = argparse.ArgumentParser(description='Parser IL CSV z GX Works2 (FX)')
    ap.add_argument('csv')
    ap.add_argument('--summary', action='store_true')
    ap.add_argument('--devices', action='store_true')
    ap.add_argument('--il', action='store_true')
    ap.add_argument('--xref', metavar='DEV')
    ap.add_argument('--instr', metavar='MNEMONIC')
    ap.add_argument('--stl', action='store_true')
    a = ap.parse_args()
    meta, instrs = parse(a.csv)

    if a.summary or not any([a.devices, a.il, a.xref, a.instr, a.stl]):
        cnt = devices(instrs)
        byt = Counter(d[0] for d in cnt)
        print('Nazwa danych :', meta['title'])
        print('PLC          :', meta['plc'])
        print('Instrukcje    :', len(instrs))
        mn = Counter(it['mnemonic'] for it in instrs)
        print('Unikalne mnemoniki:', len(mn))
        print('Urządzenia (distinct):', dict(sorted(byt.items())), 'razem', len(cnt))

    if a.devices:
        cnt = devices(instrs)
        for d in sorted(cnt, key=sortkey):
            print('%-6s %d' % (d, cnt[d]))

    if a.il:
        for it in instrs:
            print('%6s  %-6s %s' % (it['step'], it['mnemonic'], ' '.join(it['operands'])))

    if a.xref:
        report = query_usage(instrs, a.xref)
        for row in report['hits'] + report['uncertainties']:
            row['step'] = instrs[row['instruction_index']]['step']
        print(json.dumps(report, indent=2))

    if a.instr:
        m = a.instr.upper()
        for it in instrs:
            if it['mnemonic'].upper() == m:
                print('%6s  %-6s %s' % (it['step'], it['mnemonic'], ' '.join(it['operands'])))

    if a.stl:
        for it in instrs:
            mn = it['mnemonic'].upper()
            ops = ' '.join(it['operands'])
            if mn == 'STL' or (mn in ('SET', 'OUT', 'RST') and it['operands'] and it['operands'][0].startswith('S') and it['operands'][0][1:].isdigit()):
                print('%6s  %-6s %s' % (it['step'], it['mnemonic'], ops))


if __name__ == '__main__':
    main()
