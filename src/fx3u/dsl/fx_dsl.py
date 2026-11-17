#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Warstwa DSL nad il_compiler — czytelne, modularne pisanie logiki FX3U -> CSV GX Works2.

Ogólny toolchain FX3U (nie przywiązany do konkretnej maszyny). Logikę piszemy
przez nazwane symbole (z komentarzem i przydziałem urządzeń), kompilator
generuje IL + komentarze do importu w GX Works2.

Round-trip do GX Works2 (eksport CSV -> parse -> compile -> import) potwierdzony
przez autorów na realnym programie 1641 instrukcji (podstawowe + aplikacyjne +
STL + podprogramy + komentarze + numery kroków). Program źródłowy nie jest
częścią tego repozytorium. Nie jest to publiczny przykład 1585 instrukcji;
ten ma odrębny historyczny reference i poprawiony wariant testowany offline.

Elementy:
- Prog: builder (instrukcje fluent, alokacja/nazywanie urządzeń, mapa urządzeń).
- moduły: `with p.module('nazwa', M=(700,799), T=(50,59), D=(560,599)):` —
  namespacing globali FX (FX3U nie ma prawdziwych tasków/zmiennych lokalnych).
- STL: `p.stl('S20')`, przejścia `p.to('S18')`; `p.fend()/ret()/pointer()/sret()/end()`.
- wzorce: `self_hold`, `on_delay`, `pulse`, `in_remap`, `click_button`.
- device_map(): tabela nazwa->urządzenie->komentarz (do dokumentacji).

