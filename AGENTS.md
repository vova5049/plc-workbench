# FX3U toolkit agent instructions

The canonical toolkit implementation and these skill instructions live together
in this checkout. Read the skill relevant to the task before using older global
FX skill entrypoints:

- [skills/fx3u-toolchain/SKILL.md](skills/fx3u-toolchain/SKILL.md): CLI, codecs,
  evidence and authorized deployment.
- [skills/fx-plc-dsl/SKILL.md](skills/fx-plc-dsl/SKILL.md): ladder/IL authoring
  using `fx3u.dsl` and direct compilation.
- [skills/fx-plc-programming-comm/SKILL.md](skills/fx-plc-programming-comm/SKILL.md):
  `fx3u.client` transport, inspection and bounded device edits.

Legacy skill manuals, supporting files and ANTI_RULES remain reference material.
Use applicable project constraints and cited manual facts; select current APIs
and workflows from the repository skills. Do not run legacy compiler/transport
wrappers just because an old global entrypoint names them. Keep Computer Link
and MODBUS separate from Programming Communication.

Read [SAFETY.md](SAFETY.md) before live operations. Skill selection or installation
does not authorize hardware access, writes or motion tests. Preserve CPU/header
refusals and distinguish host, encoding and actual PLC evidence. Keep private
programs, captures and device dumps outside this public repository.

Keep public documentation and commit messages focused on engineering work.
Convert informal requests into structured requirements with explicit scope,
hardware assumptions, measurable acceptance criteria and implementation status.
Do not commit raw conversations or prompts as project specifications. Distinguish
implemented local control from unqualified PLC networking and planned features.
Do not add session links, model/provider names or automated attribution trailers.
Preserve legitimate contributor credits and the neutral development approach.
