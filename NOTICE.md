# Notice

- **Trademarks.** Mitsubishi Electric, MELSEC, FX3U and GX Works2 are trademarks of Mitsubishi
  Electric Corporation. They are used here only to describe compatibility. This project is
  independent and is not affiliated with, endorsed or supported by Mitsubishi Electric.
- **No vendor material.** This repository contains no Mitsubishi software, firmware, manuals or
  excerpts of them. The instruction atlas lists mnemonic/FNC numbers and availability marks
  extracted from Mitsubishi's public product web page (source URL and hash recorded in
  `capabilities.json`); descriptive text was not copied. Consult the official manuals for semantics.
- **Interoperability.** Protocol knowledge comes from observing the communication of a PLC owned
  by the authors, for the purpose of interoperability.
- **Encoding profile.** The experimental profile is a project-generated summary
  of compiler input/output observations: opcode words, operand widths and
  observed operand classes. A local product database helped select synthetic
  test inputs; its rows and the compiler's internal tables are not distributed.
  The retained observation records reproduce the published profile byte-for-byte
  through the project's own reduction script. This is not a clean-room claim.
  See [profile provenance](docs/provenance.md) for the verified chain and limits.
- **Acknowledgements.** The device address map for the programming protocol was first taken from
  [fxplc](https://github.com/KrystianD/fxplc) (MIT) and then verified on hardware. No fxplc code is included.
