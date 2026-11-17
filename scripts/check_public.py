#!/usr/bin/env python3
"""Refuse to publish private data. Scans git-tracked + staged files (or given paths).

Generic rules live here. User-specific words (site names, internal hosts, e-mail, client names)
must NOT be written into this public repo — put them, one regex per line, in a private file:
  $FX3U_PRIVATE_PATTERNS  or  ~/.config/fx3u-toolkit/private-patterns.txt
Also scans commit metadata (author/committer names, e-mails, messages).
Reviewed public identity lines in .public-scan-allowlist.json can waive only
exact private-pattern matches; generic rules and commit messages stay enforced.
Exit 0 = clean, 1 = findings, 3 = incomplete audit/configuration (including a missing
own Git checkout, unavailable Git or private patterns). CI may pass --allow-missing-private, where only
the generic rules can run; a source archive still cannot audit Git metadata.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path

MAX_BYTES = 512 * 1024
BINARY_EXT = {'.bin', '.gxw', '.gx2', '.gx3', '.gpj', '.gppw', '.gps', '.dat', '.zip', '.7z', '.pdf',
              '.csv', '.jsonl'}  # GX exports (CSV) and wire logs (JSONL) carry real programs/sessions
BINARY_OK_DIRS = ('tests/fixtures/',)
JSON_ALLOWED = ('src/fx3u/capabilities.json', 'src/fx3u/experimental_profile.json', 'examples/')  # any other .json may be a captured result
GENERIC = [
    (r'[A-Za-z]:\\Users\\[^\\\s"\']+', 'Windows user path'),
    (r'/home/(?!runner\b|user\b|<)[a-z_][a-z0-9_-]*/', 'Linux home path'),
    (r'/mnt/[a-z]/Users/', 'WSL Windows path'),
    (r'\\\\wsl(\$|\.localhost)\\', 'WSL UNC path'),
    (r'[A-Za-z0-9._%+-]+@(?!example\.(com|org)\b|users\.noreply\.github\.com\b)[A-Za-z0-9.-]+\.[A-Za-z]{2,}', 'e-mail address'),
    (r'\b(10\.\d{1,3}|192\.168|172\.(1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b', 'private IP address'),
    (r'(?i)\b(password|passwd|api[_-]?key|secret|token)\s*[:=]\s*["\'][^"\']{6,}["\']', 'credential literal'),
    (r'(?i)^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*)?(?:User|Assistant|Human|Użytkownik|Asystent)(?:\*\*)?[ \t]*:',
     'conversation role header'),
    (r'(?i)^[ \t]*#{1,6}[ \t]+(?:User|Assistant|Human|Użytkownik|Asystent)[ \t]*$',
     'conversation role header'),
    (r'(?i)^[ \t]*(?:#{1,6}[ \t]+)?(?:\*\*)?(?:Original[ \t]+user[ \t]+prompt|(?:Original|Raw)[ \t]+(?:prompt|conversation|transcript)|Oryginaln[ay][ \t]+(?:prompt|rozmowa|wiadomość))\b',
     'conversation or prompt heading'),
    (r'<\|(?:im_start|im_end|start_header_id|end_header_id|eot_id|user|assistant|system|developer)\|>|\[/?INST\]|<</?SYS>>',
     'conversation serialization marker'),
    (r'(?i)^[ \t]*(?:[>*_#-][ \t]*)*(?:🤖[ \t]*)?(?:Generated|Written|Created)[ \t]+(?:with|by|using)[ \t]+\[?(?:ChatGPT|Claude(?:[ \t]+Code)?|Codex|GitHub[ \t]+Copilot)[ \t]*(?:\]|\(|[.!]?$)',
     'automated assistant signature'),
    (r'(?i)^[ \t]*Co-authored-by:[ \t]*(?:ChatGPT|Claude(?:[ \t]+Code)?|Codex|GitHub[ \t]+Copilot)[ \t]*<',
     'automated assistant attribution'),
    # Pair a chat role with its payload; a standalone role field is not enough.
    (r'''(?is)["']role["']\s*:\s*["'](?:user|assistant|human|system|developer)["'][^{}]*?["'](?:content|parts)["']\s*:''',
     'serialized conversation'),
    (r'''(?is)["'](?:content|parts)["']\s*:\s*(?:"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|null|None)\s*,\s*["']role["']\s*:\s*["'](?:user|assistant|human|system|developer)["']''',
     'serialized conversation'),
    (r'''(?im)^[ \t]*-?[ \t]*role\s*:\s*["']?(?:user|assistant|human|system|developer)["']?[ \t]*\r?\n[ \t]*(?:content|parts)\s*:''',
     'serialized conversation'),
]
SELF = {'scripts/check_public.py', 'tests/test_check_public.py'}
# These existing two files contain detector expressions and synthetic transcript
# fixtures. Their exception is limited to that grammar, never personal data,
# credentials or the owner's private patterns.
SELF_SYNTAX_RULES = frozenset({
    'conversation role header', 'conversation or prompt heading',
    'conversation serialization marker', 'automated assistant signature',
    'automated assistant attribution', 'serialized conversation',
})
TEXT_SPANNING = frozenset({'serialized conversation'})
ALLOWLIST_FILE = '.public-scan-allowlist.json'
IDENTITY_PATHS = {'README.md', 'README.pl.md', 'LICENSE', 'pyproject.toml',
                  'git:author_name', 'git:author_email',
                  'git:committer_name', 'git:committer_email'}


def line_digest(line):
    """Hash the full UTF-8 line without its line terminator; preserve spaces."""
    return hashlib.sha256(line.encode('utf-8')).hexdigest()


def load_allowlist(root):
    """Load reviewed identity lines; malformed configuration is a refusal."""
    path = root / ALLOWLIST_FILE
    if path.is_symlink():
        raise ValueError('Publication allow-list must not be a symlink')
    if not path.exists():
        return frozenset()
    if not path.is_file() or path.stat().st_size > MAX_BYTES:
        raise ValueError('Invalid publication allow-list file')
    data = json.loads(path.read_text(encoding='utf-8'))
    if (not isinstance(data, dict) or set(data) != {'version', 'entries'}
            or type(data['version']) is not int or data['version'] != 1
            or not isinstance(data['entries'], list)):
        raise ValueError('Invalid publication allow-list schema')
    result = set()
    for entry in data['entries']:
        if (not isinstance(entry, dict)
                or set(entry) != {'path', 'line_sha256', 'reason'}
                or not isinstance(entry['path'], str)
                or entry['path'] not in IDENTITY_PATHS
                or not isinstance(entry['line_sha256'], str)
                or not re.fullmatch(r'[0-9a-f]{64}', entry['line_sha256'])
                or not isinstance(entry['reason'], str) or not entry['reason'].strip()):
            raise ValueError('Invalid publication allow-list entry')
        key = (entry['path'], entry['line_sha256'])
        if key in result:
            raise ValueError('Duplicate publication allow-list entry')
        result.add(key)
    return frozenset(result)


def allowed_identity(path, line, why, allowances):
    return why == 'private pattern' and (path, line_digest(line)) in allowances


def private_patterns():
    path = Path(os.environ.get('FX3U_PRIVATE_PATTERNS', Path.home()/'.config/fx3u-toolkit/private-patterns.txt'))
    if not path.is_file():
        return [], path
    rows = [l.strip() for l in path.read_text(encoding='utf-8').splitlines()]
    return [(r, 'private pattern') for r in rows if r and not r.startswith('#')], path


def repository_root():
    """Require this scanner's own checkout, never an enclosing repository.

    A source archive unpacked inside another project must not silently audit the
    parent's files and metadata. Git's root discovery alone would allow that.
    """
    expected = Path(__file__).resolve().parents[1]
    result = subprocess.run(['git', '-C', str(expected), 'rev-parse', '--show-toplevel'],
                            capture_output=True, text=True, check=True)
    root = Path(result.stdout.strip()).resolve()
    if root != expected:
        raise ValueError('Scanner source is not at its own Git checkout root')
    return root


def tracked_files(root):
    out = subprocess.run(['git', '-C', str(root), 'ls-files', '-z', '--cached', '--others', '--exclude-standard'],
                         capture_output=True, check=True).stdout
    return [p for p in out.decode().split('\0') if p]


def scan(root, files, rules):
    findings = []
    allowances = load_allowlist(root)
    for rel in files:
        path = root/rel
        if not path.is_file():
            continue
        findings.extend(scan_blob(rel, path.read_bytes(), rules, allowances))
    return findings


def scan_blob(rel, data, rules, allowances=frozenset()):
    """Apply the publication policy to bytes with their original repository path."""
    findings = []
    suffix = Path(rel).suffix.lower()
    if len(data) > MAX_BYTES:
        findings.append((rel, 0, f'file larger than {MAX_BYTES} bytes ({len(data)})'))
    if suffix in BINARY_EXT and not rel.startswith(BINARY_OK_DIRS):
        return findings + [(rel, 0, f'binary/project file type {suffix} outside tests/fixtures')]
    if (suffix == '.json' and rel != ALLOWLIST_FILE
            and not rel.startswith(JSON_ALLOWED + BINARY_OK_DIRS)):
        findings.append((rel, 0, '.json outside the allow-list (captured results/manifests stay private)'))
    try:
        text = data.decode('utf-8')
    except UnicodeDecodeError:
        if not rel.startswith(BINARY_OK_DIRS):
            findings.append((rel, 0, 'non-UTF-8 file outside tests/fixtures'))
        return findings
    findings.extend(scan_text(rel, text, rules, allowances))
    return findings


def scan_text(rel, text, rules, allowances=frozenset()):
    """Report locations/rules only; transcript payloads can span physical lines.

    Line-based private matching and exact identity exemptions retain their
    previous behavior. Only paired serialized-message rules span lines, and
    generic findings cannot use the public-identity exemption.
    """
    findings = []
    compiled = [(re.compile(r), why) for r, why in rules]
    lines = text.split('\n')
    for no, line in enumerate(lines, 1):
        if no < len(lines) and line.endswith('\r'):
            line = line[:-1]
        for rx, why in compiled:
            if rel in SELF and why in SELF_SYNTAX_RULES:
                continue
            if why in TEXT_SPANNING:
                continue
            if rx.search(line) and not allowed_identity(rel, line, why, allowances):
                findings.append((rel, no, why))
    for rx, why in compiled:
        if why not in TEXT_SPANNING or (rel in SELF and why in SELF_SYNTAX_RULES):
            continue
        for match in rx.finditer(text):
            findings.append((rel, text.count('\n', 0, match.start()) + 1, why))
    if (rel not in SELF and any(why in TEXT_SPANNING for _, why in compiled)
            and not any(why in TEXT_SPANNING for _, _, why in findings)
            and structured_conversation(text)):
        # JSON parsing identifies a message object, not its exact source span.
        findings.append((rel, 0, 'serialized conversation'))
    findings.sort(key=lambda row: row[1])
    return findings


def structured_conversation(text):
    """Recognize nested message exports when a whole document is valid JSON.

    A role alone is insufficient. Exported author.role plus a content payload
    is also a message record. Non-JSON text is handled by the explicit patterns.
    """
    if not re.search(r'(?i)"role"\s*:\s*"(?:user|assistant|human|system|developer)"', text):
        return False
    try:
        value = json.loads(text.lstrip('\ufeff'))
    except ValueError:
        return False
    except RecursionError as exc:
        raise ValueError('Structured transcript inspection exceeded nesting limit') from exc
    pending = [value]
    roles = ('user', 'assistant', 'human', 'system', 'developer')
    while pending:
        item = pending.pop()
        if isinstance(item, dict):
            role = item.get('role')
            author = item.get('author')
            if isinstance(author, dict):
                role = author.get('role', role)
            if (isinstance(role, str) and role.lower() in roles
                    and ('content' in item or 'parts' in item)):
                return True
            pending.extend(item.values())
        elif isinstance(item, list):
            pending.extend(item)
    return False


def scan_commits(root, rules):
    allowances = load_allowlist(root)
    # NUL framing preserves the distinction between identity fields and message.
    r = subprocess.run(['git', '-C', str(root), 'log', '--all', '-z',
                        '--format=%an%x00%ae%x00%cn%x00%ce%x00%B'],
                       capture_output=True, text=True)
    if r.returncode != 0:
        refs = subprocess.run(['git', '-C', str(root), 'show-ref', '--quiet'], capture_output=True)
        if refs.returncode == 1:  # No commits or other refs yet.
            return []
        raise ValueError('Unable to read commit metadata')
    if not r.stdout:
        return []
    fields = r.stdout.split('\0')
    if fields[-1] != '' or (len(fields) - 1) % 5:
        raise ValueError('Malformed commit metadata framing')
    fields.pop()
    names = ('author_name', 'author_email', 'committer_name', 'committer_email', 'message')
    findings = []
    compiled = [(re.compile(rx), why) for rx, why in rules]
    for start in range(0, len(fields), 5):
        for name, value in zip(names, fields[start:start+5]):
            if name != 'message' and ('\n' in value or '\r' in value):
                raise ValueError('Malformed commit identity field')
            # Identity approval covers the entire field, including any Unicode
            # separators. Only commit messages are scanned as multiple lines.
            lines = value.splitlines() if name == 'message' else [value]
            for line in lines:
                for rx, why in compiled:
                    if why in TEXT_SPANNING:
                        continue
                    if rx.search(line) and not allowed_identity('git:' + name, line, why, allowances):
                        findings.append((f'<commit #{start//5} {name}>', 0, why))
            if name == 'message':
                serialized_found = False
                for rx, why in compiled:
                    if why in TEXT_SPANNING and rx.search(value):
                        findings.append((f'<commit #{start//5} {name}>', 0, why))
                        serialized_found = True
                if (not serialized_found and any(why in TEXT_SPANNING for _, why in compiled)
                        and structured_conversation(value)):
                    findings.append((f'<commit #{start//5} {name}>', 0, 'serialized conversation'))
    return findings


def main(argv):
    allow_missing = '--allow-missing-private' in argv
    argv = [a for a in argv if a != '--allow-missing-private']
    try:
        root = repository_root()
        priv, priv_path = private_patterns()
        if not priv and not allow_missing:
            print(f'check_public: private pattern file missing or empty: {priv_path} (fail-closed)', file=sys.stderr)
            return 3
        files = argv or tracked_files(root)
        findings = scan(root, files, GENERIC + priv) + scan_commits(root, GENERIC + priv)
    except (OSError, ValueError, re.error, subprocess.SubprocessError):
        # Exception text and Git stderr can contain private paths or metadata.
        print('check_public: incomplete audit; requires this source tree\'s Git checkout, '
              'available Git, and valid scan configuration/metadata', file=sys.stderr)
        return 3
    for rel, no, why in findings:
        print(f'{rel}:{no}: {why}')  # never echo the matched text: it may be the secret itself
    note = f'{len(priv)} private patterns from {priv_path}' if priv else f'NO private patterns ({priv_path} missing)'
    print(f'check_public: {len(files)} files, {len(findings)} findings, {note}', file=sys.stderr)
    return 1 if findings else 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
