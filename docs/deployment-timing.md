# Deployment timing and qualification

The current deployment policy verifies the complete candidate in confirmed STOP
before RUN, including blocks that were not written. Per-block readback alone
cannot detect corruption elsewhere in the image. Full verification remains
required; a faster installation policy has not been qualified or implemented.

## Response-data lower bound

The qualified link uses 9600 baud, 7E1: one start bit, seven data bits, one parity
bit and one stop bit, or ten bits per character. Each binary image byte is sent
as two ASCII HEX characters. Therefore a single complete readback requires at
least `image_bytes * 2 * 10 / 9600` seconds for its response data alone:

| Program window | Response-data characters | Minimum response-data time |
|---|---|---|
| 6,096 bytes | 12,192 | 12.7 s |
| 32,000 bytes | 64,000 | 66.67 s |

This excludes request/response framing, writes, per-block readback, CPU state
polling, file writes, adapter delays and error recovery. It is neither a STOP
prediction nor an upper bound. A no-change deployment without `--leave-stopped`
does not request STOP. Recovery may require another complete image readback.

With `--leave-stopped`, successful full-image verification is reused for the
final comparison because no program write or RUN intervenes. This avoids a
second full readback while leaving the same pre-RUN guard in place. The CPU's
dynamic full-region checksum remains deferred until an authorized RUN.

The historical bench record's approximately 1.84 s STOP used an earlier path.
It cannot qualify the present implementation. No physical-PLC STOP duration
has been measured for the current path.

## Report fields

Dry-run output, the CLI candidate sidecar and deployment `result.json` include
`verification_policy: full_image_in_stop` and `full_image_readback`. The latter
records the serial assumptions, image size and `response_data_min_seconds`;
`hardware_stop_timing_qualified` remains false.

Runtime timing uses a monotonic host clock. `stop_seconds` runs from the request
for STOP through confirmed RUN, including the STOP command and state checks.
`stop_verification_seconds` instead ends after final validation in deliberately
continuing STOP. It is not a completed downtime. Corresponding recovery fields
use the same boundaries for recovery work. Failed phases retain attempt timings.

`stop_intervals` describes phases (`deploy`, `recovery`, `final_stop`), their
elapsed seconds, `stop_confirmed`, `run_confirmed` and end condition. Several
phases can belong to one continuous STOP. `stop_request_to_run_seconds` records
each completed observation from the earliest unresumed STOP request through
confirmed RUN, including a failed deployment and its recovery where applicable.
If RUN is not confirmed, `unresumed_stop_elapsed_seconds` is only the observation
elapsed so far. These conservative host observations include periods when the
actual CPU mode may be uncertain; they are not independent physical measurements.

## Bench measurement before installation use

1. Obtain authorization for the identified bench CPU, outputs, program change
   and intended final mode. Record the adapter identity, actual serial settings,
   CPU/header profile, program-window length and initial output/device state.
   Establish independent backup and machine-state recovery before STOP.
2. Prepare the exact candidate with an expected live hash and inspect the
   dry-run response-data lower bound. Use a fresh private wire log and output
   directory. Agree the acceptable downtime for this bench before execution;
   an elapsed-time estimate does not make an installation safe to stop.
3. Measure a representative changed-block deployment and restoration using the
   current implementation. Preserve `result.json` and wire timestamps alongside
   independent CPU/output observations. Test no-change and leave-stopped modes
   only when included in the authorization. Record interruptions as incomplete
   measurements, including the final observed CPU mode.
4. Compare the full image and intended output/device state after restoration.
   Record measured durations, region size, changed blocks, observed delays and
   exact source/package revision. Qualify the installation separately; a single
   successful bench run cannot establish maximum downtime or power-loss behavior.

Keep real target identities, programs and logs in the private installation
project. The current host timing tests exercise accounting and failure paths;
they do not measure a serial adapter or physical CPU.
