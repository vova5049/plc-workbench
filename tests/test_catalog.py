import json
import re
import unittest
from importlib import resources
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class CatalogTests(unittest.TestCase):
    def test_inventory_and_evidence_anchors(self):
        data = json.loads(resources.files('fx3u').joinpath('capabilities.json').read_text(encoding='utf-8'))
        rows = data['instructions']
        self.assertEqual(250, len(rows))
        self.assertEqual(len(rows), len({r['mnemonic'] for r in rows}))
        self.assertEqual(16, sum(r['current_encoder']['status'] != 'unsupported' for r in rows))
        evidence = (ROOT/'docs/evidence.md').read_text(encoding='utf-8')
        anchors = set(re.findall(r'<a id="([a-z0-9_]+)"></a>', evidence))
        ids = {r['id'] for r in data['qualification_reports']}
        for report in data['qualification_reports']:
            self.assertIn(report['evidence'].split('#')[1], anchors)
        used = {e for r in rows for k in ('hardware_evidence_baseline', 'qualification') for e in r[k]['evidence']}
        self.assertTrue(used <= ids, used - ids)  # every cited evidence id is a known report
