# Lighting control: reproducible three-station FX3U example

This example documents the local lighting control program adapted from an
existing installation. Three FX3U stations are generated from one configuration.
Each station handles 24 momentary buttons and 24 lighting zones autonomously,
without requiring a PC connection.

- [Requirements (English)](requirements.md)
- [Wymagania projektu (polski)](requirements.pl.md)

The active revision uses a 400 ms silence window, a two-second long-press
threshold, configurable zone assignments and command diagnostics. It includes
an offline correction for a lost button edge immediately after the previous
gesture is finalized; this revision has not been qualified on a PLC.
The requirements document separates implemented features from planned work.

## Implemented local behavior

- Physical `X000..X027` map to intermediate M bits before the click classifier.
- Each button classifies single, double, triple-or-more and long presses.
- Single clicks toggle configured zones, defaulting to the corresponding zone.
- Double clicks may toggle another configured list; triple remains an unused
  command. Long presses may turn off a configured list, using RST, not toggle.
  All configured zone indices must be exact Python integers (excluding bool)
  in `0..23`; invalid mappings are rejected before building any IL.
- In the included real configuration, double/long zone lists are empty and
  optional auto-off is disabled. These commands are still classified.
- `D100` counts commands and `D101` records `button_index * 10 + command_type`
  (1=single, 2=double, 3=triple-or-more, 4=long). They expose the latest command
  for polling; they are not a queue preserving every event between polls.

Each button owns ten M bits, two timers and one D register, starting at
`M(10*i)`, `T(2*i)` and `Di`. Single-click output is `Yi` in octal notation.
Device allocation is local to each PLC; three station files contain identical
executable logic with different program names.

Inter-PLC communication is a separate, unimplemented phase. The built-in
programming port uses Programming Communication and must not be treated as a
qualified 1:N Computer Link interface. Port/module selection, addressing and
network validation are out of scope for this local-control example.

## Reproduce offline

Install the toolkit from its repository root with `python -m pip install .`,
then run from that root:

```sh
python examples/lighting-case-study/generate.py --out /tmp/lighting-example
```

The script creates UTF-16 program/comment CSV, decoded IL and compiled bytes for
all three stations. It also verifies and reports the separate historical
[legacy-reference.il](legacy-reference.il) hash without generating it as the
active program. It compares every compiled image with the included
[program.il](program.il), then decodes and recompiles it byte-for-byte. It never
opens a port or deploys. An intentional configuration change requires reviewing
and updating the reference; a mismatch fails rather than silently refreshing it.

Expected for each active station (offline only, not PLC-qualified):

- 1,585 instructions; 5,954 bytes through END.
- SHA-256: `499f966aef619067b1ce6aaed78ee74eb392bc519d92442119c67d7f5429a38c`.

The historical reference also has 1,585 instructions and 5,954 bytes through END,
with SHA-256
`ca59c2fafd692761234709d19ddf42faee3a26606e8e46f2bef4b17028b930b2`.
It retains a known defect: a new press in the scan immediately after silence
window finalization can be lost. The active program changes only the three
instructions calculating the activity latch for each of 24 buttons, giving the
new edge priority over the previous window's done contact:
`active = edge OR (active AND NOT window_done)`. Allocations, instruction count
and image length stay the same. The legacy file is historical evidence, not a
recommended deployment image.

Scan tests cover all 24 inputs, all pairs of successive gestures without an
extra idle scan, and the known loss in the legacy reference. Repeated 1, 5, 10,
20 and 30 ms virtual pulses are counted as repeated edges; no debounce filter
has been added or qualified. Physical input filtering, contact bounce and scan
phase require measurements and a separate timing contract. Restart behavior
and output restoration have not been changed or qualified by this correction.

## Evidence and source adaptation

On 2026-11-14 the bytes now preserved in `legacy-reference.il` were compared
with a fresh program readback from the project's PLC2. All 5,954 bytes matched the actual program prefix
exactly (the historical `ca59…` image); the remaining bytes in its 32,000-byte
program window were `FF` padding.
This is an independent comparison with the PLC readback in addition to the
compiler's own decode/recompile check. It does not qualify the corrected
`499f…` image, which has only host encoding and scan-simulation evidence.
The complete memory image and raw wire logs are unnecessary to reproduce this public example and are not included.

`build_station.py` was extracted from the project's existing station builder.
The project-specific pinned-wheel bootstrap and its disabled, obsolete
Computer Link initialization branch were omitted. Source comments and
user-facing labels were normalized to English in that historical extraction.
The active revision applies the activity-latch correction described above.
`config.py` contains the active example configuration and empty override maps.
Private addresses, room names, host paths and credentials are excluded.
Requirements and implementation status are documented in English and Polish.

A historical physical-button click-count check was not repeated for the public
example and does not qualify every gesture,
timing, output load or restart condition. The separate
[November bench campaign](../../docs/bench-qualification-20261114.md) qualifies
selected RUN writes and a small added MOV/ADD fixture; it is not an exhaustive
acceptance test of this entire lighting program. Reading this example does not
authorize deployment to an unknown installation. STOP may reset latched outputs;
output/device-state restoration must be planned separately from program restore.
