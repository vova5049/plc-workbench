# Lighting control - project requirements

**Status:** Phase 1 is implemented as a reproducible example. Inter-PLC communication is planned but not implemented.

## 1. Objective and scope

Develop lighting control software for three Mitsubishi FX3U PLCs. Each controller must operate local lighting independently of the PC and other PLCs. The architecture should permit a fourth station and later inter-controller communication without redesigning the local button and zone logic.

Toolkit components and agent instructions must remain modular and reusable.

## 2. Hardware and signals

- `X` inputs: momentary pushbuttons.
- `Y` outputs: relays assigned to lighting zones.
- `M` bits: an abstraction layer between physical inputs and program logic.
- Configurable button-to-zone assignments without hard-coding physical device addresses into gesture classification.
- FX3U `X` and `Y` device numbers use octal notation.

Current example configuration: `PLC1`, `PLC2`, `PLC3`; 24 buttons and 24 zones per controller. This does not constitute a validated wiring map for arbitrary installations.

## 3. Local control logic

Each button must generate four distinguishable event types:

| Event | Required behavior |
|---|---|
| Single press | Toggle a configured list of zones; the corresponding zone is the default |
| Double press | Generate a distinct command with an optional zone list |
| Triple or subsequent presses | Generate a distinct command, with no output action assigned in the current configuration |
| Long press | Generate a distinct command, optionally switching off configured zones using `RST` |

Parameters of the current implementation: **400 ms** silence window and **2 s** long-press threshold. Actual timing depends on timer resolution and PLC scan behavior and has not been qualified on every device.

Additional requirements:
- Define `X -> M` and `button/event -> Y` mappings in configuration.
- Generate one-scan command pulses for the local zone-control logic.
- Optional double/long zone lists are empty in the included configuration; automatic shutoff is disabled.
- `D100` counts commands. `D101` records the most recent command as `button_index * 10 + command_code`, with codes 1/2/3/4 for single/double/triple-or-more/long. These registers provide last-event diagnostics, **not an event queue**.

## 4. Phase 1 - autonomous PLC programs

Generate and validate three PLC programs from one configuration, with no network requirement. Each program must control only its local zones and function without a PC.

Public example verification criteria:
1. Generate source and CSV for all three stations.
2. Match the version-controlled `program.il` reference.
3. Decode and recompile to byte-identical output.
4. The current configuration produces **1,585 instructions**, **5,954 bytes**, and SHA-256 `499f966aef619067b1ce6aaed78ee74eb392bc519d92442119c67d7f5429a38c` for each station.
5. Hardware readback evidence covers only `legacy-reference.il` (SHA-256 `ca59c2fa…28b930b2`) on **PLC2** in the recorded trial. It does not qualify the corrected active `program.il` or establish exhaustive gesture and physical-output testing across all three stations.

Implementation and reproduction: [README.md](README.md).

## 5. Phase 2 - inter-PLC communication

Target requirements:
- Send selected lighting commands or zone requests to other PLCs from local logic and from a PC application written in Python.
- Address stations explicitly and define acknowledgement, timeout, retry and communication-loss behavior.
- Preserve independent local operation when communication is unavailable.
- Separate the transport, protocol, command model and application layers.

**Prerequisite:** identify exact CPU variants, connectors, required expansion hardware and supported protocols. The FX3U built-in round programming port with the Programming Communication protocol used here **must not be assumed to provide 1:N Computer Link**. Supported node counts, RS-422 multidrop and virtual-COM operation require confirmation from the selected interface documentation and bench tests. Do not configure `D8120` based on assumptions about the built-in programming port.

Local program functionality is not evidence that PLC-to-PLC communication is implemented.

## 6. Toolkit modules and responsibilities

- [fx-plc-dsl](../../skills/fx-plc-dsl/SKILL.md): logic authoring, device declarations, I/O mappings and reusable components.
- [fx3u-toolchain](../../skills/fx3u-toolchain/SKILL.md): compilation, disassembly, simulation, image validation and encoding evidence.
- [fx-plc-programming-comm](../../skills/fx-plc-programming-comm/SKILL.md): communication through the qualified programming interface, inspection and explicitly authorized writes.

This division reflects the existing toolkit skills. Network functionality requires a separate specification and hardware qualification; unverified promises must not be added to the existing skills.

## 7. Safety and evidence boundaries

The example program drives physical `Y` outputs. Before running it on an installation, validate I/O mapping, device allocation, output loads and STOP/RUN behavior. CPU program writes require explicit authorization, a current program backup and a plan for restoring machine state.

Phase 2 remains unimplemented. Evidence levels and limitations are documented in the [case study](README.md), [evidence](../../docs/evidence.md) and [SAFETY](../../SAFETY.md).

The corrected active example is host/simulator-tested only; the historical PLC evidence applies to `legacy-reference.il`, not this changed program.
