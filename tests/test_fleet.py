import json
import tempfile
import unittest
from pathlib import Path

from fx3u.fleet import load_config


class FleetConfigTests(unittest.TestCase):
    def load(self, rows):
        with tempfile.TemporaryDirectory() as tmp:
            p = Path(tmp)/'c.json'
            p.write_text(json.dumps(rows))
            return load_config(p)

    def test_valid_and_normalised(self):
        rows = self.load([{'name': 'PLC1', 'port': 'com6'}, {'name': 'PLC2', 'port': 'COM7'}])
        self.assertEqual(['COM6', 'COM7'], [r['port'] for r in rows])

    def test_rejected(self):
        for rows in ([], {}, [{'name': 'a b', 'port': 'COM1'}], [{'name': 'A', 'port': '/dev/ttyUSB0'}],
                     [{'name': 'A', 'port': 'COM1'}, {'name': 'a', 'port': 'COM2'}],
                     [{'name': 'A', 'port': 'COM1'}, {'name': 'B', 'port': 'com1'}]):
            with self.subTest(rows=rows), self.assertRaises((ValueError, KeyError)):
                self.load(rows)

    def test_example_config_is_valid(self):
        root = Path(__file__).resolve().parents[1]
        self.assertEqual(2, len(load_config(root/'examples/fleet.example.json')))
