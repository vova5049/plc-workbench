# Change acceptance record

Use one record for each proposed program or toolkit change. Keep real plant
identities, hashes and wire logs in the private installation project. A skill
or a completed template does not authorize an operation on hardware.

## Record template

| Field | Required evidence |
|---|---|
| Scope | Source revision, changed behavior, affected devices and excluded functions |
| Failure scenario | Concrete input/state/scan sequence that previously failed |
| Independent expectation | Expected result derived from requirements, a hand trace or measured evidence |
| Negative test | Refusal or failure injection, including evidence of no transmission where required |
| Regression | Existing behavior preserved, or an explicit intentional delta and both hashes |
| Review | Reviewer, reviewed revision and unresolved findings; independence from implementation stated |
| Qualification | Host, encoding, scan simulation or named physical-PLC trial; never infer the next level |
| Live authorization | Human-authorized target and operation; physical identity and output wiring checked separately |
| Recovery | Program-byte recovery and machine-state recovery described separately |
| Outcome | Accepted scope, failed checks, deferred requirements and supporting artifact paths |

Before a live deployment, record the source-file and normalized candidate
lengths/hashes, padding decision, expected live hash, selected region/profile,
requested final CPU mode, adapter and physically confirmed installation. CPU
profile and program hash are not a unique machine identifier. If identity cannot
be confirmed independently, the record must remain unapproved for a live action.
This is a review record, not a deployment-manifest validator.
Also record the full-image response lower bound, measured STOP intervals,
whether RUN was confirmed, and the accepted downtime for the particular bench
or installation. A still-stopped or unknown CPU has no completed STOP duration.
See [deployment timing](deployment-timing.md).

## Current host regressions

- The gesture regression presses again in the scan immediately after the previous
  silence-window finalization, then expects a second independently recognized
  command. All pairs of four gestures are exercised on each of 24 inputs.
- Invalid zone indices include negative, out-of-range, boolean and non-integer
  values in all three command maps. They are refused before constructing a program.
- Deployment tests corrupt an untouched block while the CPU is stopped and
  require full-image refusal before RUN. Ignored/unknown STOP blocks writes;
  leave-stopped recovery cannot claim success while the CPU reports RUN/unknown.
- Import tests use hand-written malformed CSV records. Strict compilation
  refuses them without partial output. Lenient inspection remains separate.
  Source steps must also match the selected encoder's complete instruction map;
  tests cover missing steps, a nonzero start, operand continuations and valid
  instructions that occupy multiple steps.
- Short-image CLI tests require explicit padding consent before opening a
  session and preserve both input identities even after deployment fails.

These are host/simulator evidence. Debounce values, retention, restart and
power-loss behavior still need agreed requirements and measured PLC trials.
The historical lighting readback qualifies the unchanged legacy reference,
not the corrected active program or new deployment paths.
