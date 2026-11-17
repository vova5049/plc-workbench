# Experimental encoding profile: provenance

The project publishes its implementation and its inferred encoding-format
summary under the repository's MIT license. The summary is
[`experimental_profile.json`](../src/fx3u/experimental_profile.json); vendor
binaries, database extracts, decompiled code and raw research records are not
part of the distributed package.

## Verified derivation

The profile was produced by this chain:

1. Synthetic instruction-list programs were prepared for calibration. A local
   product database helped select instruction names and operand combinations;
   additional tests covered device boundaries, operand classes and missing forms.
2. A private harness passed those programs to a separately installed vendor
   compiler and recorded its accepted/rejected results and generated program
   bytes. These are compiler input/output observations, not copies of internal
   opcode tables or the compiler's implementation.
3. The project's own Python reduction script selected successful observations,
   classified their input operands, and derived opcode words, operand counts,
   encoded widths and observed operand classes from the output programs.
   The script reads the observation records, not the vendor DLL or database.
4. On 2026-11-15 a source audit reran only that reduction on seven retained
   observation batches. It reproduced the distributed file **byte-for-byte**:

   ```text
   bytes: 387767
   SHA-256: d489c77efce2f89f7f8459d87ba734735bb3f36e70a86bd4590638987558af74
   schema version: 1
   generic instruction entries: 507
   bit-form entries: 18
   special-form annotations: 9
   ```

The annotations describe cases outside the generic reduction; they are not
additional claims of hardware support. The executable codec also implements
special forms separately, so its runtime mnemonic count differs from the
generic profile entry count.

The public profile contains project-defined encoding facts and classifications.
It does not retain corpus identifiers, database rows, compiler pcode records,
decompiled functions, source-program listings or complete compiled test vectors.
The private scripts, raw observations and audit hashes remain outside this
source package. The reproduction above required no vendor binary, compiler
execution, new decompilation or PLC connection. Regenerating the original
compiler observations is a separate private calibration procedure.

## Method and limits

This is **not a clean-room claim**. Earlier private research into the compiler
interface informed how the calibration harness called it, and the product
database influenced test selection. The verified distinction is between a
project-generated summary of observed format behavior and a copied vendor
table, executable or decompiled implementation.

The release scope is the project's code and this inferred format summary;
vendor research materials remain excluded. This account records technical
provenance and the chosen publication scope, not a legal opinion or a license
grant for any vendor material.

Encoding observations do not qualify instruction semantics, physical outputs,
timing, additional CPU models or recovery on a machine. See
[evidence levels](evidence.md), [extended codec](extended-codec.md) and
[SAFETY](../SAFETY.md). A privacy scan or a successful reproduction does not
establish those properties.
