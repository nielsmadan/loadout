from __future__ import annotations

import json
import subprocess
from dataclasses import replace
from pathlib import Path

import pytest

import loadout
from loadout.discovery import PERSONAL_ITEM
from loadout.stealth_record import (
    TargetRecord,
    empty_record,
    stealth_record_path,
    write_stealth_record,
)
from loadout.written import WrittenEntry

pytestmark = pytest.mark.migration_integration

SHARED = "Shared guidance.\n"


def _repository(root: Path, files: dict[str, str]) -> Path:
    for name, text in files.items():
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text(text, encoding="utf-8")
    for args in (("init", "-q"), ("add", "-A"), ("commit", "-qm", "base")):
        subprocess.run(
            ["git", "-C", str(root), "-c", "user.email=t@e.com", "-c", "user.name=t", *args],
            check=True,
            capture_output=True,
        )
    return root


def _init(root: Path, *flags: str) -> list[str]:
    return ["init", "project", "--root", str(root), "--harness", "claude", *flags]


def _preview(root: Path, capsys: pytest.CaptureFixture[str]) -> dict[str, object]:
    assert loadout.main(_init(root, "--dry-run", "--json")) == 0
    return json.loads(capsys.readouterr().out)


def _personal_project(tmp_path: Path) -> Path:
    root = tmp_path / "project"
    root.mkdir()
    return _repository(
        root,
        {
            "CLAUDE.md": SHARED,
            "CLAUDE.local.md": "Mine.\n",
            ".claude/skills/team/SKILL.md": "---\nname: team\ndescription: d\n---\nbody\n",
            ".claude/skills/x.local/SKILL.md": "---\nname: x\ndescription: d\n---\nbody\n",
        },
    )


def test_init_reports_personal_items_and_leaves_them_where_they_are(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = _personal_project(tmp_path)

    originals = _preview(root, capsys)["originals"]

    assert isinstance(originals, list)
    retained = {o["path"]: (o["action"], o["reason"]) for o in originals}
    for name in ("CLAUDE.local.md", ".claude/skills/x.local/SKILL.md"):
        assert retained[str(root / name)] == ("retain", PERSONAL_ITEM)


def test_init_adopts_no_personal_item_and_writes_no_ignore_line_for_one(
    tmp_path: Path,
) -> None:
    root = _personal_project(tmp_path)

    assert loadout.main(_init(root, "--yes")) == 0

    assert (root / "CLAUDE.local.md").read_text(encoding="utf-8") == "Mine.\n"
    assert (root / ".claude/skills/x.local/SKILL.md").is_file()
    assert (root / "loadout/skills/team/SKILL.md").is_file()
    assert sorted(path.name for path in (root / "loadout/skills").iterdir()) == ["team"]
    ignored = (root / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "/.claude/skills/team/" in ignored
    assert "/CLAUDE.local.md" not in ignored
    assert "/.claude/skills/x.local/" not in ignored


def test_init_in_a_repository_a_stealth_source_targets_leaves_its_files_out(
    tmp_path: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _repository(
        root,
        {
            "CLAUDE.md": SHARED,
            ".claude/skills/team/SKILL.md": "---\nname: team\ndescription: d\n---\nbody\n",
            ".claude/skills/mine/SKILL.md": "---\nname: mine\ndescription: d\n---\nbody\n",
        },
    )
    resolved = root.resolve()
    written = resolved / ".claude/skills/mine/SKILL.md"
    folder = fake_home / "stealth"
    folder.mkdir()
    record = empty_record(folder, "app", fake_home)
    entry = TargetRecord(
        common_dir=resolved / ".git",
        device=0,
        block=None,
        files={written: WrittenEntry(kind="copied", sha256="c0")},
        documents={},
        leaves={},
        pending={},
    )
    write_stealth_record(replace(record, targets={resolved: entry}))

    originals = _preview(root, capsys)["originals"]

    assert isinstance(originals, list)
    retained = {o["path"]: (o["action"], o["reason"]) for o in originals}
    assert retained[str(root / ".claude/skills/mine/SKILL.md")] == (
        "retain",
        "written by stealth source app; it keeps rendering them",
    )
    assert retained[str(root / ".claude/skills/team/SKILL.md")][0] != "retain"


def _stealth_record(fake_home: Path, root: Path, written: Path) -> None:
    folder = fake_home / "stealth"
    folder.mkdir()
    entry = TargetRecord(
        common_dir=root / ".git",
        device=0,
        block=None,
        files={written: WrittenEntry(kind="copied", sha256="c0")},
        documents={},
        leaves={},
        pending={},
    )
    write_stealth_record(replace(empty_record(folder, "app", fake_home), targets={root: entry}))


def test_init_yes_leaves_a_stealth_recorded_file_in_place(tmp_path: Path, fake_home: Path) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _repository(
        root,
        {
            "CLAUDE.md": SHARED,
            ".claude/skills/team/SKILL.md": "---\nname: team\ndescription: d\n---\nbody\n",
            ".claude/skills/mine/SKILL.md": "---\nname: mine\ndescription: d\n---\nbody\n",
        },
    )
    resolved = root.resolve()
    _stealth_record(fake_home, resolved, resolved / ".claude/skills/mine/SKILL.md")

    assert loadout.main(_init(root, "--yes")) == 0

    assert (root / ".claude/skills/mine/SKILL.md").is_file()
    assert sorted(path.name for path in (root / "loadout/skills").iterdir()) == ["team"]


def test_init_refuses_when_a_stealth_record_cannot_be_read(
    tmp_path: Path, fake_home: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    root = tmp_path / "project"
    root.mkdir()
    _repository(root, {"CLAUDE.md": SHARED})
    resolved = root.resolve()
    _stealth_record(fake_home, resolved, resolved / "x")
    stealth_record_path(fake_home / "stealth").write_text("{", encoding="utf-8")

    assert loadout.main(_init(root, "--yes")) == 3

    assert "a stealth record cannot be read" in capsys.readouterr().err
