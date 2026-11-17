# Bounded live-write API and raw transport

`fx3u.client.Session` is a **privileged raw programming-port transport**. Its
low-level exchange/read/write methods can address protocol operations directly.
It is not a read-only capability, a Python sandbox or a protection boundary
against hostile code running in the same process. Keep it inside a trusted
application. No new read-only wrapper is implied by this interface.

For a bounded RUN device edit, use `fx3u.live`. Planning is offline; execution
requires the current explicit allow-list again:

```python
from fx3u.live import AllowList, plan_label, run_plan

allow = AllowList(["D44-D45"])
plan = plan_label("D44", 7, allow)  # No session or I/O needed to prepare this.
# Only in an independently authorized live session:
# result = run_plan(session, plan, allow)
```

`force_bit(session, label, on, allow)` and
`write_units(session, label, values, allow)` use the same execution check.
Callers of the older two-argument `run_plan(session, plan)` must pass the policy
as the third argument; omission is an error before I/O.
This API change is released as `0.1.0.dev1`; do not substitute it silently for a
consumer pinned to `0.1.0.dev0` without updating and verifying its call sites.

## Validation and execution

A `Plan` describes intent and expected wire/readback data. It is not an
authorization token. Before performing any session I/O, `run_plan`:

1. Requires an `AllowList` and a supported `Plan` value.
2. Rebuilds the canonical plan from its operation, device, expected value and
   the execution policy, using the normal FORCE/write builders. This rechecks
   device ranges, octal Y numbering, value limits and every device in a span.
3. Compares all stored plan fields with that rebuilt plan, including request,
   readback request, address/length, unit and bit position. Any mismatch refuses
   the operation without even sending the RUN/STOP status read.
4. Executes the rebuilt snapshot, rather than the caller's mutable value list.
   Under the session lock it reads CPU status, sends exactly one write if RUN,
   and reads back once after an ACK. A CPU reporting STOP permits only the status
   read; no device write or CPU-mode command is sent.

The caller supplies the policy and is responsible for its authorized scope.
This validation protects the bounded API from stale, inconsistent or manually
altered plan data; it does not make arbitrary Python code trustworthy. A caller
with raw-session access could use that transport directly. Do not send raw
frames to evade the bounded helper's checks.

## Results and limits

An exact NAK is reported as `nak`. An absent/uncertain ACK, differing readback or
readback failure is `uncertain`: the write may have happened. There is no retry,
STOP/RUN transition, device restoration or persistent FORCE in this helper.
A running program may consume a command or overwrite the value before readback.

Generic `qualified_on_hardware` remains false. Existing bench observations
qualify only their recorded cases, not every device/span or physical output.
See [bench evidence](bench-qualification-20261114.md) and [SAFETY](../SAFETY.md).
Host refusal tests for forged plans do not qualify a physical machine or authorize
a live operation. An allow-list is not proof that an address is unused; review
the program and any uncertainty in its device-use analysis before assigning it.