Walidacja: `demo_click_classifier()` + `tests/test_dsl.py`
(round-trip parse(compile(x)) == x na wygenerowanym programie — bez maszyny
odniesienia, bo to kod generyczny, nie odtwarzany z realnego eksportu).
"""
import sys, os, re
from contextlib import contextmanager
from .il_compiler import compile_program, write_utf16

CONST = re.compile(r'^[KH][0-9A-Fa-f]+$')

# Instrukcje jedno-/wieloargumentowe emitowane generycznie (nazwa metody = mnemonik).
_ONE = ['LD', 'LDI', 'AND', 'ANI', 'OR', 'ORI', 'LDP', 'LDF', 'ANDP', 'ANDF', 'ORP', 'ORF',
        'SET', 'RST', 'PLS', 'PLF']
_ZERO = ['ANB', 'ORB', 'MPS', 'MRD', 'MPP', 'INV', 'RET', 'FEND', 'SRET', 'END', 'NOP']
_MULTI = ['OUT', 'MOV', 'MOVP', 'CMP', 'ADD', 'SUB', 'MUL', 'DIV', 'ZRST', 'INC', 'DEC',
          'DMOV', 'DMUL', 'DDIV', 'DADD', 'DSUB', 'DCMP', 'TO', 'FROM', 'CALL', 'CALLP', 'CJ',
          'FLT', 'INT', 'DCOS', 'DSIN', 'DTAN', 'DEADD', 'DESUB', 'DEMUL', 'DEDIV', 'DECMP', 'DESQR']

# Styki porównania (LD=/AND>/OR<= …) — nazwy metod Pythona nie mogą zawierać
# operatorów, więc mapujemy jawnie: metoda -> (prefiks, operator).
_CMP_OPS = {'eq': '=', 'ne': '<>', 'gt': '>', 'ge': '>=', 'lt': '<', 'le': '<='}
_CMP = [(pfx + name, pfx, op) for pfx in ('LD', 'AND', 'OR') for name, op in _CMP_OPS.items()]


class Prog:
    def __init__(self):
        self.code = []              # [(mnem, *ops)]
        self.comments = {}          # device -> comment
        self.sym = {}               # nazwa -> urządzenie
        self.meta = {}              # nazwa -> (urządzenie, komentarz, modul)
        self._device_owners = {}    # canonical operand -> first declaring symbol
        self._ranges = {}           # kind -> iterator (globalny fallback)
        self._cur_module = None
        self._install()

    # ---- urządzenia / symbole ----
    @staticmethod
    def _device_key(device):
        """Compare direct devices by address without changing emitted spelling."""
        if not isinstance(device, str) or not device.strip():
            raise ValueError('Device must be a nonempty string')
        text = device.strip().upper()
        direct = re.fullmatch(r'([A-Z]+)([0-9]+)', text)
        if direct:
            kind, digits = direct.groups()
            try:
                return kind, int(digits, 8 if kind in ('X', 'Y') else 10)
            except ValueError:
                raise ValueError('Invalid octal X/Y device: %s' % device) from None
        return text

    def _new_symbol(self, name):
        if not isinstance(name, str) or not name:
            raise ValueError('Symbol name must be a nonempty string')
        if name in self.sym:
            raise ValueError('Symbol already declared: %s' % name)

    def dev(self, name, device, comment=None):
        if not isinstance(name, str) or not name:
            raise ValueError('Symbol name must be a nonempty string')
        key = self._device_key(device)
        if name in self.sym:
            previous = self.sym[name]
            if self._device_key(previous) != key:
                raise ValueError('Cannot change device of symbol: %s' % name)
            if comment and comment != self.meta[name][1]:
                raise ValueError('Conflicting comment for symbol: %s' % name)
            return previous
        if key in self._device_owners:
            raise ValueError('Device already declared by %s; use alias() for sharing'
                             % self._device_owners[key])
        self.sym[name] = device
        self._device_owners[key] = name
        if comment:
            self.comments[device] = comment
        self.meta[name] = (device, comment, self._cur_module)
        return device

    def alias(self, name, existing_name):
        """Explicitly share an existing symbol; keep its physical comment unchanged."""
        self._new_symbol(name)
        if existing_name not in self.sym:
            raise ValueError('Alias target is not declared: %s' % existing_name)
        device = self.sym[existing_name]
        self.sym[name] = device
        self.meta[name] = (device, self.meta[existing_name][1], self._cur_module)
        return device

    def alloc(self, name, kind, comment=None, ranges=None):
        self._new_symbol(name)  # Refuse rebinding before consuming the iterator.
        it = (ranges or self._ranges).get(kind)
        if it is None:
            raise ValueError('Brak zakresu alokacji dla %s (użyj module() lub set_ranges())' % kind)
        taken = {self._device_key(value) for value in self.sym.values()}
        taken.update(self._device_key(value) for value in self.comments)
        for n in it:
            device = '%s%d' % (kind, n)
            if self._device_key(device) not in taken:  # Skip global reservations.
                return self.dev(name, device, comment)
        raise ValueError('Wyczerpany zakres alokacji dla %s' % kind)

    def set_ranges(self, **kw):
        for k, (a, b) in kw.items():
            self._ranges[k] = iter(range(a, b + 1))

    @contextmanager
    def module(self, mod_name, **kw):
        """Namespacing: alokacje w bloku biorą z zakresów tego modułu."""
        prev_mod, prev_ranges = self._cur_module, dict(self._ranges)
        self._cur_module = mod_name
        for k, (a, b) in kw.items():
            self._ranges[k] = iter(range(a, b + 1))
        try:
            yield self
        finally:
            self._cur_module, self._ranges = prev_mod, prev_ranges

    def _r(self, x):
        if x is None:
            return x
        if x in self.sym:
            return self.sym[x]
        return x                    # urządzenie surowe albo stała K/H

    # ---- instrukcje ----
    def i(self, mnem, *ops):
        self.code.append((mnem, *[str(self._r(o)) for o in ops]))
        return self

    def _install(self):
        for m in _ONE:
            setattr(self, m, (lambda mm: (lambda x: self.i(mm, x)))(m))
        for m in _ZERO:
            setattr(self, m, (lambda mm: (lambda: self.i(mm)))(m))
        for m in _MULTI:
            setattr(self, m, (lambda mm: (lambda *a: self.i(mm, *a)))(m))
        for pyname, pfx, op in _CMP:
            setattr(self, pyname, (lambda mnem: (lambda a, b: self.i(mnem, a, b)))(pfx + op))

    # ---- STL / struktura ----
    def stl(self, state, comment=None):
        if comment:
            self.comments[self._r(state)] = comment
        return self.i('STL', state)

    def to(self, state):            # przejście STL: SET stanu
        return self.i('SET', state)

    def pointer(self, name):        # definicja podprogramu (P..)
        return self.i(self._r(name))

    # ---- wzorce ----
    def self_hold(self, out, set_cond, reset_cond):
        """out = (set_cond OR out) AND NOT reset_cond."""
        return self.LD(set_cond).OR(out).ANI(reset_cond).OUT(out)

    def on_delay(self, cond, timer, preset):
        """timer nalicza gdy cond; użyj styku timer'a dalej."""
        return self.LD(cond).OUT(timer, preset)

    def pulse(self, cond, bit):
        return self.LD(cond).PLS(bit)

    def in_remap(self, m_out, x_in):
        """Fizyczne wejście (X) -> pośredni bit (M). Dalsza logika odwołuje się
        WYŁĄCZNIE do m_out — zmiana okablowania to zmiana jednej linii tutaj."""
        return self.LD(x_in).OUT(m_out)

    def toggle(self, out, pulse):
        """out zmienia stan przy kazdym jednoscanowym impulsie pulse (XOR-latch,
        bez FNC66 ALT — przenosne, sprawdzone IL: LD/ANI + LD/ANI + ORB)."""
        return self.LD(pulse).ANI(out).LD(out).ANI(pulse).ORB().OUT(out)

    def force_off(self, out, pulse):
        """out bezwarunkowo RST przy kazdym jednoscanowym impulsie pulse —
        w odroznieniu od toggle() nie zalezy od poprzedniego stanu out.
        Typowe uzycie: zbiorcze/grupowe wylaczenie (np. LONG = wylacz liste
        stref), gdzie kilka przyciskow moze bezpiecznie dzielic te sama
        strefe (RST jest idempotentny, w przeciwienstwie do toggle)."""
        return self.LD(pulse).RST(out)

    def auto_off(self, zone, minutes, minute_clock='M8014'):
        """Wymusza wylaczenie `zone` po `minutes` minutach CIAGLEGO wlaczenia,
        niezaleznie co ja wlaczylo (przycisk, PC przez Computer Link, ...).
        Uzywa 1 licznika (C, wymaga zakresu 'C' w biezacym module()) + zegara
        systemowego M8014 (wbudowany puls 1/min, FX3U — potwierdzone w
        fx-plc-reference, FX_PROGRAMMING ch6). Licznik resetuje sie przy
        kazdym wylaczeniu strefy (gotowy na nastepne wlaczenie). Preset do
        32767 min (~22 dni) — 16-bit counter, wystarcza z zapasem na godziny."""
        cnt = self.alloc('%s_autooff' % zone, 'C', 'auto-off %s po %d min' % (zone, minutes))
        self.LD(zone).AND(minute_clock).OUT(cnt, 'K%d' % minutes)
        self.LD(cnt).RST(zone)
        self.LDI(zone).RST(cnt)
        return cnt

    def click_button(self, btn, win='K4', long_press='K8'):
        """Klasyfikator kliknięcia: pojedyncze/podwójne/potrójne/długie.

        `btn` = nazwa już zremapowanego bitu pośredniego (patrz in_remap) —
        NIE surowe wejście X. Wywoływać wewnątrz `with p.module(...):` —
        alokuje własne urządzenia pomocnicze z zakresów bieżącego modułu
        (M/T/D) i zwraca dict z nazwami 4 bitów komend (jednoscanowe impulsy
        PLS): single/double/triple/long.

        Timing (HIPOTEZA — do skalibrowania na realnych przyciskach):
        `win` = okno ciszy PO PUSZCZENIU przycisku, po którym zliczamy
        kliknięcia (domyślnie K4 = 400 ms przy timerze 100 ms). `long_press`
        = próg przytrzymania (domyślnie K8 = 800 ms). Trzy i więcej kliknięć
        w oknie traktowane jako "triple".

        Okno ciszy liczy się TYLKO gdy przycisk jest aktualnie PUSZCZONY
        (R3b: `ANI btn`) — dzięki temu długie przytrzymanie (>= long_press)
        nie generuje najpierw fałszywego SINGLE w trakcie trzymania. Gdy
        LONG faktycznie się odpali (R6), R6b/R6c/R6d bezwarunkowo kasują
        stan liczenia (active/win_t/cnt_d), żeby po puszczeniu nie odpalił
        się spóźniony SINGLE z resztek starego cyklu. Nowa krawędź ma
        pierwszeństwo przed stykiem zakończonego okna z poprzedniego skanu:
        active = edge OR (active AND NOT win_t).
        """
        p = '%s_' % btn
        edge = self.alloc(p + 'edge', 'M', 'edge %s' % btn)
        active = self.alloc(p + 'active', 'M', 'aktywne liczenie %s' % btn)
        win_t = self.alloc(p + 'win', 'T', 'okno ciszy %s' % btn)
        long_t = self.alloc(p + 'long', 'T', 'prog dlugiego %s' % btn)
        long_fired = self.alloc(p + 'longfired', 'M', 'dlugie juz odpalone %s' % btn)
        finalize = self.alloc(p + 'final', 'M', 'finalizacja liczenia %s' % btn)
        cnt_d = self.alloc(p + 'cnt', 'D', 'licznik klikniec %s' % btn)
        cmd_single = self.alloc(p + 'single', 'M', 'KOMENDA pojedyncze %s' % btn)
        cmd_double = self.alloc(p + 'double', 'M', 'KOMENDA podwojne %s' % btn)
        cmd_triple = self.alloc(p + 'triple', 'M', 'KOMENDA potrojne+ %s' % btn)
        cmd_long = self.alloc(p + 'longcmd', 'M', 'KOMENDA dlugie %s' % btn)

        self.LD(btn).PLS(edge)                                   # R1: krawedz wcisniecia
        self.LD(active).ANI(win_t).OR(edge).OUT(active)           # R2: nowa krawedz ma pierwszenstwo
        self.LD(edge).RST(win_t)                                  # R3a: restart okna na kazdym kliknieciu
        self.LD(active).ANI(btn).OUT(win_t, win)                  # R3b: naliczanie okna TYLKO po puszczeniu
        self.LD(btn).OUT(long_t, long_press)                      # R4: naliczanie przytrzymania
        self.LD(long_t).ANI(long_fired).PLS(cmd_long)             # R5: komenda "dlugie" (raz)
        self.LD(cmd_long).SET(long_fired)                         # R6: zatrzask "dlugie juz bylo"
        self.LD(cmd_long).RST(active)                             # R6b: kasuj liczenie klikniec
        self.LD(cmd_long).RST(win_t)                              # R6c: kasuj okno ciszy
        self.LD(cmd_long).MOV('K0', cnt_d)                        # R6d: kasuj licznik klikniec
        self.LDI(btn).RST(long_fired)                             # R7: odblokuj po puszczeniu
        self.LD(edge).INC(cnt_d)                                  # R8: licznik klikniec w oknie
        self.LD(win_t).ANI(long_fired).PLS(finalize)              # R9: koniec okna, bez dlugiego
        self.LD(finalize).ANDeq(cnt_d, 'K1').PLS(cmd_single)      # R10
        self.LD(finalize).ANDeq(cnt_d, 'K2').PLS(cmd_double)      # R11
        self.LD(finalize).ANDge(cnt_d, 'K3').PLS(cmd_triple)      # R12
        self.LD(finalize).MOV('K0', cnt_d)                        # R13: reset licznika

        return {'single': cmd_single, 'double': cmd_double,
                'triple': cmd_triple, 'long': cmd_long}

    # ---- wyjścia ----
    def device_map(self):
        rows = []
        for name, (dev, com, mod) in self.meta.items():
            rows.append((mod or '-', name, dev, com or ''))
        return sorted(rows)

    def compile(self, name):
        return compile_program(self.code, self.comments, name)


