import subprocess
import sys
import tempfile
import unittest
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'scripts'))
from check_public import GENERIC, repository_root, scan  # noqa: E402


class CheckPublicTests(unittest.TestCase):
    def run_scan(self, files, extra=()):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for rel, content in files.items():
                (root/rel).parent.mkdir(parents=True, exist_ok=True)
                (root/rel).write_bytes(content if isinstance(content, bytes) else content.encode())
            return {(rel, why) for rel, _, why in scan(root, list(files), GENERIC + list(extra))}

    def test_violations_found(self):
        found = self.run_scan({
            'a.md': 'see C:' + '\\Users\\someone\\x',
            'b.md': 'cd /home/' + 'alice/code',
            'c.md': 'mail alice@' + 'corp.invalid',
            'd.md': 'host 192.' + '168.1.20',
            'e.json': 'password = "' + 'hunter22"',
            'f.gxw': b'\x00\x01',
            'h.csv': 'Step No.\tInstruction',
            'i.jsonl': '{}',
            'result.json': '{"ok": true}',
            'g.md': 'Site Foo' + 'bar lights',
        }, extra=[('Foo' + 'bar', 'private pattern')])
        whys = {why for _, why in found}
        for why in ('Windows user path', 'Linux home path', 'e-mail address', 'private IP address',
                    'credential literal', 'binary/project file type .gxw outside tests/fixtures',
                    'binary/project file type .csv outside tests/fixtures',
                    'binary/project file type .jsonl outside tests/fixtures',
                    '.json outside the allow-list (captured results/manifests stay private)', 'private pattern'):
            self.assertIn(why, whys)

    def test_allowed(self):
        self.assertEqual(set(), self.run_scan({
            'a.md': 'mail dev@' + 'example.com; ~/code; /home/' + 'user/x; COM6',
            'tests/fixtures/x.bin': b'\x00\xff',
            'examples/fleet.example.json': '[]',
        }))

    def test_fail_closed_without_private_patterns(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            # A synthetic checkout tests the CLI boundary even from a tarball;
            # it is not an audit of the toolkit's actual history.
            subprocess.run(['git', 'init', '-q', str(root)], check=True, capture_output=True)
            script = self.copy_scanner(root)
            env = dict(os.environ, FX3U_PRIVATE_PATTERNS=str(root/'missing-patterns.txt'))
            r = subprocess.run([sys.executable, str(script)], cwd=root,
                               capture_output=True, text=True, env=env)
            self.assertEqual(3, r.returncode)
            self.assertIn('private pattern file missing or empty', r.stderr)
            self.assertNotIn('Traceback', r.stderr)

    def test_repository_is_clean(self):
        try:
            root = repository_root()
        except (OSError, ValueError, subprocess.SubprocessError):
            self.skipTest('Actual toolkit Git checkout required; source archive has no Git metadata')
        self.assertEqual(ROOT, root)
        r = subprocess.run([sys.executable, str(ROOT/'scripts/check_public.py'), '--allow-missing-private'], cwd=ROOT,
                           capture_output=True, text=True)
        self.assertEqual(0, r.returncode, r.stdout + r.stderr)

    @staticmethod
    def copy_scanner(root):
        script = root/'scripts/check_public.py'
        script.parent.mkdir(parents=True)
        shutil.copyfile(ROOT/'scripts/check_public.py', script)
        return script

    def test_source_archive_refuses_without_traceback(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = self.copy_scanner(root)
            r = subprocess.run([sys.executable, str(script), '--allow-missing-private'], cwd=root,
                               capture_output=True, text=True)
            self.assertEqual(3, r.returncode)
            self.assertEqual('', r.stdout)
            self.assertIn('incomplete audit', r.stderr)
            self.assertNotIn('Traceback', r.stderr)
            self.assertNotIn(str(root), r.stderr)

    def test_source_archive_does_not_audit_enclosing_repository(self):
        with tempfile.TemporaryDirectory() as tmp:
            parent = Path(tmp)
            subprocess.run(['git', 'init', '-q', str(parent)], check=True, capture_output=True)
            root = parent/'archive/source'
            script = self.copy_scanner(root)
            r = subprocess.run([sys.executable, str(script), '--allow-missing-private'], cwd=root,
                               capture_output=True, text=True)
            self.assertEqual(3, r.returncode)
            self.assertEqual('', r.stdout)
            self.assertIn('incomplete audit', r.stderr)
            self.assertNotIn('Traceback', r.stderr)
            self.assertNotIn(str(parent), r.stderr)

    def test_unavailable_git_returns_incomplete_without_traceback(self):
        import os
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            script = self.copy_scanner(root)
            empty = root/'empty-path'
            empty.mkdir()
            env = dict(os.environ, PATH=str(empty))
            r = subprocess.run([sys.executable, str(script), '--allow-missing-private'], cwd=root,
                               capture_output=True, text=True, env=env)
            self.assertEqual(3, r.returncode)
            self.assertEqual('', r.stdout)
            self.assertIn('incomplete audit', r.stderr)
            self.assertNotIn('Traceback', r.stderr)
            self.assertNotIn(str(root), r.stderr)

    def test_git_listing_failure_does_not_expose_diagnostics(self):
        import contextlib
        import io
        from unittest.mock import patch
        from check_public import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            error = subprocess.CalledProcessError(128, ['git'], stderr='sensitive diagnostic')
            stderr = io.StringIO()
            with patch('check_public.repository_root', return_value=root), \
                    patch('check_public.private_patterns', return_value=([], root/'missing')), \
                    patch('check_public.tracked_files', side_effect=error), \
                    contextlib.redirect_stderr(stderr):
                self.assertEqual(3, main(['--allow-missing-private']))
            self.assertIn('incomplete audit', stderr.getvalue())
            self.assertNotIn('sensitive diagnostic', stderr.getvalue())
            self.assertNotIn('Traceback', stderr.getvalue())


class PublicIdentityAllowlistTests(unittest.TestCase):
    """Only exact, reviewed identity lines may waive user-specific patterns."""

    @staticmethod
    def write_allowances(root, rows):
        import json
        from check_public import ALLOWLIST_FILE, line_digest
        entries = [{'path': path, 'line_sha256': line_digest(line),
                    'reason': 'Reviewed public identity field.'} for path, line in rows]
        (root/ALLOWLIST_FILE).write_text(json.dumps({'version': 1, 'entries': entries}))

    def scan_identity(self, files, rows):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for path, content in files.items():
                (root/path).parent.mkdir(parents=True, exist_ok=True)
                (root/path).write_text(content, encoding='utf-8')
            self.write_allowances(root, rows)
            return scan(root, list(files), GENERIC + [
                ('PublicHandle', 'private pattern'), ('InternalRoom', 'private pattern')])

    def test_exact_approved_line_and_terminator_only(self):
        line = 'Author: PublicHandle'
        for ending in ('\n', '\r\n'):
            with self.subTest(ending=repr(ending)):
                self.assertEqual([], self.scan_identity({'README.md': line+ending},
                                                       [('README.md', line)]))

    def test_polish_public_identity_requires_its_own_exact_approval(self):
        line = 'Maintainer: PublicHandle'
        self.assertEqual([], self.scan_identity({'README.pl.md': line}, [('README.pl.md', line)]))
        self.assertTrue(self.scan_identity({'README.pl.md': line + ' InternalRoom'},
                                          [('README.pl.md', line)]))
        self.assertTrue(self.scan_identity({'README.pl.md': line}, [('README.md', line)]))

    def test_changed_line_extra_data_and_wrong_context_remain_blocked(self):
        line = 'Author: PublicHandle'
        cases = [
            {'README.md': line+' changed'},
            {'README.md': line+' InternalRoom'},
            {'README.md': line+' '},
            {'README.md': line+'\u2028Extra context'},
            {'README.md': line+'\vExtra context'},
            {'README.md': line+'\nInternalRoom'},
            {'notes.md': line},
            {'LICENSE': line},
        ]
        for files in cases:
            with self.subTest(files=tuple(files)):
                findings = self.scan_identity(files, [('README.md', line)])
                self.assertTrue(any(why == 'private pattern' for _, _, why in findings))

    def test_generic_rules_cannot_be_waived(self):
        lines = [
            ('Author: PublicHandle at 192.'+'168.1.20', 'private IP address'),
            ('Author: PublicHandle alice@'+'corp.invalid', 'e-mail address'),
            ('Author: PublicHandle /home/'+'alice/work', 'Linux home path'),
            ('PublicHandle password = "'+'hunter22"', 'credential literal'),
        ]
        for line, reason in lines:
            with self.subTest(reason=reason):
                found = self.scan_identity({'README.md': line}, [('README.md', line)])
                self.assertIn(reason, [why for _, _, why in found])

    def test_allowlist_json_is_the_only_new_json_exception(self):
        from check_public import ALLOWLIST_FILE
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_allowances(root, [])
            (root/'unapproved.json').write_text('{}')
            (root/(ALLOWLIST_FILE+'.copy.json')).write_text('{}')
            findings = scan(root, [ALLOWLIST_FILE, 'unapproved.json',
                                   ALLOWLIST_FILE+'.copy.json'], GENERIC)
            paths = {path for path, _, _ in findings}
            self.assertNotIn(ALLOWLIST_FILE, paths)
            self.assertEqual({'unapproved.json', ALLOWLIST_FILE+'.copy.json'}, paths)

    def test_invalid_or_expanded_configuration_fails_closed(self):
        import json
        from check_public import ALLOWLIST_FILE, line_digest, load_allowlist
        valid = {'path': 'README.md', 'line_sha256': line_digest('Author: PublicHandle'),
                 'reason': 'Reviewed identity.'}
        invalid = [
            {'version': True, 'entries': [valid]},
            {'version': 1, 'entries': [valid], 'ignore_generic': True},
            {'version': 1, 'entries': [dict(valid, path='git:message')]},
            {'version': 1, 'entries': [dict(valid, path='notes.md')]},
            {'version': 1, 'entries': [dict(valid, path='tests/fixtures/example.bin')]},
            {'version': 1, 'entries': [dict(valid, line_sha256='bad')]},
            {'version': 1, 'entries': [dict(valid, reason=' ')]},
            {'version': 1, 'entries': [dict(valid, rule='generic')]},
            {'version': 1, 'entries': [valid, valid]},
        ]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for document in invalid:
                (root/ALLOWLIST_FILE).write_text(json.dumps(document))
                with self.subTest(document=document), self.assertRaises(ValueError):
                    load_allowlist(root)
            (root/ALLOWLIST_FILE).write_text('{broken')
            with self.assertRaises(ValueError):
                load_allowlist(root)

    def test_invalid_configuration_returns_cli_failure(self):
        import contextlib
        import io
        from unittest.mock import patch
        from check_public import ALLOWLIST_FILE, main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root/ALLOWLIST_FILE).write_text('{broken')
            with patch('check_public.repository_root', return_value=root), \
                    patch('check_public.private_patterns', return_value=(
                        [('PublicHandle', 'private pattern')], root/'private-rules')), \
                    contextlib.redirect_stderr(io.StringIO()):
                self.assertEqual(3, main(['README.md']))

    def scan_metadata(self, fields, *, malformed=None):
        from subprocess import CompletedProcess
        from unittest.mock import patch
        from check_public import scan_commits
        names = ('author_name', 'author_email', 'committer_name', 'committer_email')
        approved = ('PublicHandle', 'PublicHandle@users.noreply.github.com',
                    'PublicHandle', 'PublicHandle@users.noreply.github.com')
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_allowances(root, [('git:'+key, value)
                                         for key, value in zip(names, approved)])
            output = '\0'.join(fields)+'\0' if malformed is None else malformed
            result = CompletedProcess([], 0, stdout=output, stderr='')
            with patch('check_public.subprocess.run', return_value=result):
                return scan_commits(root, GENERIC + [('PublicHandle', 'private pattern')])

    def test_commit_identity_fields_are_separate_from_message(self):
        fields = ['PublicHandle', 'PublicHandle@users.noreply.github.com',
                  'PublicHandle', 'PublicHandle@users.noreply.github.com', 'Release source\n']
        self.assertEqual([], self.scan_metadata(fields))
        changed = fields.copy(); changed[0] += ' changed'
        self.assertTrue(self.scan_metadata(changed))
        changed = fields.copy(); changed[-1] = 'PublicHandle\n'
        findings = self.scan_metadata(changed)
        self.assertEqual(1, len(findings))
        self.assertIn('message', findings[0][0])
        changed[-1] = 'Author: PublicHandle\nMore context\n'
        self.assertTrue(self.scan_metadata(changed))
        for separator in ('\u2028', '\v'):
            changed = fields.copy(); changed[0] += separator + 'Extra context'
            with self.subTest(separator=repr(separator)):
                self.assertTrue(self.scan_metadata(changed))

    def test_commit_generic_rule_and_bad_framing_are_not_waived(self):
        from subprocess import CompletedProcess
        from unittest.mock import patch
        from check_public import scan_commits
        fields = ['PublicHandle', 'PublicHandle@'+'corp.invalid',
                  'PublicHandle', 'PublicHandle@users.noreply.github.com', 'Release source\n']
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            self.write_allowances(root, [('git:author_email', fields[1])])
            with patch('check_public.subprocess.run', return_value=CompletedProcess(
                    [], 0, stdout='\0'.join(fields)+'\0', stderr='')):
                self.assertIn('e-mail address', [why for _, _, why in scan_commits(root, GENERIC)])
        for malformed in ('name\0email\0', 'missing terminator'):
            with self.subTest(malformed=malformed), self.assertRaises(ValueError):
                self.scan_metadata(fields, malformed=malformed)
        fields[0] = 'PublicHandle\nUnexpected context'
        with self.assertRaises(ValueError):
            self.scan_metadata(fields)


class ConversationPrivacyTests(unittest.TestCase):
    """Synthetic boundary cases distinguish transcripts from technical wording."""

    @staticmethod
    def reasons(text, path='notes.md', extra=()):
        from check_public import scan_blob
        return {why for _, _, why in scan_blob(path, text.encode('utf-8'),
                                                GENERIC + list(extra))}

    def test_role_turns_are_refused(self):
        for text in ('User: Fixture question\nAssistant: Fixture response',
                     'human: Fixture question', '## Assistant',
                     '# **User**: Fixture question',
                     'Użytkownik: Pytanie testowe', 'Asystent: Odpowiedź testowa'):
            with self.subTest(text=text):
                self.assertIn('conversation role header', self.reasons(text))

    def test_raw_prompt_headings_are_refused(self):
        for text in ('# Original user prompt', 'Original prompt: fixture',
                     '## Raw conversation', 'Original transcript',
                     'Oryginalny prompt: dane testowe'):
            with self.subTest(text=text):
                self.assertIn('conversation or prompt heading', self.reasons(text))

    def test_serialization_markers_are_refused(self):
        for marker in ('<|im_start|>', '<|im_end|>', '<|start_header_id|>',
                       '<|end_header_id|>', '<|eot_id|>', '<|assistant|>',
                       '[INST]', '[/INST]', '<<SYS>>', '<</SYS>>'):
            with self.subTest(marker=marker):
                self.assertIn('conversation serialization marker', self.reasons(marker))

    def test_json_repr_and_yaml_payload_pairs_can_span_lines(self):
        payloads = [
            '{"role": "user", "content": "fixture"}',
            '{\n  "role": "assistant",\n  "name": "fixture",\n  "content": []\n}',
            "{'role': 'human',\n 'content': 'fixture'}",
            '{"content": "fixture",\n "role": "user"}',
            '{"content": null,\n "role": "assistant"}',
            '- role: user\n  content: fixture',
            'role: "assistant"\ncontent: fixture',
            '{\r\n"role": "user",\r\n"parts": ["fixture"]\r\n}',
        ]
        for text in payloads:
            with self.subTest(text=text):
                self.assertIn('serialized conversation', self.reasons(text))

    def test_nested_author_exports_and_reverse_complex_payloads_are_refused(self):
        for text in (
                '{"mapping": {"fixture": {"message": {"author": {"role": "user", "metadata": {}}, '
                '"content": {"content_type": "text", "parts": ["FixturePayload"]}}}}}',
                '{"content": [{"type": "text", "text": "FixturePayload"}], "role": "assistant"}',
                '\ufeff{"author": {"role": "user"}, "content": {"parts": ["FixturePayload"]}}'):
            with self.subTest(text=text):
                self.assertIn('serialized conversation', self.reasons(text))

    def test_auto_signatures_and_bot_attribution_are_refused(self):
        for text in ('Generated with ChatGPT', '🤖 Generated with [Claude Code](https://example.com)',
                     'Created using Codex.', '> Generated by GitHub Copilot'):
            with self.subTest(text=text):
                self.assertIn('automated assistant signature', self.reasons(text))
        self.assertIn('automated assistant attribution', self.reasons(
            'Co-authored-by: Codex <bot@example.com>'))

    def test_technical_words_human_credits_and_nonchat_roles_remain_allowed(self):
        for text in ('AI agents can inspect this DSL.',
                     'ChatGPT, Claude and Codex are examples of development tools.',
                     'A prompt is not a PLC acceptance test.',
                     'User manual: address mapping and recovery procedure.',
                     'The Assistant: field name is documented inline.',
                     'Human',
                     '{"role": "operator", "content": "display label"}',
                     '{"role": "user", "permissions": ["read"]}',
                     '{"content": "fixture"}',
                     'Generated with Python',
                     'Written by Claude Nguyen',
                     'Co-authored-by: Example Developer <dev@example.com>',
                     'Co-authored-by: Claude Nguyen <dev@example.com>'):
            with self.subTest(text=text):
                self.assertEqual(set(), self.reasons(text))

    def test_synthetic_rule_fixtures_do_not_waive_private_patterns(self):
        # The existing two SELF paths exempt only synthetic transcript grammar.
        # Generic personal-data rules and owner rules still apply everywhere.
        for path in ('scripts/check_public.py', 'tests/test_check_public.py'):
            with self.subTest(path=path):
                self.assertEqual({'private pattern'}, self.reasons(
                    'User: SyntheticPrivateMarker', path,
                    [('SyntheticPrivateMarker', 'private pattern')]))
                self.assertIn('e-mail address', self.reasons('dev@' + 'private.invalid', path))
                self.assertIn('private IP address', self.reasons('192.' + '168.1.20', path))
        self.assertIn('conversation role header', self.reasons(
            'User: fixture', 'tests/test_other.py'))

    def test_multiline_findings_report_location_without_payload(self):
        from check_public import scan_blob
        rows = scan_blob('notes.md', b'heading\n{\n"role": "user",\n"content": "FixturePayload"\n}', GENERIC)
        self.assertEqual([('notes.md', 3, 'serialized conversation')], rows)
        self.assertNotIn('FixturePayload', repr(rows))

    def test_commit_message_transcripts_are_refused_without_payload(self):
        from subprocess import CompletedProcess
        from unittest.mock import patch
        from check_public import scan_commits
        for message, reason in (
                ('Update fixture\nUser: FixturePayload\n', 'conversation role header'),
                ('Update fixture\n{\n"role": "user",\n"content": "FixturePayload"\n}',
                 'serialized conversation'),
                ('Generated with Codex\n', 'automated assistant signature')):
            fields = ['Example Author', 'dev@example.com', 'Example Author',
                      'dev@example.com', message]
            with tempfile.TemporaryDirectory() as tmp:
                output = CompletedProcess([], 0, stdout='\0'.join(fields)+'\0', stderr='')
                with patch('check_public.subprocess.run', return_value=output):
                    rows = scan_commits(Path(tmp), GENERIC)
            with self.subTest(reason=reason):
                self.assertIn(reason, [why for _, _, why in rows])
                self.assertNotIn('FixturePayload', repr(rows))

    def test_removed_transcript_remains_detected_in_history(self):
        from check_history import audit_history
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)

            def git(*args):
                return subprocess.run(['git', '-C', str(root), *args], check=True,
                                      capture_output=True, text=True).stdout.strip()

            git('init', '-q', '-b', 'main')
            git('config', 'user.name', 'Example Author')
            git('config', 'user.email', 'dev@example.com')
            (root/'notes.md').write_text('User: FixturePayload\nAssistant: FixtureReply\n')
            git('add', '.')
            git('commit', '-m', 'Add synthetic fixture')
            old = git('rev-parse', 'HEAD')
            (root/'notes.md').write_text('Technical requirements\n')
            git('add', '.')
            git('commit', '-m', 'Replace synthetic fixture')
            report = audit_history(root, GENERIC)
            self.assertEqual(2, report['commits'])
            self.assertEqual([(old, 'notes.md', 1, 'conversation role header'),
                              (old, 'notes.md', 2, 'conversation role header')],
                             report['findings'])
