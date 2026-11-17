#!/usr/bin/env python3
"""Route shared skill entrypoints to this checkout; dry-run unless --apply."""

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import tempfile


SKILLS = ("fx3u-toolchain", "fx-plc-dsl", "fx-plc-programming-comm")
MARKER = "<!-- fx3u-toolkit skill router v1 -->"


def sources(checkout):
    for required in ("pyproject.toml", "SAFETY.md", "src/fx3u/__init__.py"):
        if not (checkout / required).is_file():
            raise ValueError(f"Incomplete toolkit checkout: {required}")
    result = {}
    for name in SKILLS:
        path = checkout / "skills" / name / "SKILL.md"
        text = path.read_text(encoding="utf-8")
        if not text.startswith("---\n") or "\n---\n" not in text[4:]:
            raise ValueError(f"Missing skill frontmatter: {path}")
        header = text.split("\n---\n", 1)[0]
        if f"\nname: {name}\n" not in header + "\n":
            raise ValueError(f"Wrong skill name: {path}")
        match = re.search(r"^description: (.+)$", header, re.MULTILINE)
        if not match or not match.group(1).strip():
            raise ValueError(f"Missing skill description: {path}")
        for link in re.findall(r"\[[^\]]*\]\(([^)]+)\)", text):
            if "://" in link or link.startswith("#"):
                continue
            target = (path.parent / link.split("#", 1)[0]).resolve()
            if not target.is_relative_to(checkout) or not target.is_file():
                raise ValueError(f"Missing or external skill reference: {path}: {link}")
        result[name] = (path, match.group(1))
    return result


def router(name, path, description):
    return (
        f"---\nname: {name}\ndescription: {json.dumps(description)}\n---\n\n"
        f"{MARKER}\n\n"
        f"Read and follow the canonical skill at `{path}` before doing this task.\n"
        "Resolve its relative links from that canonical skill directory.\n"
        "If the checkout is unavailable, stop and report the missing source; do not\n"
        "fall back to legacy compiler or transport wrappers.\n\n"
        "Adjacent legacy files, manuals and ANTI_RULES are preserved as references.\n"
        "Use relevant project constraints and manual facts; the repository skill\n"
        "selects the current toolkit APIs and workflow. This router grants no\n"
        "authorization for PLC access or writes.\n"
    ).encode("utf-8")


def prepare(checkout, skills_dir):
    if skills_dir.is_relative_to(checkout):
        raise ValueError("Destination must be outside the toolkit checkout")
    actions = []
    for name, (source, description) in sources(checkout).items():
        directory = skills_dir / name
        target = directory / "SKILL.md"
        if directory.is_symlink() or target.is_symlink():
            raise ValueError(f"Refusing a symlink skill entrypoint: {target}")
        if directory.exists() and not directory.is_dir():
            raise ValueError(f"Not a directory: {directory}")
        if target.exists() and not target.is_file():
            raise ValueError(f"Not a regular skill file: {target}")
        content = router(name, source, description)
        old = target.read_bytes() if target.exists() else None
        backup = None
        if old is not None and old != content:
            sha = hashlib.sha256(old).hexdigest()
            backup = directory / f"SKILL.md.pre-fx3u-router.{sha}.bak"
            if backup.is_symlink() or (backup.exists() and backup.read_bytes() != old):
                raise ValueError(f"Backup collision: {backup}")
        actions.append((target, content, old, backup))
    return actions


def apply(actions):
    for target, content, old, backup in actions:
        if old == content:
            continue
        if target.parent.is_symlink() or target.is_symlink():
            raise ValueError(f"Entrypoint changed after preflight: {target}")
        current = target.read_bytes() if target.exists() else None
        if current != old:
            raise ValueError(f"Entrypoint changed after preflight: {target}")
        target.parent.mkdir(parents=True, exist_ok=True)
        if backup is not None and not backup.exists():
            with backup.open("xb") as handle:
                handle.write(old)
            os.chmod(backup, stat.S_IMODE(target.stat().st_mode))
        mode = stat.S_IMODE(target.stat().st_mode) if old is not None else 0o644
        temp_name = None
        try:
            with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as handle:
                temp_name = handle.name
                handle.write(content)
            os.chmod(temp_name, mode)
            os.replace(temp_name, target)
        finally:
            if temp_name is not None and Path(temp_name).exists():
                Path(temp_name).unlink()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--checkout", type=Path, default=Path(__file__).resolve().parents[1],
                        help="Canonical toolkit checkout (default: this script's repository)")
    parser.add_argument("--skills-dir", type=Path, default=Path.home() / ".agents" / "skills",
                        help="Agent discovery directory (default: ~/.agents/skills)")
    parser.add_argument("--apply", action="store_true", help="Write routers; otherwise only report")
    args = parser.parse_args(argv)
    checkout = args.checkout.expanduser().resolve()
    skills_dir = args.skills_dir.expanduser().resolve()
    try:
        if any(character in str(checkout) for character in "\n\r`"):
            raise ValueError("Checkout path cannot contain newlines or backticks")
        actions = prepare(checkout, skills_dir)
        for target, content, old, backup in actions:
            verb = "unchanged" if old == content else "replace" if old is not None else "create"
            print(f"{verb}: {target}")
            if backup is not None:
                print(f"  preserve original: {backup}")
        if args.apply:
            apply(actions)
            print("Applied. Reload skills or start a new agent session to refresh discovery.")
        else:
            print("Dry-run only; no files changed. Pass --apply to write these entrypoints.")
    except (OSError, ValueError) as exc:
        parser.exit(1, f"install-skill-routers: {exc}\n")


if __name__ == "__main__":
    main()
