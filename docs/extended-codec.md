# Independent extended FX3U codec

The extended codec runs without GX Works2 or Mitsubishi DLLs. It uses independently
implemented format rules and an oracle-calibrated profile. The calibration harness
is private research tooling and is not part of the package.

## Coverage and evidence

All 250 base entries in the FX3U inventory have at least one canonical encoding.
The runtime profile contains 528 mnemonic names, including separate D/P variants.
Some manual base names use a canonical D-prefixed spelling (for example SIN/DSIN
and DEG/DDEG); this does not imply a 16-bit form exists.

The private regression campaign checks 73,404 byte-identical encodings and 31,154
matching rejections, including complete device-address sweeps and boundary cases.
The decoder is checked by re-encoding its canonical IL. This is evidence of tested
forms, not exhaustive coverage of every operand combination or CPU firmware.

An internal-only CPU trial checked signed arithmetic, double-word arithmetic,
bit groups, rotations, BCD/BIN, index register addressing, floating-point addition,
sine, timer and counter behavior. Its original program, scratch state and RUN
were restored and verified. Physical I/O and attached-module functions require
separate commissioning and measurement.

## Commands

```
fx3u catalog --mnemonic ADPRW --experimental
fx3u compile-experimental program.il program.bin
fx3u disassemble-experimental program.bin decoded.il
fx3u deploy program.bin --experimental --pad-to-region --leave-stopped --port COM6 --wire-log new.jsonl \
  --out-dir new-backup --expected-sha256 EXPECTED_HASH
```

Deploy defaults to a dry run. `--execute` enables writes after the expected-image,
backup, STOP, per-block and full-image readback checks. A short extended image
requires `--pad-to-region`: this explicitly replaces the rest of the selected
region with `0xFF`. Dry-run and execution reports include `source_bytes`,
`candidate_bytes`, both SHA-256 values and `padded`. This does not detect a
logically truncated but decodable source; it makes the replacement decision
explicit. An input record beside the wire log (`WIRE_LOG.input.json`) is created
before connection so its identity survives deployment/recovery failures.

## Full-region layout and integrity

`--full-region` on dump/compare/deploy selects a 32,000-byte window only after
qualifying the observed static 92-byte program header. Unknown headers are
refused before reading that full program window. The partial final block is
handled explicitly. This is one observed CPU/layout profile, not general CPU
discovery or initialization of a new PLC.

Layout qualification and image integrity are separate checks:

- Static fingerprinting ignores only header bytes `2:4`, which contain a dynamic
  little-endian sum16 maintained by the CPU.
- Integrity compares that word with the low 16 bits of the sum of all 16,000
  little-endian words in the complete 32,000-byte program window, including padding.
- Header and program reads are not atomic. A checksum mismatch is a refusal to
  claim integrity, not proof of which device or concurrent change caused it.

`dump --full-region` saves `program.bin`, `program-header.bin` and `manifest.json`
before decoding. Its manifest/stdout adds `layout_valid`, `checksum_valid`,
`checksum_expected_sum16`, `checksum_actual_sum16` and `verified` to the usual
hash/size/mode/scope information. A checksum mismatch retains the raw artifacts,
sets `checksum_valid=false`, `verified=false`, records `checksum_error`, skips
decoding and returns exit2. Preserved raw bytes are not a verified recovery copy.
Unknown static headers are refused before this normal dump path.

`compare --full-region` reports byte equality as `match` separately from
`checksum_valid`, with the same layout/checksum fields. `success` requires both
byte equality and a valid checksum; a mismatch in either returns exit2.
The small default-region commands keep their existing format; they do not
claim this full-window checksum verification.

## Deployment and recovery order

Before any program write, full-region deploy checks the current layout,
expected image SHA-256 and original full-window checksum, and saves a fresh
backup. After confirming STOP, it writes only changed blocks, checks each block,
and compares the **entire candidate image before RUN**. `stopped.bin` preserves
that read and `pre_run_image_verified` reports the comparison. The default lab
path then runs the CPU and checks the full image, dynamic checksum and RUN status.
These new paths have host fault-injection coverage, not new hardware timing evidence.

