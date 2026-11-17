---
name: fx3u-toolchain
description: Compile, inspect, simulate, and prepare verified FX3U program deployments with fx3u-toolkit, without GX Works2 at runtime. Use for the toolkit CLI and evidence-based instruction qualification; use fx-plc-dsl for authoring ladder logic.
---

# FX3U toolchain

API baseline: `fx3u-toolkit 0.1.0.dev3`. These instructions require at least
that version and are tested against it. Check the installed version with
`python -c "from importlib.metadata import version; print(version('fx3u-toolkit'))"`.
Keep the package and this checkout's skills aligned; an older dev0 lacks the
structured xref and execution-policy contracts described here.

In dev3, `compile-csv` checks both strict structure and every source step against
the selected encoder's map; the lighting generator uses the same checks.
`parse_lenient` remains an inspection API. Short extended deployment images require `--pad-to-region`.
Review source/candidate sizes and hashes; this flag replaces the rest with 0xFF.
Deployment verifies the full image in confirmed STOP before RUN. Select
`--leave-stopped` when automatic RUN (including recovery RUN) is not authorized;
its dynamic full-region checksum is deferred until an authorized RUN.
At 9600 baud, 7E1, ASCII HEX, a full-image response alone takes at least 12.7 s
for 6,096 bytes or 66.7 s for 32,000 bytes. This is not total STOP time; requests,
writes and adapter delays add time. The earlier 1.84 s bench result predates this
verification path. Read [deployment timing](../../docs/deployment-timing.md)
and measure the current path on an authorized bench before installation use.

Use the installed `fx3u` command or `python -m fx3u`. Locate the toolkit checkout
and read its `SAFETY.md` before live operations. Repository documentation links
below resolve within this checkout; retain access to it when installing a skill.
The toolkit currently supports one observed CPU/version/layout profile. Do not
remove CPU/header/checksum refusals to accommodate another device.

## Select the evidence and codec

- `catalog --mnemonic MOV` reports documentation, encoding and recorded CPU tests
  separately. An encoder form or an offline round-trip is not hardware execution.
- `compile` / `disassemble` use the conservative default encoder.
- `compile-experimental` / `disassemble-experimental` use the extended codec,
  independently calibrated offline. Its executable set is wider than the current
  catalog: an absent catalog spelling is not proof that the encoder rejects it.
- Unsupported forms must fail, not turn into guessed bytecode. A timer/communication
  or motion instruction compiling successfully does not qualify its timing or I/O.
- Read [extended-codec](../../docs/extended-codec.md) for extended forms, layout,
  strings and the catalog gap; [evidence](../../docs/evidence.md) for CPU cases;
  [scan model](../../docs/scan-simulator.md) before relying on a simulation.

## Offline workflow

Start with source, target CPU, device allocations and expected scan results.
For a change to an existing program, preserve the unmodified instructions and
compare the decoded result; do not regenerate unrelated logic.

`xref` returns an object with `hits`, `uncertainties`, and `complete`, not the
older literal-operand list. Select `xref --experimental` for extended images.
Grouped operands and supported static ranges are expanded; runtime indexing
and unknown footprints remain uncertain. An empty report is not authorization
to use an address. See [device usage](../../docs/device-usage.md).

```sh
fx3u catalog --mnemonic MOV
fx3u compile program.il out/program.bin
fx3u disassemble out/program.bin out/decoded.il
fx3u compile out/decoded.il out/roundtrip.bin
fx3u xref out/program.bin D0
fx3u simulate program.il --scans 3 --dt-ms 10 --watch M0 D0
```

Compare `program.bin` and `roundtrip.bin` byte-for-byte. Repeat with the paired
experimental commands if the task needs that codec. The simulator is an explicit
subset: unsupported instructions are rejected, not assumed to work. Its virtual
scan timing does not measure the actual PLC or attached hardware.

IL source ends with `END`. Current parsing uppercases string contents too, so
mixed-case strings and escaped quotes need separate qualification.

## Live sequence, only for an authorized task

1. Identify the actual adapter, CPU and output wiring; a previous COM number is
   not identity. Use native Windows Python for the currently qualified transport,
   one owner per serial port. Start with `status` / `diagnostics` / `monitor`.
2. Before any write, require explicit human authorization for that live action
   and its device/program scope. Record the initial CPU mode, output state,
   program hash and relevant device values; obtain an independent recovery copy.
   Authorization to run offline compilation does not authorize deployment.
3. `dump` saves a program window, not a complete PLC backup. It excludes parameters,
   retained devices and comments. `--full-region` requires the qualified header
   and checksum; it is not a generic initializer for a new CPU.
4. Prepare `deploy` without `--execute`, matching the candidate codec, selected
   window and expected live SHA-256. Only the explicitly authorized execution
   adds `--execute`. Require the reported backup and read-back results.
5. STOP may reset latched Y outputs, and RUN does not necessarily restore them.
   A recovery plan must cover output state and touched devices as well as program
   bytes. Never force outputs back without understanding their machine function.
6. An unconfirmed write may have happened: do not retry blindly. Preserve logs;
   unverified recovery needs explicit resolution and a CPU-status check; a broken
   connection can leave the final mode unknown.

Do not use a basic default-profile deployment for an extended/full-region image.
Read the exact `deploy --help` for flags before preparing the concrete command.
Use the [change acceptance record](../../docs/change-acceptance.md) to identify
the failure scenario, negative test, independent review and qualification level.
All wire logs and live images belong to the user's private project, not this
public toolkit. Do not bundle Mitsubishi DLLs, real plant programs or raw traces.
Do not run positioning, pulse-output or other motion tests without a separately
approved and electrically confirmed bench.
