from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

import loadout
from loadout.emit import CLAUDE_IMPORT
from loadout.written import record_written, text_entry

pytestmark = pytest.mark.migration_integration

# A copy of this repository's AGENTS.md as of 2026-10-12.
MULTI_SECTION = Path(__file__).parent / "fixtures" / "adoption" / "AGENTS.md"
CONTENT = "# Rules\n\nBe brief.\n"


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        ["git", "-C", str(root), "-c", "user.email=t@example.com", "-c", "user.name=t", *args],
        check=True,
        capture_output=True,
    )


def _project(root: Path, claude: str | None, agents: str = CONTENT) -> Path:
    root.mkdir()
    (root / "AGENTS.md").write_text(agents)
    if claude is None:
        (root / "CLAUDE.md").symlink_to("AGENTS.md")
    else:
        (root / "CLAUDE.md").write_text(claude)
    _git(root, "init", "-q")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "base")
    return root


def _init(root: Path) -> int:
    harnesses = ("--harness", "claude", "--harness", "codex")
    return loadout.main(["init", "project", "--root", str(root), *harnesses, "--yes"])


@pytest.mark.parametrize(
    "claude",
    ["@AGENTS.md\n", "\n  @./AGENTS.md  \n\n", CLAUDE_IMPORT],
    ids=["bare", "relative-with-whitespace", "loadout-banner"],
)
def test_a_claude_md_importing_agents_md_is_adopted_as_the_same_document(
    tmp_path: Path, claude: str, capsys
) -> None:
    root = _project(tmp_path / "p", claude)

    assert _init(root) == 0

    assert (root / "loadout/instructions/main.md").read_text() == CONTENT
    assert (root / "CLAUDE.md").read_text() == CLAUDE_IMPORT
    assert (root / "AGENTS.md").read_text().endswith(CONTENT)
    capsys.readouterr()
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert "Up to date." in capsys.readouterr().out


def test_a_claude_md_linked_to_agents_md_is_adopted_and_stays_a_link(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p", None)

    assert _init(root) == 0

    assert (root / "loadout/instructions/main.md").read_text() == CONTENT
    assert (root / "CLAUDE.md").readlink() == Path("AGENTS.md")
    assert (root / "AGENTS.md").read_text().endswith(CONTENT)
    capsys.readouterr()
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert "Up to date." in capsys.readouterr().out
    assert loadout.main(["check", "--root", str(root)]) == 0


def test_an_import_with_other_content_still_disagrees(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p", "@AGENTS.md\n\nAlso run the linter.\n")

    assert _init(root) == 0

    assert "Not adopted, conflicting copies of instructions 'main':" in capsys.readouterr().out
    assert (root / "CLAUDE.md").read_text() == "@AGENTS.md\n\nAlso run the linter.\n"
    assert (root / "AGENTS.md").read_text() == CONTENT


def test_a_multi_section_agents_md_behind_an_import_is_adopted_whole(tmp_path: Path) -> None:
    agents = MULTI_SECTION.read_text()
    root = _project(tmp_path / "p", "@AGENTS.md\n", agents)

    assert _init(root) == 0

    assert (root / "loadout/instructions/main.md").read_text() == agents
    assert (root / "CLAUDE.md").read_text() == CLAUDE_IMPORT


def _synced_project(root: Path) -> Path:
    (root / "loadout/instructions").mkdir(parents=True)
    (root / "loadout/instructions/main.md").write_text(CONTENT)
    (root / "loadout/config.toml").write_text(
        'harnesses = ["claude", "codex"]\ninstructions = ["main"]\n'
    )
    return root


def test_sync_rewrites_a_generated_claude_md_to_the_import(tmp_path: Path) -> None:
    """A CLAUDE.md the previous rendering wrote with the full content."""
    root = _synced_project(tmp_path / "p")
    full = f"{CLAUDE_IMPORT.split(chr(10))[0]}\n\n{CONTENT}"
    (root / "CLAUDE.md").write_text(full)
    record_written(root, "default", {root / "CLAUDE.md": text_entry(root / "CLAUDE.md", full)})

    assert loadout.main(["sync", "--root", str(root)]) == 0

    assert (root / "CLAUDE.md").read_text() == CLAUDE_IMPORT
    assert (root / "AGENTS.md").read_text() == full


def test_sync_never_rewrites_a_hand_written_claude_md(tmp_path: Path, capsys) -> None:
    root = _synced_project(tmp_path / "p")
    (root / "CLAUDE.md").write_text("Hand-written.\n")

    assert loadout.main(["sync", "--root", str(root)]) == 1

    assert "CLAUDE.md was modified outside loadout" in capsys.readouterr().err
    assert (root / "CLAUDE.md").read_text() == "Hand-written.\n"