`--leave-stopped` (API `leave_stopped=True`) omits automatic RUN on success and
recovery. It also stops a previously RUNning CPU when no program blocks differ.
Success requires an observed STOP and equal full bytes; it does not mean the
machine is safe. Resuming requires a separately authorized decision. A CPU whose
STOP cannot be confirmed is not written. The initial-RUN precondition and all
CPU/layout refusals remain enforced.

The observed CPU refreshes dynamic header bytes `2:4` after RUN. Consequently,
checksum verification after RUN cannot simply be moved before RUN. On a failed
write or final check, recovery rewrites all potentially touched original blocks,
compares the recovered full bytes with the original **before RUN**, then checks
the refreshed full-region header/checksum after RUN. With `--leave-stopped`,
`checksum_status` (or `recovery_checksum_status`) is `deferred_until_run`;
static-header and byte verification still apply, but the dynamic CPU checksum
is not claimed for the stopped candidate. The normal path reports
`verified_after_run` only after its checksum comparison succeeds.

If recovery fails, the client attempts STOP once and independently reads final
CPU status. `result.json` preserves the original failure plus `recovery_error`,
`recovery_stop_attempted`, optional `recovery_stop_error`, and `final_cpu_mode`
(`STOP`, `RUN`, `unknown`); a failed status read adds `final_cpu_mode_error`.
Neither an attempted STOP nor a lost ACK proves the final state. An uncertain
program write is never blindly retried. Recovery is a separate controlled write
of the saved image, not an automatic repeat of the failed candidate write.
Output/device-state restoration remains separate from program-byte recovery.
The recovery failure-path improvements have host fault-injection coverage;
they have not been requalified on a physical PLC after the recorded changes.

## Limits

- Assembly validates calibrated encoding forms, not every runtime value or ladder
  control-flow invariant. Undefined labels and invalid instruction sequencing can
  still produce a CPU error. Use an internal bench before deploying new logic.
- IL is currently normalized to uppercase, including string content; mixed-case
  string preservation and escaped quotes are not qualified.
- Raw program-window backups and program headers are not complete backups of all
  parameters, retained devices, comments or attached modules.
- RAM-only deployment does not prove persistence after power loss.
- Motion, high-speed signals, serial peers, CF adapters and other attached hardware
  remain separately qualified. Successful compilation does not prove those signals.

The instruction semantics and hardware requirements are defined in Mitsubishi
JY997D16601-R (Basic & Applied Instructions), JY997D16801-K (Positioning), and the
relevant communication/module manuals. FNC numbers are inventory identifiers;
they must not be substituted blindly for machine opcodes.

Historical full-region bench trials covered a 32,000-byte maximum-length RAM
program, a shorter program and restoration. Those results do not qualify every
new failure path or CPU. See [scan simulator](scan-simulator.md) for offline
scan-model coverage and [bench qualification](bench-qualification-20261114.md)
for the separately recorded selected RUN-write and DSL-to-CPU cases.

## Known limitation: catalog coverage

The encoder knows 528 mnemonics (`extended_codec.runtime_mnemonics`), but the instruction
catalog (`fx3u catalog --experimental`, `capabilities.json`) lists only 317 canonical
mnemonics. The encoder, not the catalog, is the source of truth for what can be encoded.
Not listed in the catalog: the `P` (pulse) variants of catalog mnemonics, and 25 further
mnemonics (`LDD<`..`LDD>=`, `ANDD<`..`ANDD>=`, `ORD<`..`ORD>=`, `EXTR`, `DEXTR`, `EXTRP`,
`DEXTRP`, `OPT`, `DOPT`, `DXOR`). A test pins this gap, so it cannot grow unnoticed.
Completing the catalog is separate, planned work.
