"""Conformance against real, private programs (never distributed).

Set FX3U_PRIVATE_CORPUS to a JSON manifest kept outside this repo:
  {"cases": [{"name": "...", "csv": "<GX Works2 IL CSV export>",
              "readback": "<program region read from the PLC>",
              "instructions": 1585, "last_step": 2976}]}
Relative paths are resolved against the manifest's directory. Without the variable the test
is skipped; with it, the encoder must reproduce every PLC read-back byte for byte.
"""
import json
import os
import unittest
from pathlib import Path

from fx3u.bytecode import Instruction, assemble, disassemble
from fx3u.dsl.gxworks_csv import parse

MANIFEST = os.environ.get('FX3U_PRIVATE_CORPUS')


@unittest.skipUnless(MANIFEST, 'FX3U_PRIVATE_CORPUS not set (private conformance corpus)')
class PrivateCorpusTests(unittest.TestCase):
    def test_byte_identical_with_plc_readback(self):
        manifest = Path(MANIFEST)
        cases = json.loads(manifest.read_text(encoding='utf-8'))['cases']
        self.assertTrue(cases)
        for case in cases:
            with self.subTest(case=case['name']):
                _, rows = parse(str(manifest.parent/case['csv']))
                instructions = [Instruction(r['mnemonic'], tuple(r['operands'])) for r in rows]
                actual, mapping = assemble(instructions)
                expected = (manifest.parent/case['readback']).read_bytes()
                self.assertEqual(case['instructions'], len(instructions))
                self.assertEqual(case['last_step'], mapping[-1]['step'])
                self.assertEqual(expected, actual)
                self.assertEqual(expected, assemble(disassemble(expected))[0])
                for ins, row in zip(instructions, mapping):
                    self.assertEqual(str(ins), row['instruction'])
