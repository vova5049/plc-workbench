# CSV compilation boundaries

`compile-csv` imports a complete GX Works2 instruction-list export, beginning
at step 0 and ending in END. The selected encoder determines supported
instructions, operands and instruction widths. Select `--experimental` for
forms outside the conservative encoder. Unsupported forms are refused.

Compilation has two checks before any image, IL or map file is written:

1. `parse_strict` checks the seven-column header, metadata, decimal increasing
   steps, complete records and operand continuations. It is a structural parser;
   by itself it does not prove that instruction-step distances are correct.
2. `validate_compiled_steps` compares every parsed source step, including the
   first and last, against the complete map returned by the selected assembler.
   Missing instructions, unexplained gaps and incorrect instruction widths are
   refused. The lighting generator uses both checks too.

For example, `0: LD M0; 99: END` is refused: LD M0 occupies one step, so END
must start at 1. A five-step MOV followed by END at step 5 is valid. Additional
operands occupy continuation records with empty step and instruction cells;
their width comes from the encoder, rather than from counting CSV rows.

Explicit supported NOPs and labels are instructions in this comparison. The
importer does not synthesize NOPs to fill gaps or renumber source instructions.
Partial exports starting at a nonzero step and unknown export constructs are
outside this compilation interface; inspect them using `parse_lenient` or its
backward-compatible alias `parse` without treating that result as deployable.
An invalid export produced by a DSL step-size estimate is refused too; preserve
that refusal until the particular instruction's export sizing is corrected.

The step comparison establishes consistency with this encoder's map. It does
not independently qualify the encoder or a new instruction on physical hardware.
