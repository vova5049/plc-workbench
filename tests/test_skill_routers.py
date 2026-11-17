"""Offline installer checks; all destinations are isolated temporary directories."""

from pathlib import Path
import hashlib
import importlib.util
import shutil

import pytest


ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location(
    "skill_routers", ROOT / "scripts" / "install-skill-routers.py"
)
installer = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(installer)


def snapshot(directory):
    return {
        str(path.relative_to(directory)): (path.read_bytes(), path.stat().st_mtime_ns)
        for path in directory.rglob("*") if path.is_file()
    }


def test_dry_run_creates_nothing(tmp_path):
    destination = tmp_path / "absent" / "skills"
    installer.main(["--skills-dir", str(destination)])
    assert not destination.parent.exists()


def test_two_applies_preserve_original_and_helpers(tmp_path):
    destination = tmp_path / "skills"
    legacy = destination / "fx-plc-dsl"
    (legacy / "scripts").mkdir(parents=True)
    original = b"---\nname: fx-plc-dsl\n---\nOld entrypoint\n"
    (legacy / "SKILL.md").write_bytes(original)
    (legacy / "ANTI_RULES.md").write_bytes(b"Keep this restriction\n")
    (legacy / "scripts" / "helper.py").write_bytes(b"# Legacy helper\n")
    before = snapshot(destination)
    installer.main(["--skills-dir", str(destination)])
    assert snapshot(destination) == before
    installer.main(["--skills-dir", str(destination), "--apply"])
    for name in installer.SKILLS:
        entry = (destination / name / "SKILL.md").read_text()
        assert str(ROOT / "skills" / name / "SKILL.md") in entry
    sha = hashlib.sha256(original).hexdigest()
    assert (legacy / f"SKILL.md.pre-fx3u-router.{sha}.bak").read_bytes() == original
    for relative in ("fx-plc-dsl/ANTI_RULES.md", "fx-plc-dsl/scripts/helper.py"):
        path = destination / relative
        assert (path.read_bytes(), path.stat().st_mtime_ns) == before[relative]
    first = snapshot(destination)
    installer.main(["--skills-dir", str(destination), "--apply"])
    assert snapshot(destination) == first


def test_incomplete_third_skill_refuses_before_any_write(tmp_path):
    checkout = tmp_path / "checkout"
    checkout.mkdir()
    for relative in ("skills", "docs"):
        shutil.copytree(ROOT / relative, checkout / relative)
    for relative in ("pyproject.toml", "SAFETY.md", "src/fx3u/__init__.py"):
        target = checkout / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / relative, target)
    (checkout / "skills" / installer.SKILLS[-1] / "SKILL.md").unlink()
    destination = tmp_path / "destination"
    with pytest.raises(SystemExit) as error:
        installer.main(["--checkout", str(checkout), "--skills-dir", str(destination), "--apply"])
    assert error.value.code == 1
    assert not destination.exists()


def test_symlink_refused_without_touching_source(tmp_path):
    destination = tmp_path / "skills"
    target = destination / installer.SKILLS[0]
    target.mkdir(parents=True)
    original = tmp_path / "external.md"
    original.write_bytes(b"Do not change\n")
    (target / "SKILL.md").symlink_to(original)
    with pytest.raises(SystemExit) as error:
        installer.main(["--skills-dir", str(destination), "--apply"])
    assert error.value.code == 1
    assert original.read_bytes() == b"Do not change\n"
    assert (target / "SKILL.md").is_symlink()


def test_backup_collision_refuses_without_overwrite(tmp_path):
    destination = tmp_path / "skills"
    target = destination / installer.SKILLS[0]
    target.mkdir(parents=True)
    old = b"Legacy instructions\n"
    (target / "SKILL.md").write_bytes(old)
    sha = hashlib.sha256(old).hexdigest()
    (target / f"SKILL.md.pre-fx3u-router.{sha}.bak").write_bytes(b"Different backup\n")
    before = snapshot(destination)
    with pytest.raises(SystemExit) as error:
        installer.main(["--skills-dir", str(destination), "--apply"])
    assert error.value.code == 1
    assert snapshot(destination) == before
