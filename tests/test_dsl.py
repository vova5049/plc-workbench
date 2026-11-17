import tempfile
import unittest
from pathlib import Path

from fx3u.bytecode import Instruction, assemble, disassemble
from fx3u.dsl import Prog, parse, write_utf16
from fx3u.dsl.fx_dsl import demo_click_classifier, demo_self_hold


def roundtrip(prog_csv):
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp)/'p.csv'
        write_utf16(str(path), prog_csv)
        return parse(str(path))[1]


class DslTests(unittest.TestCase):
    @staticmethod
    def declarations(program):
        return (dict(program.sym), dict(program.meta), dict(program.comments),
                dict(program._device_owners))

    def test_name_retargeting_is_rejected_without_mutation(self):
        p = Prog()
        p.dev('command', 'M0', 'Original command')
        p.LD('command')
        before = self.declarations(p)
        with self.assertRaises(ValueError):
            p.dev('command', 'M1', 'Replacement')
        self.assertEqual(before, self.declarations(p))
        self.assertEqual([('LD', 'M0')], p.code)

    def test_duplicate_address_is_canonicalized_without_changing_spelling(self):
        for first, second in [('M1', 'm001'), ('D0', 'D000'),
                              ('X10', 'x010'), ('Y007', 'y7'), ('M0', ' M000 ')]:
            with self.subTest(first=first):
                p = Prog()
                p.dev('first', first)
                before = self.declarations(p)
                with self.assertRaises(ValueError):
                    p.dev('second', second)
                self.assertEqual(before, self.declarations(p))
                self.assertEqual(first, p.sym['first'])

    def test_identical_declaration_is_idempotent_across_modules(self):
        p = Prog()
        with p.module('original'):
            p.dev('input', 'X000', 'Button')
        before = self.declarations(p)
        with p.module('consumer'):
            self.assertEqual('X000', p.dev('input', 'x0'))
            self.assertEqual('X000', p.dev('input', 'X0', 'Button'))
            with self.assertRaises(ValueError):
                p.dev('input', 'X0', 'Different button')
        self.assertEqual(before, self.declarations(p))
        self.assertEqual(('X000', 'Button', 'original'), p.meta['input'])

    def test_alias_is_explicit_and_preserves_physical_comment(self):
        p = Prog()
        p.dev('source', 'M000', 'Shared condition')
        with p.module('consumer'):
            self.assertEqual('M000', p.alias('shared', 'source'))
        self.assertEqual('M000', p.alias('chain', 'shared'))
        p.LD('shared')
        self.assertEqual([('LD', 'M000')], p.code)
        self.assertEqual({'M000': 'Shared condition'}, p.comments)
        self.assertEqual(('M000', 'Shared condition', 'consumer'), p.meta['shared'])
        before = self.declarations(p)
        for name, target in [('shared', 'source'), ('source', 'shared'),
                             ('missing', 'not_declared')]:
            with self.subTest(name=name), self.assertRaises(ValueError):
                p.alias(name, target)
            self.assertEqual(before, self.declarations(p))
        with self.assertRaises(ValueError):
            p.dev('implicit', 'M0')
        self.assertEqual(before, self.declarations(p))

    def test_invalid_octal_declaration_is_rejected_before_mutation(self):
        for device in ('X8', 'Y009', 'x18'):
            p = Prog()
            before = self.declarations(p)
            with self.subTest(device=device), self.assertRaises(ValueError):
                p.dev('bad', device)
            self.assertEqual(before, self.declarations(p))

    def test_alloc_name_conflict_does_not_consume_iterator(self):
        p = Prog()
        p.set_ranges(M=(0, 2))
        self.assertEqual('M0', p.alloc('first', 'M'))
        before = self.declarations(p)
        with self.assertRaises(ValueError):
            p.alloc('first', 'M')
        self.assertEqual(before, self.declarations(p))
        self.assertEqual('M1', p.alloc('second', 'M'))

    def test_allocation_skips_global_addresses_and_restores_module_ranges(self):
        p = Prog()
        p.set_ranges(M=(5, 7))
        with p.module('first', M=(0, 2)):
            p.dev('occupied', 'm000')
            self.assertEqual('M1', p.alloc('local_first', 'M'))
        with p.module('second', M=(0, 3)):
            self.assertEqual('M2', p.alloc('local_second', 'M'))
            with self.assertRaises(ValueError):
                p.alloc('local_first', 'M')
            with self.assertRaises(ValueError):
                p.dev('duplicate', 'M000')
            p.alias('shared', 'occupied')
        self.assertEqual('M5', p.alloc('global', 'M'))

    def test_allocation_respects_canonical_comment_only_reservations(self):
        p = Prog()
        p.comments['M000'] = 'Reserved outside a named declaration'
        p.set_ranges(M=(0, 1))
        self.assertEqual('M1', p.alloc('available', 'M'))

    def test_compile_parse_roundtrip(self):
        for fn in (demo_self_hold, demo_click_classifier):
            with self.subTest(fn=fn.__name__):
                rows = roundtrip(fn()[0])
                steps = [r['step'] for r in rows]
                self.assertTrue(all(s.isdigit() for s in steps))
                self.assertEqual(steps, sorted(steps, key=int))
                self.assertEqual('END', rows[-1]['mnemonic'])

    def test_dsl_output_can_exceed_encoder_subset(self):
        # The DSL targets GX import; the direct encoder is a qualified subset and must refuse the rest.
        rows = roundtrip(demo_self_hold()[0])
        with self.assertRaises(ValueError):
            assemble([Instruction(r['mnemonic'], tuple(r['operands'])) for r in rows])

    def test_subset_program_to_bytecode(self):
        from fx3u.dsl.il_compiler import compile_program
        program = [('LD', 'M0'), ('PLS', 'M1'), ('LD', 'M1'), ('INC', 'D0'), ('END',)]
        rows = roundtrip(compile_program(program, {}, 'MAIN')[0])
        ins = [Instruction(r['mnemonic'], tuple(r['operands'])) for r in rows]
        self.assertEqual(ins, disassemble(assemble(ins)[0]))
