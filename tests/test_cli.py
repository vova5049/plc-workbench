import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def fx3u(*args, ok=True):
    r = subprocess.run([sys.executable, '-m', 'fx3u', *map(str, args)], capture_output=True, text=True, timeout=60)
    if ok and r.returncode != 0:
        raise AssertionError(r.stderr)
    return r


class CliTests(unittest.TestCase):
    def test_help_lists_commands(self):
        out = fx3u('--help').stdout
        for cmd in ('catalog', 'compile', 'disassemble', 'xref', 'compile-csv', 'fleet-watch', 'deploy'):
            self.assertIn(cmd, out)

    def test_catalog_from_package_data(self):
        rows = json.loads(fx3u('catalog', '--mnemonic', 'sftl').stdout)
        self.assertEqual(['SFTL'], [r['mnemonic'] for r in rows])
        self.assertEqual(2, fx3u('catalog', '--mnemonic', 'NOPE', ok=False).returncode)

    def test_examples_compile_disassemble_xref(self):
        with tempfile.TemporaryDirectory() as tmp:
            for src in sorted((ROOT/'examples').glob('*.il')):
                with self.subTest(src=src.name):
                    out = Path(tmp)/(src.stem+'.bin')
                    info = json.loads(fx3u('compile', src, out).stdout)
                    self.assertEqual(6096, out.stat().st_size)
                    back = Path(tmp)/(src.stem+'.il')
                    fx3u('disassemble', out, back)
                    self.assertEqual(src.read_text().split(), back.read_text().split())
                    self.assertEqual(info['instructions'], len(src.read_text().split('\n')) - 1)
                    # examples must never drive physical outputs
                    self.assertNotRegex(src.read_text(), r'\bY\d')
            xref = json.loads(fx3u('xref', Path(tmp)/'count_d0.bin', 'D0').stdout)
            self.assertTrue(xref['complete'])
            self.assertEqual(3, len({row['instruction_index'] for row in xref['hits']}))
            self.assertTrue(all('step' in row and 'line' in row for row in xref['hits']))

    def test_xref_extended_groups_and_index_uncertainty(self):
        with tempfile.TemporaryDirectory() as tmp:
            source, image = Path(tmp)/'p.il', Path(tmp)/'p.bin'
            source.write_text('MOV K4M0 D0\nMOV D0Z0 D100\nEND\n')
            fx3u('compile-experimental', source, image)
            result = json.loads(fx3u('xref', image, 'M15', '--experimental').stdout)
            self.assertFalse(result['complete'])
            self.assertEqual('group', result['hits'][0]['kind'])
            self.assertEqual(0, result['hits'][0]['step'])
            self.assertEqual(1, result['hits'][0]['line'])
            potential = json.loads(fx3u('xref', image, 'D500', '--experimental').stdout)
            self.assertEqual('potential', potential['hits'][0]['certainty'])
            self.assertTrue(potential['uncertainties'])
            self.assertNotEqual(0, fx3u('xref', image, 'Y8', '--experimental', ok=False).returncode)

    def test_deploy_without_execute_is_offline_dry_run(self):
        with tempfile.TemporaryDirectory() as tmp:
            img = Path(tmp)/'p.bin'
            fx3u('compile', ROOT/'examples/blink_m0.il', img)
            # Port name is bogus: a dry run must never try to open it.
            r = fx3u('deploy', '--port', 'COM999', '--wire-log', Path(tmp)/'w.jsonl', '--out-dir', Path(tmp)/'o',
                     '--expected-sha256', '0'*64, img)
            self.assertTrue(json.loads(r.stdout)['dry_run'])
            self.assertFalse((Path(tmp)/'w.jsonl').exists())
            short = Path(tmp)/'short.bin'
            short.write_bytes(img.read_bytes()[:100])
            r = fx3u('deploy', '--port', 'COM999', '--wire-log', Path(tmp)/'w2.jsonl', '--out-dir', Path(tmp)/'o2',
                     '--expected-sha256', '0'*64, short, ok=False)
            self.assertNotEqual(0, r.returncode)

    def test_live_commands_refuse_non_windows(self):
        if sys.platform == 'win32':
            self.skipTest('live transport is allowed on Windows')
        with tempfile.TemporaryDirectory() as tmp:
            r = fx3u('status', '--port', 'COM999', '--wire-log', Path(tmp)/'w.jsonl', ok=False)
            self.assertIn('qualified only with native Windows', r.stderr)

    def test_compile_csv_rejects_unsupported(self):
        from fx3u.dsl import write_utf16
        from fx3u.dsl.fx_dsl import demo_self_hold
        from fx3u.dsl.il_compiler import compile_program
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp)/'good.csv'
            write_utf16(str(good), compile_program([('LD', 'M0'), ('OUT', 'M1'), ('END',)], {}, 'MAIN')[0])
            info = json.loads(fx3u('compile-csv', good, Path(tmp)/'good.bin').stdout)
            self.assertEqual(3, info['instructions'])
            bad = Path(tmp)/'bad.csv'
            write_utf16(str(bad), demo_self_hold()[0])
            r = fx3u('compile-csv', bad, Path(tmp)/'bad.bin', ok=False)
            self.assertIn('Unsupported instruction', r.stderr)
            self.assertFalse((Path(tmp)/'bad.bin').exists())
