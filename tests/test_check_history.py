import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from check_history import audit_history
from check_public import GENERIC, line_digest


def git(root, *args):
    return subprocess.run(['git', '-C', str(root), *args], capture_output=True,
                          check=True).stdout.decode().strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, 'init', '-b', 'main')
    git(tmp_path, 'config', 'user.name', 'Example Author')
    git(tmp_path, 'config', 'user.email', 'dev@example.com')
    return tmp_path


def commit(root, text, name='notes.md'):
    (root / name).write_text(text)
    git(root, 'add', '.')
    git(root, 'commit', '-m', 'Update example')
    return git(root, 'rev-parse', 'HEAD')


def test_removed_private_content_is_still_detected(repo):
    old = commit(repo, 'InternalMarker')
    commit(repo, 'Public documentation')
    report = audit_history(repo, GENERIC + [('InternalMarker', 'private pattern')])
    assert report['commits'] == 2
    assert report['findings'] == [(old, 'notes.md', 1, 'private pattern')]
    assert (repo / 'notes.md').read_text() == 'Public documentation'


def test_other_local_branch_is_included(repo):
    commit(repo, 'Public documentation')
    git(repo, 'checkout', '-b', 'other')
    hidden = commit(repo, 'InternalMarker')
    git(repo, 'checkout', 'main')
    report = audit_history(repo, [('InternalMarker', 'private pattern')])
    assert report['findings'] == [(hidden, 'notes.md', 1, 'private pattern')]


def test_same_blob_under_another_path_cannot_reuse_identity_approval(repo):
    import json
    text = 'Author: PublicHandle'
    commit(repo, text, 'README.md')
    commit(repo, text, 'notes.md')
    (repo / '.public-scan-allowlist.json').write_text(json.dumps({'version': 1, 'entries': [
        {'path': 'README.md', 'line_sha256': line_digest(text), 'reason': 'Public identity.'}]}))
    report = audit_history(repo, [('PublicHandle', 'private pattern')])
    assert len(report['findings']) == 1
    assert report['findings'][0][1:] == ('notes.md', 1, 'private pattern')


def test_historical_policy_cannot_approve_private_content(repo):
    import json
    text = 'Author: InternalMarker'
    (repo / '.public-scan-allowlist.json').write_text(json.dumps({'version': 1, 'entries': [
        {'path': 'README.md', 'line_sha256': line_digest(text), 'reason': 'Untrusted old approval.'}]}))
    commit(repo, text, 'README.md')
    (repo / '.public-scan-allowlist.json').unlink()
    commit(repo, 'Public documentation', 'README.md')
    assert audit_history(repo, [('InternalMarker', 'private pattern')])['findings']


def test_shallow_repository_is_refused(repo, tmp_path):
    commit(repo, 'Public documentation')
    clone = tmp_path / 'shallow'
    git(repo, 'clone', '--depth=1', repo.as_uri(), str(clone))
    with pytest.raises(ValueError, match='Shallow'):
        audit_history(clone, GENERIC)


def test_cli_does_not_print_matched_contents(repo):
    import os
    private = repo / 'private-rules'
    private.write_text('InternalMarker')
    commit(repo, 'InternalMarker')
    result = subprocess.run([sys.executable, str(ROOT/'scripts/check_history.py')], cwd=repo,
                            env=dict(os.environ, FX3U_PRIVATE_PATTERNS=str(private)),
                            capture_output=True, text=True)
    assert result.returncode == 1
    assert 'private pattern' in result.stdout
    assert 'InternalMarker' not in result.stdout + result.stderr


def test_invalid_private_regex_refuses_without_printing_pattern(repo):
    import os
    commit(repo, 'Public documentation')
    private = repo / 'private-rules'
    private.write_text('(InternalMarker')
    result = subprocess.run([sys.executable, str(ROOT/'scripts/check_history.py')], cwd=repo,
                            env=dict(os.environ, FX3U_PRIVATE_PATTERNS=str(private)),
                            capture_output=True, text=True)
    assert result.returncode == 3
    assert 'InternalMarker' not in result.stdout + result.stderr
