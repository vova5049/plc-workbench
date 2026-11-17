# Safety

A PLC runs machines. This software can stop it and change its program. Read this first.

1. **STOP changes CPU execution and can affect outputs.** A confirmed CPU STOP does not establish a safe machine state. `deploy --execute` switches the CPU to STOP for the duration of
   the write and full-image verification. STOP duration depends on the region and
   link; it has not been remeasured for these new guards. Outputs follow the CPU's STOP behaviour.
   At 9600 baud, 7E1, ASCII HEX, the full-image response data alone takes at least
   12.7 s (6,096 bytes) or 66.7 s (32,000 bytes), before requests, framing,
   writes and adapter delays. These are lower bounds, not maximum downtime.
   The historical 1.84 s STOP predates this verification path. See
   [timing and bench measurement](docs/deployment-timing.md).
   Never deploy to a PLC whose outputs drive motion, heating, pressure or anything that is unsafe
   to stop or restart abruptly, unless the machine is in a safe state and you follow your site's
   lock-out procedures.
   `device-write --execute` also performs STOP, even for an internal M/D edit.
   Existing latched outputs can remain reset after RUN; the command does not
   automatically restore them. See [device debugger](docs/device-debugger.md).
2. **Only RAM is written.** Retention across a power cycle has not been tested. Keep a GX Works2
   project / independent backup of anything you care about. `dump` saves the program region only —
   not parameters, comments or device memory.
3. **Dry run is the default.** Without `--execute`, `deploy` never opens the port. With it, you
   must pass the SHA-256 of the image currently in the PLC (`dump`), so stale or foreign programs
   are not overwritten blindly. A backup of the live image is written before any change; an
   uncertain write triggers recovery of the saved program bytes with readback verification.
   The original error is preserved even when recovery succeeds. If recovery fails,
   the client attempts STOP once and separately reads CPU status; `result.json`
   records `recovery_error`, `recovery_stop_attempted`, any `recovery_stop_error`,
   and `final_cpu_mode` (`STOP`, `RUN` or `unknown`). A failed status read records
   `final_cpu_mode_error`. A lost connection can prevent both STOP and confirmation:
   never infer a safe CPU state from an attempted command or a saved program alone.
   Changed blocks and the complete candidate are checked in confirmed STOP before
   RUN. The default laboratory mode then resumes RUN and checks the image again;
   use `--leave-stopped` to suppress automatic RUN on success and recovery.
   Full-region dynamic checksum validation is deferred until RUN in that mode.
   `--leave-stopped` also stops the CPU when no blocks differ. Observed STOP is
   not proof of a safe machine. Program restore does not restore latched outputs,
   device values or machine state. Short extended images additionally require
   `--pad-to-region`; review original/candidate sizes and hashes before execution.
4. **One qualified CPU.** The client refuses CPUs whose version reply differs from the tested one.
   Do not patch that check out to "just try" on a production machine.
5. **Encoder subset.** Unsupported instructions/operands are rejected. Do not hand-edit bytecode.
6. **The lighting case study uses physical outputs (Y).** Its source and generator
   are an offline example, not an authorization to deploy to arbitrary wiring.
   Start bench fixtures on internal devices (M/D/T/C); identify every output circuit
   and its machine function before testing Y. STOP can clear latched Y, and RUN may
   not restore them. Restoring output states requires a separate, authorized plan.
7. **AI agents** using this toolkit must obtain an explicit human instruction for every live
   write (`deploy --execute`, `device-write --execute`, `live-write --execute`) and must report
   wire logs and verification results, not summaries.
8. **Live writes (`live-write`, `fx3u.live`) change a RUNning CPU without STOP.**
   Selected M250, D44/D45, M240-byte and Y0 forms now have a
   [bench qualification record](docs/bench-qualification-20261114.md); all other
   addresses/forms and physical output behaviour remain unqualified. `live-write M250 1` FORCEs one M bit, `live-write Y1 1` FORCEs one
   output (octal numbering; earlier evidence covered Y1 ON; the newer record adds Y0 ON/OFF and M250 ON/OFF), `live-write D44 0xA5A5`
   writes one D word, the API can also write M bytes; nothing is stopped, restarted or restored, and a
   running ladder may overwrite or consume the value at once (a FORCE of an M bit or output is not a
   persistent override). Rules enforced before anything is sent: a JSON allow-list is mandatory
   (`{"allow": ["M240-M255", "D24-D49"]}`; absent or empty = refusal), every written element must
   be in it (a span that is one element too long is refused), only M0..M255, Y0..Y27 (octal,
   FORCE only) and D0..D127 can be named (never X/T/C or special M8000+/D8000+ devices), and a CPU reporting STOP is refused.
   Without `--execute` the command only prints the frame and never opens a port. With it, exactly
   one attempt is made, followed by one read-back; there is **no automatic retry**: an
   unconfirmed write (lost ACK, garbled reply, read-back that differs or fails) is reported as
   `uncertain` — the write may have been executed — and only a NAK proves it was not. A caller
   that wants to try again must first establish the device state by other means (for a mailbox
   bit: a consumption counter in the ladder). Choose allow-listed devices the loaded program does
   not use (check with `xref`), and do the first use on a live PLC only in a supervised session.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND (see LICENSE). You are responsible
for its use on your equipment.
