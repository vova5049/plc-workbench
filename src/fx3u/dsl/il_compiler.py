#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""PoC kompilator: opis programu w Pythonie -> CSV w formacie GX Works2 (FX IL).

Cel: pisać logikę w czytelnej, modularnej formie i kompilować do plików CSV
(program + komentarze), które da się zaimportować do GX Works2. Round-trip:
parsowanie z powrotem przez fx3u.dsl.gxworks_csv.

UWAGA (do zweryfikowania na GX Works2): czy import wymaga DOKŁADNYCH numerów kroków,
czy GX je przelicza. Tu liczymy je best-effort wg tabeli rozmiarów instrukcji FX.
Zacząć od testu importu małego programu (funkcja demo()).

DSL (minimalny): program = lista krotek (mnemonik, *operandy).
Komentarze: dict {urządzenie: "komentarz"}.
"""
import csv, io, sys

# Rozmiary kroków (FX3U) — podzbiór zweryfikowany na eksporcie MAIN.csv.
# (Brakujące instrukcje aplikacyjne dodać po potwierdzeniu na maszynie/manualu.)
STEP_SIZE = {
    'LD': 1, 'LDI': 1, 'AND': 1, 'ANI': 1, 'OR': 1, 'ORI': 1,
    'ANB': 1, 'ORB': 1, 'MPS': 1, 'MRD': 1, 'MPP': 1, 'INV': 1,
    'LDP': 2, 'LDF': 2, 'ANDP': 2, 'ANDF': 2, 'ORP': 2, 'ORF': 2,
    'OUT': 1,            # OUT Y/M = 1; OUT T/C z presetem = 3 (obsłużone niżej)
    'SET': 1, 'RST': 1,  # SET/RST bit = 1; SET/RST S (stan) = 2 (obsłużone niżej)
    'PLS': 2, 'PLF': 2,
    'STL': 1, 'RET': 1,
    'MC': 3, 'MCR': 2,
    'MOV': 5, 'MOVP': 5, 'CMP': 7, 'ZRST': 5,
    'ADD': 7, 'SUB': 7, 'MUL': 7, 'DIV': 7, 'WAND': 7, 'WOR': 7, 'WXOR': 7, 'INC': 3, 'DEC': 3,
    'END': 1, 'FEND': 1, 'NOP': 1, 'SRET': 1,
    'CALL': 3, 'CALLP': 3, 'CJ': 3,
    'TO': 9, 'FROM': 9, 'DMOV': 9, 'DMUL': 13, 'DDIV': 13, 'DADD': 13, 'DSUB': 13, 'DCMP': 13,
    'FLT': 5, 'INT': 5, 'DCOS': 9, 'DSIN': 9, 'DTAN': 9,
    'DEADD': 13, 'DESUB': 13, 'DEMUL': 13, 'DEDIV': 13, 'DECMP': 13, 'DESQR': 13,
    'DHSCS': 13,   # High-Speed Counter Set (porownanie HSC 32-bit) — potw. w MAIN.csv
}

# Styki porównania 16-bit (LD=/AND>/OR<= …) = 5 kroków (potwierdzone: AND=, AND> w MAIN.csv).
# Wariantów 32-bit (D-prefiks) tu nie ma — dodać po kalibracji, gdy wystąpią.
for _pfx in ('LD', 'AND', 'OR'):
    for _op in ('=', '>', '<', '<=', '>=', '<>'):
        STEP_SIZE[_pfx + _op] = 5


import re as _re
def _dev(op):
    """(litera, numer) dla operandu urządzenia, np. 'C236' -> ('C', 236). Inaczej (None, None)."""
    mm = _re.match(r'^([A-Za-z]+)(\d+)$', op or '')
    return (mm.group(1).upper(), int(mm.group(2))) if mm else (None, None)


def step_size(mnem, ops):
    m = mnem.upper()
    if _re.match(r'^P\d+$', m):           # definicja pointera (etykieta) — jak w eksporcie GX
        return 1
    if m == 'OUT' and len(ops) >= 2:      # OUT T/C z presetem
        letter, num = _dev(ops[0])
        if letter == 'C' and num is not None and num >= 200:
            return 5                      # licznik 32-bit (C200–C255, np. HSC C235+) = 5
        return 3                          # OUT T lub OUT C 16-bit (C0–C199) z presetem = 3
    if m == 'SET' and ops:                # SET stanu STL = 2 (SET Y/M = 1)
        letter, _ = _dev(ops[0])
        if letter == 'S':
            return 2
    if m == 'RST' and ops:                # RST stanu/timera/licznika (word) = 2 (RST Y/M = 1)
        letter, _ = _dev(ops[0])
        if letter in ('S', 'T', 'C'):
            return 2
    if m in STEP_SIZE:
        return STEP_SIZE[m]
    raise ValueError('Nieznany rozmiar kroku dla instrukcji: %s (dodaj do STEP_SIZE)' % mnem)


def compile_program(program, comments, data_name, plc='FXCPU FX3U/FX3UC'):
    """Zwraca (tekst_csv_programu, tekst_csv_komentarzy) — oba do zapisu jako UTF-16LE.

    UWAGA: nazwa programu w FX (GX Works2) = MAX 8 znaków. Import wgrywa IL do wybranego
    programu (głównego = MAIN); pierwszy wiersz CSV to etykieta danych.
    Round-trip import + komentarze potwierdzone na GX Works2 (2026-07-09).
    """
    if len(data_name) > 8:
        sys.stderr.write('UWAGA: nazwa „%s" >8 znaków — FX pozwala max 8; skracam do „%s".\n'
                         % (data_name, data_name[:8]))
        data_name = data_name[:8]
    # --- program CSV ---
    program_output = io.StringIO(newline='')
    writer = csv.writer(program_output, delimiter='\t', quoting=csv.QUOTE_ALL,
                        lineterminator='\r\n')
    writer.writerow([data_name])
    writer.writerow(['PLC Information:', plc])
    hdr = ['Step No.', 'Line Statement', 'Instruction', 'I/O(Device)', 'Blank', 'PI Statement', 'Note']
    writer.writerow(hdr)
    step = 0
    for ins in program:
        mnem = ins[0]
        ops = [str(o) for o in ins[1:]]
        first = ops[0] if ops else ''
        writer.writerow([str(step), '', mnem, first, '', '', ''])
        for extra in ops[1:]:             # operandy kontynuacyjne = wiersze z pustym Step/Instr
            writer.writerow(['', '', '', extra, '', '', ''])
        step += step_size(mnem, ops)
    prog_csv = program_output.getvalue()

    # --- comment CSV --- (FX: komentarz urządzenia max 32 znaki)
    MAX_COMMENT = 32
    comments_output = io.StringIO(newline='')
    comment_writer = csv.writer(comments_output, delimiter='\t', quoting=csv.QUOTE_ALL,
                                lineterminator='\r\n')
    comment_writer.writerow([data_name])
    comment_writer.writerow(['Device Name', 'Comment'])
    for dev in sorted(comments):
        c = comments[dev]
        if len(c) > MAX_COMMENT:
            sys.stderr.write('UWAGA: komentarz %s „%s" >%d znaków — skracam.\n' % (dev, c, MAX_COMMENT))
            c = c[:MAX_COMMENT]
        comment_writer.writerow([dev, c])
    com_csv = comments_output.getvalue()
    return prog_csv, com_csv


def write_utf16(path, text):
    with open(path, 'wb') as f:
        f.write(b'\xff\xfe')                       # BOM UTF-16LE
        f.write(text.encode('utf-16-le'))


def demo():
    """Mały program testowy do sprawdzenia importu w GX Works2."""
    program = [
        ('LD',  'X000'),
        ('OR',  'M0'),
        ('ANI', 'X001'),
        ('OUT', 'M0'),         # samopodtrzymanie: Start X000, Stop X001
        ('LD',  'M0'),
        ('OUT', 'T0', 'K30'),  # timer 3 s
        ('LD',  'T0'),
        ('OUT', 'Y000'),
        ('END',),
    ]
    comments = {
        'X000': 'Start test', 'X001': 'Stop test', 'M0': 'Podtrzymanie',
        'T0': 'Opoznienie 3s', 'Y000': 'Wyjscie test',
    }
    return compile_program(program, comments, 'RTEST')  # nazwa FX max 8 znaków


def demo2():
    """Bogatszy test: STL, MOV, CMP, licznik, podprogram (CALL/P/SRET) — trudniejsze części formatu."""
    program = [
        ('LD', 'M8002'), ('SET', 'S0'),          # init -> S0
        ('STL', 'S0'),
        ('LD', 'X002'), ('OUT', 'C0', 'K5'),      # zlicz 5 iskier
        ('LD', 'C0'), ('SET', 'S20'),
        ('STL', 'S20'),
        ('LD', 'M8000'), ('MOV', 'K100', 'D0'),   # instrukcja aplikacyjna
        ('CMP', 'C0', 'D0', 'M10'),               # porównanie
        ('CALL', 'P0'),                           # podprogram
        ('LD', 'M10'), ('OUT', 'Y001'),
        ('RET',),                                 # koniec sekwencji STL
        ('FEND',),                                # koniec programu głównego
        ('P0',),                                  # podprogram
        ('LD', 'M8000'), ('MOV', 'D0', 'D2'),
        ('SRET',),
        ('END',),
    ]
    comments = {
        'X002': 'Iskra test', 'C0': 'Licznik iskier', 'D0': 'Nastawa', 'D2': 'Kopia',
        'M10': 'C0=D0', 'Y001': 'Wyjscie 2', 'S0': 'Init', 'S20': 'Praca',
    }
    return compile_program(program, comments, 'STLTEST')


if __name__ == '__main__':
    which = 'demo2' if (len(sys.argv) > 2 and sys.argv[2] == 'demo2') else 'demo'
    out = sys.argv[1] if len(sys.argv) > 1 else 'roundtrip_test'
    prog, com = demo2() if which == 'demo2' else demo()
    write_utf16(out + '.csv', prog)
    write_utf16(out + '_COMMENT.csv', com)
    print('Zapisano:', out + '.csv', 'oraz', out + '_COMMENT.csv')
