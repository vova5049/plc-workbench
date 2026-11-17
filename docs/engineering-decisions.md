# Engineering decisions

## Scope: a workbench, with an FX3U backend today

The motivating workflow for winding machines is: understand perforation signals, reject
false or missing marks, track film position and decelerate toward a target.
It needs a versioned machine specification, independent expected results,
programming tools and observable execution. The current release supplies the
FX3U toolchain; it does not contain a complete commissioned application for winding machines.

The broader PLC Workbench direction does not rename the Python package
`fx3u-toolkit` or CLI `fx3u`. Additional backends require concrete CPU identities,
encoding/communication evidence and their own tests. FX5U and Delta DVP are
planned targets, not working backends here. Every additional target requires
exact device identification; compatibility is not inferred from similar names.

## Preserve the layers

1. **Specification:** actual I/O, units, polarities, states, timing and acceptance
   cases with their sources. An address from another machine is not a mapping.
2. **Authoring:** Python DSL produces inspectable IL/compatible CSV. Device
   allocations and global names remain explicit.
3. **Encoding:** conservative and extended codecs reject unsupported forms.
   Byte-identical re-encoding is encoding evidence, not proof of instruction timing.
4. **Execution:** a supported scan model predicts a subset offline; the qualified
   PLC backend performs explicit, authorized actions and reports observations.
5. **Process and view, planned integration:** a deterministic mechanism model
   consumes actuator outputs and produces sensor signals. A2D or 3D renderer
   displays that state; it must not invent an independent machine cycle.

A shared scenario/trace contract is the intended bridge between these layers.
There is no new universal runtime or automatic model extraction in this release.

## Independent evidence beats a convincing animation

The lighting case study retains a historical 1,585-instruction / 5,954-byte source
as [legacy-reference.il](../examples/lighting-case-study/legacy-reference.il),
checked against a recorded PLC readback. The active example fixes a lost edge
immediately after gesture finalization; its new hash is verified offline and in
the scan model. CI regenerates all three corrected stations and checks the
historical hash separately. The new program is not hardware-qualified by the old readback.
CI does not contain private memory dumps, drive a PLC or prove physical outputs.
Project requirements are maintained as structured English and Polish
specifications alongside the lighting case study. They distinguish the
implemented local-control phase from planned communication work.

For development for winding machines, start with functional 2D and signal traces: raw and
accepted marks, position, velocity, I/O transitions, pause/step/replay and fault
injection. Use the same model state for later 3D where geometry is useful.
Independent hand-calculated cases and measured traces must complement results
produced by the reference model itself.

## Keep clocks and machine state explicit

PLC scan, high-speed capture, transport polling and rendering have different
clocks. Polling a 9600 bps link does not reproduce a fast encoder input. Test the
capture/HSC path and input filtering separately from an M/D test interface.
Likewise, program bytes, retained devices, output latches and physical machine
state are different recovery responsibilities. A successful program restore is
not permission to restart equipment or force old outputs blindly.

## Conservative integrity and recovery claims

Keep CPU/header refusal guards. Full-region layout fingerprint and dynamic
sum16 validation are separate. Deploy checks changed blocks and the complete
candidate in confirmed STOP before RUN; the default lab path verifies again
after RUN. The leave-stopped path defers the dynamic checksum until RUN. Failed recovery makes one best-effort STOP attempt
and then independently reports STOP/RUN/unknown; a broken link cannot establish
a safe mode. See [extended codec](extended-codec.md) and [SAFETY](../SAFETY.md).

## Small source package and reviewable release artifacts

Keep raw wire logs, program-window backups, credentials and private calibration
material outside public source history. Ignore rules reduce accidental adds;
they do not clean tracked files or replace a privacy scan. Synthetic binary
fixtures need explicit, file-specific review before an ignore exception is added.
The public example is text source and generates its binary artifacts offline.
Host CI tests the source and example without hardware or publication privileges.
