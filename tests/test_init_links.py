"""`init project` reports a link project sync would refuse instead of walking it, and never
plans to retire a path outside the harness folders it adopts from (review 3, 1.3)."""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

import loadout
import loadout.init_workflow as workflow
from loadout.errors import LoadoutError

pytestmark = pytest.mark.migration_integration

SKILL = "---\nname: x\ndescription: d\n---\nX\n"


def _repo(root: Path) -> Path:
    root.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    config = root / ".git/config"
    config.write_text(config.read_text() + "[http]\n\textraheader = TOKEN-IN-GIT-CONFIG\n")
    (root / ".claude/skills/x").mkdir(parents=True)
    (root / ".claude/skills/x/SKILL.md").write_text(SKILL)
    return root


def _preview(root: Path, capsys) -> tuple[int, str]:
    code = loadout.main(
        ["init", "project", "--root", str(root), "--harness", "claude", "--dry-run"]
    )
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_a_skill_link_into_the_git_dir_is_reported_not_walked(tmp_path: Path, capsys) -> None:
    root = _repo(tmp_path / "p")
    link = root / ".claude/skills/x/g"
    link.symlink_to("../../../.git")

    code, out = _preview(root, capsys)

    assert code == 2
    assert f"{link} links to {root / '.git'}, inside the repository's .git" in out
    # The preview is what a dry run prints; the git dir's credentials never reach it.
    assert "TOKEN-IN-GIT-CONFIG" not in out
    assert [line for line in out.splitlines() if "Retire original" in line] == []


def test_a_linked_folder_inside_a_harness_tree_is_reported_not_walked(
    tmp_path: Path, capsys
) -> None:
    root = _repo(tmp_path / "p")
    (root / "tools").mkdir()
    (root / "tools/run.sh").write_text("echo run\n")
    link = root / ".claude/skills/x/scripts"
    link.symlink_to("../../../tools", target_is_directory=True)

    code, out = _preview(root, capsys)

    assert code == 2
    assert f"{link} links to the folder {root / 'tools'}" in out
    assert [line for line in out.splitlines() if "Retire original" in line] == []


def test_a_linked_file_inside_a_harness_tree_is_reported(tmp_path: Path, capsys) -> None:
    """Sync never writes through it, so the generated copy could not replace it."""
    root = _repo(tmp_path / "p")
    (root / "tools").mkdir()
    (root / "tools/run.sh").write_text("echo run\n")
    link = root / ".claude/skills/x/run.sh"
    link.symlink_to("../../../tools/run.sh")

    code, out = _preview(root, capsys)

    assert code == 2
    assert f"{link} links to the file {root / 'tools/run.sh'}" in out
    assert [line for line in out.splitlines() if "Retire original" in line] == []


def test_a_preview_refusal_names_the_project_rather_than_scratch(tmp_path: Path, capsys) -> None:
    root = _repo(tmp_path / "p")
    (root / "docs").mkdir()
    (root / "docs/rules.md").write_text("Rules.\n")
    (root / "CLAUDE.md").symlink_to("docs/rules.md")

    code, out = _preview(root, capsys)

    assert code == 3
    assert f"loadout: refusing to write {root / 'CLAUDE.md'} -> {root / 'docs/rules.md'}" in out


def test_an_existing_source_whose_loadout_folder_is_a_link_is_an_issue(
    tmp_path: Path, capsys
) -> None:
    """Review 4, batch 2.7: init adopted it as needing no migration, and sync refused it."""
    root = tmp_path / "p"
    root.mkdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    (elsewhere / "config.toml").write_text('harnesses = ["claude"]\n')
    (root / "loadout").symlink_to(elsewhere)

    code = loadout.main(["init", "project", "--root", str(root), "--harness", "claude", "--yes"])

    out = "".join(capsys.readouterr())
    assert code == 2
    assert f"{root / 'loadout'} is a link; loadout/ must be a real folder" in out


def test_a_skill_link_into_the_git_dir_in_another_case_is_reported(tmp_path: Path, capsys) -> None:
    """Review 4, batch 4.1: on a case-insensitive disk `.GIT` is the git dir."""
    root = _repo(tmp_path / "p")
    if not (root / ".GIT").exists():
        pytest.skip("the file system is case-sensitive")
    link = root / ".claude/skills/x/g"
    link.symlink_to("../../../.GIT")

    code, out = _preview(root, capsys)

    assert code == 2
    assert f"{link} links to {root / '.GIT'}, inside the repository's .git" in out


@pytest.mark.parametrize(("name", "code"), [("run.sh", "linked-file"), ("tools", "linked-folder")])
def test_a_link_inside_a_harness_tree_is_named_by_what_it_links_to(
    tmp_path: Path, capsys, name: str, code: str
) -> None:
    root = _repo(tmp_path / "p")
    (root / "tools").mkdir()
    (root / "tools/run.sh").write_text("echo run\n")
    link = root / ".claude/skills/x" / name
    link.symlink_to(f"../../../tools/{'run.sh' if name == 'run.sh' else ''}".rstrip("/"))

    assert (
        loadout.main(
            ["init", "project", "--root", str(root), "--harness", "claude", "--dry-run", "--json"]
        )
        == 2
    )

    issues = json.loads(capsys.readouterr().out)["issues"]
    assert [(issue["code"], issue["paths"]) for issue in issues] == [(code, [str(link)])]


def test_an_output_linked_to_a_folder_is_refused_before_the_render(tmp_path: Path, capsys) -> None:
    """Review 4, batch 4.1: the scratch copy skips what is not a file, so only the check on
    the project itself sees this link; the two checks had only been tested together."""
    root = _repo(tmp_path / "p")
    (root / "docs").mkdir()
    (root / "docs/guide.md").write_text("guide\n")
    (root / "AGENTS.md").symlink_to("docs")
    # Instructions to adopt, so the render writes AGENTS.md.
    (root / "CLAUDE.md").write_text("# Project\n")

    harnesses = ["--harness", "claude", "--harness", "codex"]
    code = loadout.main(["init", "project", "--root", str(root), *harnesses, "--dry-run"])
    captured = capsys.readouterr()
    out = captured.out + captured.err

    assert code == 3
    assert f"refusing to write {root / 'AGENTS.md'} -> {root / 'docs'}, a link" in out


def test_a_refusal_from_the_scratch_render_names_the_project(
    tmp_path: Path, capsys, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path / "p")

    def refusing(plan: object, profile: str, scratch: Path, real: Path) -> object:
        raise LoadoutError(f"cannot render {scratch / 'AGENTS.md'}")

    monkeypatch.setattr(workflow, "_planned_render", refusing)

    code, out = _preview(root, capsys)

    assert code == 3
    assert f"loadout: cannot render {root / 'AGENTS.md'}\n" in out
