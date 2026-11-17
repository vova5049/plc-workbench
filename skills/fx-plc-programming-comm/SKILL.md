---
name: fx-plc-programming-comm
description: Inspect an FX3U through its built-in programming port with fx3u-toolkit and prepare bounded device edits or deployments. Use for Programming Communication over the existing cable, not Computer Link, MODBUS or unqualified multidrop networks.
---

# FX3U Programming Communication

API baseline: `fx3u-toolkit 0.1.0.dev3`, the tested minimum for these instructions.
Check `importlib.metadata.version('fx3u-toolkit')` in the project environment.
Earlier dev0 has a different `run_plan` contract; update callers and package
together. Use a pinned package and its matching repository skills in consumers.

Use `fx3u.client.Session` and the toolkit CLI, not the older `fxplc` wrapper.
`Session` is a privileged raw transport: its low-level methods are not a
read-only interface or a sandbox. Use the bounded helpers for application
device writes; do not expose a raw session to untrusted callers.
The supported connection is point-to-point through the CPU's built-in programming
port using the Programming Communication protocol. Do not treat it as Computer
Link/multidrop or assume that a communications expansion board is installed.
The currently qualified transport uses native Windows Python, 9600 baud, 7E1,
and specific serial-control settings applied by `Session.open()`.

## Read before selecting an operation

Read toolkit `SAFETY.md` and [device debugger](../../docs/device-debugger.md).
Use [evidence](../../docs/evidence.md) to distinguish actual CPU cases from frames
inferred or only tested on the host. The client checks the observed CPU profile;
a refusal on another CPU/header is a boundary, not an instruction to remove it.

Identify the actual serial adapter and CPU. Do not copy an old COM number as
proof of identity. Close other port owners. Every live command needs a fresh
exclusive wire-log path; preserve it in the user's private working directory.
The following are read-only examples; replace PORT and paths with the observed
adapter and new private locations:

```sh
fx3u status --port PORT --wire-log logs/status-new.jsonl
fx3u diagnostics --port PORT --wire-log logs/diagnostics-new.jsonl
fx3u monitor M0 D0 Y0 --count 3 --interval 1 --port PORT --wire-log logs/monitor-new.jsonl
```

`monitor` is not an atomic snapshot. Read ranges currently include X/Y0..27
(octal), M0..255, D0..127, T0..48 current values, and C0 current value.
D/T/C values are raw unsigned 16-bit words. Validate addresses with the toolkit,
not a guessed decimal conversion of X/Y labels.

## Choose the write semantics explicitly

| Operation | CPU effect | Required preparation |
|---|---|---|
| `device-write` | Temporary STOP; one internal M/D edit, readback, previous CPU mode restored | Expected device value, device backup/recovery directory, authorization for STOP and that edit |
| `live-write` | One allow-listed M/Y FORCE or D-word write while RUN; no STOP or automatic restoration | Explicit allow-list, authorized target/value, unused or intentionally controlled devices, independent recovery plan |
| `deploy` | STOP, changed program blocks, verification and attempted recovery | Fresh program hash, image/layout qualification, backup, authorization for program replacement |

These dry-run examples do not open a port:

```sh
fx3u device-write D0 7 --expected-value 0 --port PORT --wire-log logs/edit-new.jsonl --out-dir logs/edit-new
fx3u live-write D0 7 --allow allow.json
```

For the second example, `allow.json` contains an explicit list such as
`{"allow": ["D0"]}`. That demonstrates syntax, not that D0 is free or approved.
In Python, pass the policy to execution as well as planning:
`plan = plan_label("D0", 7, allow)` followed by
`run_plan(session, plan, allow)`, or use `force_bit` / `write_units` with `allow`.
Execution rebuilds and checks the complete intent, wire frame and readback
before any session I/O. A `Plan` is data, not proof of authorization; manually
changed frames or a plan outside the execution policy are refused. See
[live API boundaries](../../docs/live-api.md).
Only an explicitly authorized live action adds `--execute` and the required
live port/log arguments. Do not silently convert one write method to another:
STOP itself can reset latched physical outputs, even when only D/M is edited.

Before writing, record the program/CPU/output state and relevant device values,
check device use, and establish how to restore them after the particular trial.
A raw program `dump` is not a complete backup of parameters, retained memory,
comments or output state. Restore physical outputs only within the user's
approved machine/bench scope, then verify their actual state by readback.

## Interpret results without blind retries

FORCE is one-shot, not a persistent override. Running ladder may overwrite the
bit/output immediately or consume a command before readback. A differing readback
is not by itself proof that a write was ignored. For a command mailbox, qualify
an acknowledgement/consumption counter in the PLC program.

With `live-write`, an uncertain ACK or readback means the write may have happened.
Only NAK proves rejection; do not retry until independent observation resolves
the state. The live API does not stop, restore devices or recover the program.
Read the [selected bench record](../../docs/bench-qualification-20261114.md)
for observed RUN-write forms; do not promote other addresses, physical signal
behaviour or CPU variants after these specific successes.

Do not send raw write frames as a workaround for allow-list, RUN, CPU or layout
checks. Do not write special M/D devices through this bounded interface. No
motion/pulse-output/physical loop tests without their separate approved bench.
