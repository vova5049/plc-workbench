# Hardware evidence

What has actually been executed on a real PLC, and what it proves. Raw traces, images and
the user program they came from are **private** and are not part of this repository; this page
summarises method and results. Evidence levels used across the project:

| Level | Meaning |
|---|---|
| `manual_documented` | Mitsubishi documents the instruction for FX3U. Says nothing about this encoder. |
| `encoder` | `fx3u compile` can encode the listed operand forms (unit-tested). |
| `present_in_captured_user_program` | The form appears in a real program that compiles byte-identically to the PLC read-back. Proves encoding, not isolated semantics. |
| `functionally_tested_subset` | A small dedicated test ran on the CPU and its effect was read back. Only the listed cases. |

**Tested hardware (all evidence below):** one FX3U main unit, transistor (MT) outputs, version
reply `f75e` (reported by GX Works2 as CPU 3.11), USB-to-RS-422 programming cable on Windows,
9600 bps 7E1. Nothing here has been tested on any other unit, CPU version or cable.

## <a id="captured_user_program_20261107"></a>Byte-identical compile of a real program

- A 1585-instruction / 2977-step user program (GX Works2 IL CSV export) compiled with
  `fx3u compile-csv` reproduces the 6096-byte program region read from the PLC **byte for byte**.
- The same region was read independently by GX Works2 and twice by this client: identical SHA-256.
- Re-run privately with `FX3U_PRIVATE_CORPUS=<manifest.json>` (`tests/test_private_corpus.py`).

## <a id="toolchain_trial_20261107"></a>New rung executed without GX

Source IL → bytecode → STOP → RAM write (changed blocks only) → read-back compare → RUN → read result:

| Step | Result |
|---|---|
| New program | original logic + `LDI M239 / MOV K123 D120` before END (1587 instructions, 2983 steps) |
| Execution | D120 = 0 before; three independent reads during RUN = 123 |
| Restore | separately compiled `MOV K0 D120` → 0; original image restored and verified, CPU RUN |
| STOP window | ≈1.58 s per deployment (one changed block) |

That timing belongs to the deployment path used in the recorded trial. It does
not measure the current complete-image verification in STOP; see
[deployment timing](deployment-timing.md) for response-data lower bounds and the
bench measurement still required for the current implementation.

A first attempt checked RUN too early and correctly restored the original; the client now waits
up to 2 s for the RUN transition without re-sending the command (regression test included).

## <a id="internal_lab_20261107"></a>Instruction lab (internal devices only)

Program: `examples/instruction_lab.il` appended to the original program; no physical outputs.
Preflight required all scratch devices (M239–M251, T48, C0, D121) to be zero and unused.

Observed: C0 counted 6, 7, 8, 9, 10 and its contact turned on at the preset; SFTL filled the
8-bit window M241–M248 (63 → 127 → 255); ROL produced successive powers of two and wrapped
32768 → 1 (rotate, not shift); T48 (100 ms base, K20) cycled 0–19 with an estimated period of
1.6–2.4 s, limited by PC polling resolution. Afterwards scratch devices were reset, the
original image restored, CPU RUN.

## <a id="held_counter_20261107"></a>Counter with a permanently true condition

`OUT C0 K3` driven by M249 held at 1 for thousands of scans: C0 = 1 in every sample. The counter
counts rising edges of its condition, not scans. Scratch reset and original restored afterwards.

## Not established

Power-cycle retention of RAM writes; other CPU versions or memory layouts; automatic detection
of program capacity; parameters/comments/device memory backup (`dump` covers the program region
only); positioning/high-speed instructions; multi-PLC wiring.

## extended_codec_campaign_20261107

Independent encoder/decoder, calibrated offline against a locally installed
converter and checked against the captured real program (full 32,000-byte
conversion round-trip; IL to 5,954-byte executable prefix). The extended profile
covers all 250 inventory base entries through canonical forms and 528 runtime
mnemonics. The private campaign checks 73,404 byte matches and 31,154 matching
rejections. Encoding evidence is separate from CPU/physical qualification.

The internal-only CPU trial passed signed arithmetic, double-word operations,
rotations, bit groups, BCD/BIN, Z-indexing, float addition, sine, timer and counter
checks. The original image, scratch state and RUN were restored and verified.
An earlier trial's same-scan counter-reset criterion failed; its result was
retained rather than relabeled as a pass. See `extended-codec.md` for limitations.

The maximum-length 32,000-byte program ran and produced its marker. A shorter
program cleared the marker, then the original full program, header, scratch and
RUN were restored and verified. Header bytes 2:4 are the observed CPU-maintained
sum16; static layout fields and the program checksum are validated separately.
The scan model reproduced both internal CPU samples, including the earlier
same-scan counter-reset outcome. This does not qualify every hardware function.

## RUN-BENCH-20261114
Selected RUN writes and restored device/output state are documented in
[the bench record](bench-qualification-20261114.md). Raw traces and real user
programs are retained privately. Qualification is limited to the listed forms.