# ===================== DEMA / WALIDACJA =====================

def demo_self_hold():
    """Minimalny przyklad: start/stop z samopodtrzymaniem (walidacja podstaw)."""
    p = Prog()
    p.dev('start', 'X000', 'Start')
    p.dev('hold', 'M0', 'Podtrzymanie')
    p.dev('stop', 'X001', 'Stop')
    p.self_hold('hold', 'start', 'stop')
    p.END()
    return p.compile('SHTEST')


def demo_click_classifier():
    """Jeden przycisk: remap X->M + pelny klasyfikator kliknec (walidacja wzorca)."""
    p = Prog()
    p.dev('x_btn', 'X000', 'Przycisk fizyczny')
    with p.module('btn00', M=(0, 19), T=(0, 1), D=(0, 0)):
        p.alloc('btn', 'M', 'Przycisk (posredni)')
        p.in_remap('btn', 'x_btn')
        cmds = p.click_button('btn', win='K4', long_press='K8')
    p.dev('y_zone', 'Y000', 'Strefa oswietlenia')
    p.dev('y_group', 'Y001', 'Strefa grupowa (demo force_off)')
    p.toggle('y_zone', cmds['single'])      # SINGLE = przelacz strefe (demo)
    p.force_off('y_group', cmds['long'])    # LONG = wymus wylaczenie (demo)
    with p.module('autooff', C=(0, 0)):
        p.auto_off('y_zone', 360)           # demo: auto-off po 6h (360 min)
    p.END()
    return p.compile('CLICKTST')


if __name__ == '__main__':
    which = sys.argv[2] if len(sys.argv) > 2 else 'click'
    out = sys.argv[1] if len(sys.argv) > 1 else 'out'
    prog, com = {'shtest': demo_self_hold, 'click': demo_click_classifier}[which]()
    write_utf16(out + '.csv', prog)
    write_utf16(out + '_COMMENT.csv', com)
    print('Zapisano:', out + '.csv', 'oraz', out + '_COMMENT.csv')
