# Selected RUN-write qualification — 2026-11-14

Evidence ID: RUN-BENCH-20261114. One previously qualified FX3U CPU/profile,
native Windows Python and a programming-port adapter; CPU version reply f75e.
The owner identified the bench controller and authorized the trials. The Y0
relay was physically removed according to the owner. No physical voltage,
frequency, relay contact or connected-load measurement is claimed.

## Recorded cases

| Operation | Observed operands/data | Result |
|---|---|---|
| One-shot M FORCE | M250 ON and OFF | ACK, immediate and later independent readback, restoration PASS |
| Single D word | D44=0xA55A and 65535 | ACK/readback/restoration PASS |
| Two D words | D44/D45=0x1234/0xABCD | ACK/readback/restoration PASS |
| Two M bytes | M240..255=0xA5/0x5A | ACK/readback/restoration PASS |
| One-shot output FORCE | Y0 OFF and ON | ACK, output-image readback and restoration PASS |

Eight positive trials ran without STOP. Each trial restored its touched devices
and checked the original output image. After the series, the complete program
window was unchanged, original outputs matched on two reads, and CPU remained RUN.
The observed write/status/readback operation durations were about 74–92 ms;
this is not a PLC-network or end-to-end button latency measurement.

## DSL-to-CPU round-trip

A separate trial generated two M-controlled MOV/ADD rungs through the Python
DSL, exported and parsed UTF-16 CSV, compiled with the extended codec, and
checked the decoded program in the scan simulator. The fixture was appended
before END in the existing program; the preceding program bytes were preserved.
Only one program block changed. No new Y coil or motion instruction was added.

The toolkit deployed and verified the candidate without GX Works2. On the CPU,
the observed D-register trace was `0/0 -> 1234/1241 -> 1234/1241`, matching the
simulator for the control sequence OFF/ON/OFF. The complete original program was
then restored and verified by its original SHA-256. Scratch devices and the
original output image were restored; two final output reads agreed and CPU was
RUN with no active error relay. Each deploy/restore STOP lasted about 1.84 s.

That timing belongs to the deployment implementation used on 2026-11-14.
It does not measure the later full-image verification in STOP. The current
path needs a new authorized bench measurement; its response-data lower bound
alone is 12.7 s for 6,096 bytes or 66.7 s for 32,000 bytes. See
[deployment timing](deployment-timing.md).

Output restoration immediately after each RUN was implemented by a private
bench wrapper. It is not automatic behavior provided by the public deploy CLI.
Program changes were RAM-only; this trial does not establish persistence after
a power cycle, complete parameter recovery, or every extended instruction.

## Limits

These cases qualify the listed forms on the observed bench, not every address,
CPU or operand span. FORCE remains one-shot; ladder can replace or consume it.
`qualified_on_hardware` in generic runtime results remains false: it is not an
attestation of the particular physical CPU or an exhaustive address qualification.
Use this evidence table instead of promoting every implemented operation.
Unknown CPU/header checks and allow-lists remain required. Writes to other Y
are not qualified by the Y0 case. No physical encoder/motion/module test,
power-cycle persistence or complete parameter recovery was performed.
Raw wire logs, user program and before/after device images are retained in the
private bench evidence repository, not distributed with this toolkit.
