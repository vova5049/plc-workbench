"""The CLI must not crash on consoles/pipes whose encoding cannot represent catalog symbols.

On Windows, stdout redirected from another process is often a legacy code page (e.g. cp1250).
JSON printed by the CLI therefore has to be ASCII-safe (\\uXXXX escapes) and still parse.
"""

import json
import os
import subprocess
import sys

import pytest

CODE = "import sys; from fx3u.cli import main; sys.argv = ['fx3u'] + sys.argv[1:]; main()"


def run_cli(*args):
    env = {k: v for k, v in os.environ.items() if k not in ("PYTHONUTF8", "PYTHONIOENCODING")}
    env["PYTHONIOENCODING"] = "cp1250"
    return subprocess.run(
        [sys.executable, "-c", CODE, *args],
        env=env,
        capture_output=True,
        timeout=120,
    )


@pytest.mark.parametrize(
    "args",
    [
        ("catalog", "--experimental"),
        ("catalog", "--mnemonic", "ADPRW", "--experimental"),
        ("catalog",),
        ("catalog", "--family", "Program Flow", "--supported", "--experimental"),
    ],
)
def test_catalog_json_survives_legacy_console_encoding(args):
    result = run_cli(*args)
    assert result.returncode == 0, result.stderr.decode("ascii", "replace")
    result.stdout.decode("ascii")  # strict: no raw non-ASCII bytes on stdout
    rows = json.loads(result.stdout)
    assert rows and all("mnemonic" in row for row in rows)
    # The escaped symbol decodes back to the original text for the consumer.
    assert any(row.get("fx3u_mark", "").startswith("○") for row in rows)
