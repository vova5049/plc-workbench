# Scan simulator

`fx3u simulate source.il --scans 50 --dt-ms 10 --watch D0 D1 C0 T48 M42`
executes an explicit subset of IL semantics offline. `--trace frames.json` accepts
an array of scan frames with optional `inputs` (X devices, boolean values) and
`dt_ms`. Unsupported instructions are rejected before the first scan. Special
relay, module, communication and high-speed/directional counter models are limited
or deliberately absent; this is not a complete CPU or machine simulator.

The model includes boolean buses/branches, instruction-local edge memory, regular
timers and 16-bit counters, selected signed/double-word arithmetic, transfers,
bit operations, BCD/BIN, rotations, shifts, index registers and floating-point
operations. Timers advance in virtual scan time; real timing can differ by a tick.
Status/error flags and hardware timing require separate qualification.

Tests use the actual IL recovered from two captured CPU trials and compare all
observed D0..31, D120, M0..47, T48 and C0 values. One trial includes the same-scan
counter-reset case; the model reproduces the observed zero count. No instruction
is silently replaced by a mock success.
