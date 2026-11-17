# Validation record

This record summarizes reproducible automated checks, recorded physical-PLC
evidence, and the limits of release validation. The project remains experimental.

## Review-archive follow-up — 2026-11-16

The independent dev3 review gave a conditional portfolio PASS with no new P1.
It found two Git-dependent test failures when running the original source
archive without `.git`, and noted partial generator outputs on validation errors.
The following results cover the packaging follow-up; core library files and the
dev3 wheel remain byte-identical to the reviewed version.

| Check | Recorded result |
|---|---|
| Full suite from a fresh source copy without Git metadata, Python 3.12.13 | 1,012 passed, 6 skipped (101.98 s); PYTHONPATH=src; actual checkout audit explicitly skipped in addition to 5 optional-fixture skips |
| Scanner regressions in actual checkout | 24 passed; missing or foreign Git checkout, unavailable Git and failed listing refused with exit 3 and no traceback |
| Generator regressions | 5 passed; validation failure in station 2/3 preserves existing destination bytes or creates no new output directory; all 12 successful artifacts unchanged |
| Independent follow-up review | PASS, no new P1/P2; 22 passed in checkout and 21 passed/1 skipped without Git metadata |
| Current source privacy scan | 90 files, 0 findings with all 12 private patterns and generic rules |
| Core wheel equivalence | All 18 package files still match source; original dev3 wheel SHA-256 retained below |

The source-archive suite still needs the Git executable for synthetic fixtures;
those fixtures do not attest to real repository history. Public/history scanners
require an actual checkout. Without it, `check_public.py` returns exit 3 as an
incomplete audit. Do not substitute `git init` for an audit of the actual history.
See [reproducing archive checks](docs/review-package.md).

Only the validation record, source manifest and package instructions were
finalized after the source-copy suite; packaged executable code and tests are
checked byte-for-byte against the tested copy. The original reviewed archive
is preserved separately; the corrected archive has its own name and checksum.

## Local validation before the archive follow-up — 2026-11-16

These results apply to the local `0.1.0.dev3` source changes based on `a013b4a`.
They do not identify a new published commit or transfer remote CI status to it.

| Check | Recorded result |
|---|---|
| Full host suite, Python 3.12.13 | 1,004 passed, 5 optional-fixture skips (261.38 s); direct source import with PYTHONPATH=src |
| Privacy scan on source and commit metadata | 88 files, 0 findings with all 12 owner-specific patterns, in addition to generic rules |
| Public identity exceptions | Exact reviewed file/field and full-line SHA-256 only; generic rules and commit messages remain enforced |
| Wheel build and isolated installation | PASS for 0.1.0.dev3 with pyserial 3.5; all 18 installed package files match source; strict CSV step guard, Quick Start and deployment dry-run checked offline |
| Offline Quick Start | Compile, disassemble, xref and byte-identical recompile |
| Lighting example | Three corrected stations; 1,585 instructions and 5,954 bytes each; legacy readback evidence retained separately |
| Remote CI for reviewed source 293f606 | Run 43748295132, 2026-11-15: host Python 3.10 and 3.12 both success; each reports 481 passed, 5 skipped, and 75 subtests passed |
| Remote CI for reviewed source a013b4a | Run 43915077264, 2026-11-16: host Python 3.10 and 3.12 success; recorded before these dev3 changes |
| Independent change review | No new P1/P2; CSV, full STOP verification, timing and recovery inspected; 97 targeted regressions plus the new pre-connection test passed independently |
| CSV source-step regression | Gaps, nonzero start and wrong instruction widths refused before output; valid continuations, multiword instructions, NOP and supported labels accepted |
| Deployment timing regression | Response-data lower bounds, STOP request through confirmed RUN, continuing STOP and failed/recovery observations; synthetic host clock only |
| Reference gesture simulation | All four gestures on all 24 inputs, silence/hold boundaries, long-after-short and repeated-edge limitation; virtual time only |
| Ambiguous new lighting mappings | OFF/TOGGLE sharing is refused in either generation order; no central priority implementation claimed |
| Historical-content audit | Separate check_history.py scans local reachable path/blob pairs; GitHub PR/cache/Actions retention is outside its scope |

