---
name: fx-plc-dsl
description: Write and modify Mitsubishi FX3U ladder logic using fx3u.dsl, named devices and reusable rung patterns; compile directly with fx3u-toolkit or export compatible IL CSV. Use for logic authoring, not for proving hardware behavior or motion commissioning.
---

# FX3U ladder authoring

API baseline: `fx3u-toolkit 0.1.0.dev3`, the tested minimum for these instructions.
Check `importlib.metadata.version('fx3u-toolkit')` in the project environment.
Earlier dev0 does not enforce the declaration/alias contract below. Keep the
versioned package and its repository skills together in consumer projects.

Use `from fx3u.dsl import Prog, write_utf16`. This is a Python rung/IL builder,
not a graphical ladder editor or a generic IEC compiler. Direct compilation runs
without GX Works2; a compatible CSV is only an intermediate/export format.
Read the installed builder's signatures before extending examples.

## Plan the logic before generating it

Specify the actual FX CPU, I/O mapping, named internal devices, timer/counter
numbers, expected scan behavior and test cases. Keep device ranges explicit and
non-overlapping. All PLC devices are global. `p.module()` changes allocation
ranges/metadata, not symbol scope: names still must be unique across modules.
`p.dev(name, device, comment=None)` reserves a device globally. Redeclaring the
same name/address is idempotent if it does not change the comment; it preserves
the first spelling, module and metadata. Retargeting a name or declaring a second
name for the same address is rejected before changing the declaration tables.
Direct-device comparisons normalize case and leading zeros, with octal X/Y.
Use `p.alias(new_name, existing_name)` for intentional sharing: the target must
already exist, the alias name must be new, and its physical comment stays owned
by the original declaration. `p.alloc()` rejects an existing name before consuming
its range and skips addresses reserved in any module. These declaration checks
are not a proof that grouped, indexed or multiword instructions leave an address
unused; inspect the actual program's operand usage separately.
For button-based logic, map physical X inputs to intermediate M bits so a wiring
change does not rewrite every rung; keep the physical output mapping explicit.

Use the target CPU manual for instruction semantics and hardware requirements.
Do not invent step sizes, timer bases, HSC input assignments or port settings.
The toolkit's instruction catalog is an index of evidence, not a replacement for
the manual. X and Y numbers are octal (`X7` then `X10`; no `X8` or `Y9`). Other
ranges and special relay meanings must be checked for the concrete instruction.

For an existing program, make a delta plan and preserve unaffected decoded IL.
Separate a change in encoding from a change in scan semantics.

## Small internal-only example

The following script writes source CSV; it does not open a port or deploy.

```python
from pathlib import Path
from fx3u.dsl import Prog, write_utf16

p = Prog()
p.dev("condition", "M0", "Internal condition")
p.dev("edge", "M1", "One-scan edge")
p.dev("count", "D0", "Edge count")
p.pulse("condition", "edge")
p.LD("edge").INC("count")
p.END()
program_csv, comments_csv = p.compile("MAIN")
Path("out").mkdir(exist_ok=True)
write_utf16("out/MAIN.csv", program_csv)
write_utf16("out/MAIN_COMMENT.csv", comments_csv)
```

```sh
fx3u compile-csv out/MAIN.csv out/program.bin
fx3u disassemble out/program.bin out/decoded.il
fx3u compile out/decoded.il out/roundtrip.bin
```

Compare the two binary files, inspect devices/step map, and check expected logic
in the scan simulator where supported. This example uses only M/D; those addresses
are illustrative, never presumed free in a connected PLC.

The CSV output is UTF-16LE with BOM, tab-separated quoted fields and separate
device comments. Use `write_utf16`, not a generic UTF-8/comma CSV writer.
Keep the program name at most eight characters. `Prog.compile()` returns two
strings; it does not deploy or prove that the chosen direct encoder accepts all
DSL output. Use `compile-csv --experimental` for independently calibrated forms
outside the baseline subset, then the matching experimental decoder/deploy mode.

## Reusable patterns

- `self_hold(out, set_cond, reset_cond)` builds a latch with reset condition.
- `on_delay(cond, timer, preset)` expects an explicit timer number/preset.
- `pulse(cond, bit)` creates an instruction-local rising-edge pulse.
- `in_remap(m_out, x_in)` maps a physical input to an internal bit.
- `toggle(out, pulse)` expects a one-scan command, not a held button.
- `click_button(btn, win="K4", long_press="K8")` must be inside an allocation
  module and expects an already-remapped M bit. It returns named command symbols
  for `single`, `double`, `triple`, `long`. Reserve sufficient M/T/D ranges and
  resolve names through the builder's symbol map before inspecting raw devices.
  Its default timing is a starting hypothesis: verify timer bases and measure the
  actual button behavior. Do not claim a debounce/timing result from encoding.
  Dev2 gives a fresh rising edge priority over an expired silence timer. Its
  corrected lighting example has a new offline-tested hash; the unchanged
  legacy reference carries the historical PLC evidence. No software debounce
  or restart/retention policy is implied by that correction.

Use `p.i(mnemonic, *operands)` for an instruction whose convenience method is not
present, but only after confirming compiler support and the intended semantics.
Do not add guessed opcodes or alter an unknown-instruction rejection to pass a
build. [Extended codec](../../docs/extended-codec.md) describes limits;
[scan simulator](../../docs/scan-simulator.md) describes modeled semantics.

## Deliver and qualify

Keep source, device allocation map, decoded comparison, binary hash and expected
results together. Classify the result as host/encoding, simulated, or specific
PLC-tested cases. All hardware writes require explicit human authorization and
a backup/recovery plan; use the `fx3u-toolchain` workflow and toolkit `SAFETY.md`.
STOP can reset Y outputs; a ladder rung can overwrite a one-shot FORCE. A successful
compile never authorizes output, motion, high-speed or module tests.
