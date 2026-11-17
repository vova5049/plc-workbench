#!/usr/bin/env python3
"""Read-only publication audit of every file version reachable from local refs.

Does not fetch refs, modify history, or inspect remote PR caches/Actions logs.
Uses the current reviewed identity allow-list, never historical allow-lists.
Exit codes match check_public: 0 clean, 1 findings, 3 incomplete/configuration.
"""
import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

from check_public import (GENERIC, MAX_BYTES, load_allowlist, private_patterns,
                          scan_blob, scan_commits)


def git(root, *args):
    result = subprocess.run(['git', '-C', str(root), *args], capture_output=True)
    if result.returncode:
        # Git stderr may itself include private paths or metadata.
        raise ValueError('Unable to read local Git objects')
    return result.stdout


def audit_history(root, rules):
    if git(root, 'rev-parse', '--is-shallow-repository').strip() != b'false':
        raise ValueError('Shallow history cannot be audited completely')
    commits = git(root, 'rev-list', '--all', 'HEAD').decode('ascii').splitlines()
    allowances = load_allowlist(root)
    seen = set()
    findings = []
    for commit in commits:
        for entry in git(root, 'ls-tree', '-r', '-l', '-z', commit).split(b'\0'):
            if not entry:
                continue
            header, raw_path = entry.split(b'\t', 1)
            mode, kind, oid, size = header.split()
            rel = raw_path.decode('utf-8')
            key = (rel, oid)
            if key in seen:
                continue
            seen.add(key)
            if kind != b'blob' or mode not in (b'100644', b'100755'):
                rows = [(rel, 0, 'unsupported Git entry; manual review required')]
            elif int(size) > MAX_BYTES:
                rows = [(rel, 0, 'historical file exceeds scan size limit; manual review required')]
            else:
                data = git(root, 'cat-file', 'blob', oid.decode('ascii'))
                if len(data) != int(size):
                    raise ValueError('Historical blob size mismatch')
                rows = scan_blob(rel, data, rules, allowances)
            findings.extend((commit, path, line, why) for path, line, why in rows)
    # Metadata uses all local refs too; content scanning above also includes HEAD.
    findings.extend(('', path, line, why) for path, line, why in scan_commits(root, rules))
    return {'commits': len(commits), 'file_versions': len(seen), 'findings': findings}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-missing-private', action='store_true')
    args = parser.parse_args(argv)
    private, _ = private_patterns()
    if not private and not args.allow_missing_private:
        print('check_history: private patterns missing (fail-closed)', file=sys.stderr)
        return 3
    try:
        root = Path(git(Path.cwd(), 'rev-parse', '--show-toplevel').decode().strip())
        report = audit_history(root, GENERIC + private)
    except (OSError, ValueError, re.error):
        print('check_history: incomplete audit or invalid configuration', file=sys.stderr)
        return 3
    for commit, path, line, why in report['findings']:
        # Only locations and rule names; never print matched contents.
        print(json.dumps({'commit': commit, 'path': path, 'line': line, 'rule': why}))
    print(f"check_history: {report['commits']} commits, {report['file_versions']} file versions, "
          f"{len(report['findings'])} findings, {len(private)} private patterns; "
          'local reachable objects only', file=sys.stderr)
    return 1 if report['findings'] else 0


if __name__ == '__main__':
    sys.exit(main())