The five optional external/private-fixture tests are not required by public CI.
The private patterns are not distributed. Without that file, the default scanner
refuses with exit 3; CI uses `--allow-missing-private` to run generic checks.
That generic-only check does not replace the owner's complete local scan.

The earlier dev2 host record was 976 passed, 5 skipped and 75 reported subtests
on 2026-11-15; it remains historical evidence. The dev3 wheel SHA-256 is
`3dcad5be76c84fbc7bea42085c863fe02d0aa1bf6aa1da3d41eb5da04841c2f9`.

The publication allow-list records hashes of deliberately public identity lines,
not a blanket exemption for names. Changed lines, other contexts, commit
messages, generic secret/path rules and malformed scan configuration remain
subject to refusal. Regression tests cover those boundaries, including Unicode
line separators in identity fields and source files.

Current source and commit metadata pass with all 12 owner patterns. Historical
audit results apply only to the refs and objects actually inspected. Before
publication, run both scanners on the intended publication checkout; a clean
local snapshot does not clear historical GitHub PR content in another repository.
See [publication checks](docs/publication-audit.md) for remaining requirements.

The remote CI records above belong to their respective reviewed source commits. A new
publication root must run its own CI; source-equivalence checks do not transfer
the GitHub Actions status between commits or repositories.

## Hardware evidence

The [bench record](docs/bench-qualification-20261114.md) describes selected RUN
writes and a small DSL-to-CPU trial on one observed FX3U CPU/profile. It is not
an exhaustive qualification of addresses, physical circuits or PLC families.
The later recovery/error-path changes have host fault-injection coverage;
neither the new STOP verification nor leave-stopped paths were tested on a physical PLC.
The current timing changes were tested with a synthetic host clock. Full-image
response data alone requires at least 12.7 s (6,096 bytes) or 66.7 s (32,000 bytes)
at 9600 baud, 7E1, ASCII HEX; actual current STOP duration remains unmeasured.
The old 1.84 s observation predates this path. See
[deployment timing and bench measurement](docs/deployment-timing.md).

The historical `legacy-reference.il` was compared against the owner's private
readback during the recorded campaign. It contains the known click-loss defect.
The corrected active `program.il` has only host/scan-model evidence and was not
deployed during this follow-up. The old readback does not qualify the changed program.

Corrected active image digest, identical for PLC1/PLC2/PLC3 (offline only):

```text
SHA256 499f966aef619067b1ce6aaed78ee74eb392bc519d92442119c67d7f5429a38c
```

Historical legacy image digest: `ca59c2fafd692761234709d19ddf42faee3a26606e8e46f2bef4b17028b930b2`.

The current changes fix fresh-edge priority and invalid zone indices, require
strict CSV compilation and explicit padding, and verify complete bytes in
confirmed STOP before RUN. Physical debounce, restart/retention, installation
identity and a production recovery/approval workflow remain unqualified.
CSV compilation and the example generator additionally compare all source steps
with the selected encoder map; [CSV boundaries](docs/csv-import.md) document the
complete-program contract and unsupported export layouts.
Use the [change acceptance record](docs/change-acceptance.md) for each review.

## Reproduce

```sh
python -m pip install -e ".[dev]"
python -m pytest -q
python scripts/check_public.py --allow-missing-private
python examples/lighting-case-study/generate.py --out /tmp/lighting-validation
```

Use the default scanner with configured private patterns before publishing.
`MANIFEST.sha256` records the versioned source/document file hashes, excluding
itself and Git metadata. Encoding-profile provenance is documented in
[provenance](docs/provenance.md); license and third-party acknowledgements are
preserved in LICENSE and NOTICE.
