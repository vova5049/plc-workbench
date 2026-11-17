# Device-reference analysis

`fx3u xref IMAGE DEVICE` returns a JSON report containing `device`, `hits`,
`uncertainties`, and `complete`. Select `--experimental` when the image requires
the extended decoder. The CSV inspection command uses the same report:
`python -m fx3u.dsl.gxworks_csv PROGRAM.csv --xref M15`.

This is a conservative inspection aid. It does not allocate unused devices,
validate a complete PLC program, or authorize writes to a connected controller.
An empty `hits` list must be considered together with `uncertainties`; it is
never a physical-machine commissioning decision.

## Resolved footprints

The current subset resolves direct operands, explicit digit-designated bit
groups, the contact/coil forms used by the lighting example, 16-bit MOV,
INC/DEC/ROL/ROR, 16-bit LD/AND/OR comparisons, DMOV register pairs for D/R,
constant-length BMOV D/R register blocks, ordered static same-family ZRST endpoints, and
constant-length SFTL bit ranges. Pulse forms of these application instructions
have the same footprint. X/Y addresses use octal numbering throughout.

For example, `MOV K4M0 D20` references M0 through M15 and D20.
`DMOV D10 D100` references D10–D11 and D100–D101.
`BMOV D10 D100 K3` references D10–D12 and D100–D102.
`ZRST Y7 Y12` includes Y7, Y10, Y11, and Y12.

The range rules follow the FX Series Programming Manual II descriptions for
MOV/DMOV (FNC 12, section 5.2.3), BMOV (FNC 15, section 5.2.6), ZRST (FNC 40,
section 5.5.1), and SFTL (FNC 35, section 5.4.6). They describe selected
instruction footprints, not qualification of every FX3U instruction or CPU.

## Uncertainty is part of the result

Each hit identifies the zero-based instruction and operand index, mnemonic,
operands, range endpoints, kind, and `certainty`. Indexed operands produce a
`potential` hit for any queried address in their device family: `MOV D0Z0 D100`
may access D500, depending on Z0. The explicit index register is also reported
as a reference. Unknown runtime offsets make `complete` false.

Unsupported instruction semantics, unsupported operand forms, dynamic BMOV
counts, BMOV bit-group/other-family block layouts, and unresolved counter/device-family
widths also make `complete` false. Recognized direct anchors can still be
reported alongside those uncertainties. Logical high-speed counters are not
expanded into adjacent C devices. The analysis never infers that a missing
reference in an incomplete report means an address is available.

`complete: true` means that all footprints in the supplied instruction list
were resolved by this subset. It does not cover interrupts, external writers,
retention settings, application parameters, a different deployed image, or
physical process state.

The CSV `devices()` helper and `--devices` count explicit operand anchors,
including K-group bases and index registers. This is deliberately different
from the full range report produced by `xref`. CSV parsing remains an
inspection-oriented importer. Compilation uses strict structural parsing and
compares every source step against the selected encoder map before writing
output; see [CSV import boundaries](csv-import.md). The CSV writer quotes fields
and preserves embedded quotes, tabs and newlines.
