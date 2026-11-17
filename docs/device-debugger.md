# Bounded device debugger

`monitor` reads selected devices without GX. Adjacent bytes are read together;
results include timestamps, CPU state and `atomic: false`. X/Y numbers are octal.

```
fx3u monitor M0 M7 D120 T48 C0 Y10 --count 10 --interval 1 \
  --port COM6 --wire-log monitor-new.jsonl
```

Qualified read ranges: X/Y0..27 octal, M0..255, D0..127, T0..48 current values,
and C0 current value. D/T/C values are raw unsigned 16-bit words.

`device-write` validates offline by default. `--execute` stops a running CPU,
backs up the addressed device, checks its expected value in STOP, writes once,
verifies readback, then restores the initial CPU operating state.

```
fx3u device-write D120 4660 --expected-value 0 --port COM6 \
  --wire-log edit-new.jsonl --out-dir edit-new
```

Only internal M0..255 and D0..127 are writable through this API. M uses FORCE
commands with a little-endian bit address; D uses BYTE_WRITE and a little-endian
word. This changes a device once; it does not hold an override against ladder.
The report separates `stopped_readback` from `after_resume`, and reports when
ladder has already overwritten the value. Other changes can happen later.

**STOP can reset physical outputs, and RUN may not restore their previous
state.** Even an internal M/D edit performs STOP. Output restoration is not
automatic; it depends on the machine's intended state.

No candidate write is retried automatically. If an ACK is lost, recovery writes
the saved value and checks it while STOP. Failed recovery suppresses automatic
RUN; a broken connection can prevent confirmation of the CPU mode. Independently
check the actual state before continuing. The edit directory retains raw
before/after/recovery data and a JSON report.
This is a bounded CLI debugger, not persistent forcing, online program editing
or a full memory backup.

Hardware evidence: D120 accepted 4660 and 65535. M0/M7 accepted one-shot ON in
STOP and were overwritten after RUN. Program bytes and scratch were restored.
STOP changed an existing latched Y1; its original state was restored separately
and checked twice while RUN. The public API does not expose that private recovery.
