import unittest
from fx3u.bytecode import assemble, disassemble, parse_il
from pathlib import Path

LAB = (Path(__file__).resolve().parents[1]/'examples/instruction_lab.il').read_text()


class InstructionLabTests(unittest.TestCase):
    def test_external_reference_vectors(self):
        # FX2N doc/21 encoding: FNC=(n+8)*2; bit M = 0x800+n.
        vectors = {
            'LDI T48': '3036', 'OUT C0 K10': '000e0a800080',
            'LD C0': '002e', 'RST C0': '0c00008e',
            'ROL D121 K1': '4e00f286008601800080',
            'SFTL M249 M241 K8 K1': '5600f9840880f18408800880008001800080',
        }
        for il, raw in vectors.items():
            with self.subTest(il=il):
                program = parse_il(il+'\nEND')
                self.assertEqual(bytes.fromhex(raw+'0f00'), assemble(program, False)[0])
                self.assertEqual(program, disassemble(assemble(program)[0]))
        program = parse_il(LAB)
        self.assertEqual(program, disassemble(assemble(program)[0]))
        self.assertLess(len(assemble(program, False)[0]), 142)

    def test_bounds_and_variants_rejected(self):
        for il in ['OUT C1 K10', 'LD T49', 'SFTL M255 M241 K8 K2',
                   'SFTL M249 M250 K8 K1', 'SFTL M249 M241 K1 K2',
                   'ROL D121 K17', 'ROL D121 K0', 'DROL D121 K1']:
            with self.subTest(il=il), self.assertRaises(ValueError):
                assemble(parse_il(il+'\nEND'))
