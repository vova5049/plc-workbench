# Publication checks and remaining qualification

The toolkit remains experimental. Host checks and byte-identical images do not
qualify a physical installation or every FX3U instruction.

## API and device analysis

Bounded writes now require the execution policy again and reject an inconsistent
plan before any session I/O. Raw `Session` methods remain privileged transport;
see [live API boundaries](live-api.md). DSL declarations reject implicit symbol
retargeting and duplicate direct addresses; intentional aliases are explicit.
This is separate from instruction footprints and runtime indexing.

`xref` now reports structured direct/group/range references and uncertainty,
including extended-codec images selected with `--experimental`. Its JSON changed
from a list to an object with `hits`, `uncertainties`, and `complete`. Hits retain
PLC step and source-line locations. See [device analysis](device-usage.md).

## Lighting example

Offline tests exercise single/double/triple/long on all 24 reference inputs,
silence boundaries at 399/400/401 ms, hold boundaries at 1999/2000/2001 ms,
long after short, and absence of a delayed second command in those traces.
These use virtual scan time, not measured PLC timer ticks or input filtering.
The corrected images remain identical across the three local stations, but differ
from the historical PLC program. The unchanged `legacy-reference.il` preserves
that historical evidence. Regression tests cover every pair of successive gestures
on all 24 inputs, including a new press in the scan immediately after finalization.
Every zone index is validated as a non-boolean integer in range before program generation.

The generator refuses a zone shared between OFF and TOGGLE ownership, in either
generation order. It does not implement a central command arbiter. Such new
maps need an agreed priority and a new behavior qualification before support.
The current empty double/long maps are unaffected.

A short repeated-edge trace is classified as double: there is no software
debounce in the reference logic. Physical input-filter settings, a required
stable interval, behavior when starting with a held button, zone retention,
STOP/RUN, and power-loss recovery still need agreed requirements and PLC tests.
The scan model is not a restart/power-cycle emulator. Diagnostic D100/D101 are
an event counter and latest code, not a lossless event queue.

## Local Git audit versus GitHub retention

Run both checks with the owner's private patterns configured:

```sh
python scripts/check_public.py
python scripts/check_history.py
```

The first checks current files and local commit metadata. The second examines
every unique path/blob pair reachable from all local refs and HEAD, plus commit
metadata. It refuses shallow history and flags unsupported entries or files
above its size limit for manual review. It uses the current reviewed identity
allow-list, never approvals from an older revision. Reports contain locations
and rule names, not matched contents. Exit 1 requires human classification;
exit 3 means the audit could not complete or private patterns are missing.

Neither tool fetches remote refs, changes history, inspects unreachable objects,
downloads Actions logs, or proves that GitHub has removed old PR/cached content.
Generic-only CI also cannot replace the owner's private-pattern scan. False
positives must be documented rather than weakening private rules or silently
rewriting an already cleaned publication branch.

Before changing visibility, review remote PR refs, old SHA links and Actions
retention separately. A rewritten branch alone does not purge all GitHub data;
see [GitHub's removal guidance](https://docs.github.com/en/authentication/keeping-your-account-and-data-secure/removing-sensitive-data-from-a-repository).

## Deployment limits

CPU/profile checks and an expected image hash do not uniquely identify a machine.
Deployment now verifies all candidate bytes in confirmed STOP before any RUN.
This requires at least 12.7 s (6,096 bytes) or 66.7 s (32,000 bytes) just for
the response data at the qualified serial settings. The current path has no
measured physical-PLC STOP duration; the earlier 1.84 s result does not apply.
See [timing and bench protocol](deployment-timing.md).
The default laboratory path still resumes automatically; `--leave-stopped`
suppresses RUN on both success and recovery. It is not a production process
approval workflow. Program rollback does not recover physical output states,
retained data or mechanical state. A production target manifest, a complete
final-RUN approval workflow and machine-state recovery remain separate engineering work.
See [SAFETY](../SAFETY.md) and the recorded [bench qualification](bench-qualification-20261114.md).
