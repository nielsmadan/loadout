"""A source skill or agent named like an entry a harness writes for itself is refused
where it would land on that entry: the two would overwrite each other on every run."""

from __future__ import annotations

from pathlib import Path

import pytest

import loadout
from registration import register

MISSION = "---\nname: scrutiny-feature-reviewer\ndescription: m\n---\nUsed only within missions.\n"
SYNCED = "---\nname: pdf\n---\nAccount skill.\n"


def _source(root: Path, harnesses: tuple[str, ...], kind: str) -> Path:
    root.mkdir(parents=True)
    blocks = "".join(f"\n[{harness}]\n" for harness in harnesses)
    (root / "loadout.toml").write_text(f'[[source]]\nname = "test"\npath = "."\n{blocks}')
    if kind == "agent":
        (root / "agents").mkdir()
        (root / "agents/scrutiny-feature-reviewer.md").write_text(
            "---\nname: scrutiny-feature-reviewer\ndescription: Mine.\n---\nMine.\n"
        )
    else:
        (root / "skills/synced").mkdir(parents=True)
        (root / "skills/synced/SKILL.md").write_text(
            "---\nname: synced\ndescription: Mine.\n---\nMine.\n"
        )
    return register(root)


def _owned(home: Path, kind: str) -> tuple[Path, str, str]:
    """The harness's own entry, put there first; its path, its owner, and its content."""
    if kind == "agent":
        path = home / ".factory/droids/scrutiny-feature-reviewer.md"
        path.parent.mkdir(parents=True)
        path.write_text(MISSION)
        return path, "Droid", MISSION
    path = home / ".claude/skills/synced"
    (path / "org_account/pdf").mkdir(parents=True)
    (path / "org_account/pdf/SKILL.md").write_text(SYNCED)
    return path, "Claude Code", SYNCED


def _refusal(owner: str, owned: Path, kind: str, name: str) -> str:
    return (
        f"loadout: {owner} writes {owned} itself; rename the {kind} {name!r} so the two do not "
        "overwrite each other"
    )


@pytest.mark.integration
@pytest.mark.parametrize("command", ["sync", "check"])
@pytest.mark.parametrize("kind", ["agent", "skill"])
def test_a_source_entry_named_like_a_harness_s_own_is_refused_where_it_lands(
    tmp_path: Path, fake_home: Path, capsys, command: str, kind: str
) -> None:
    _source(tmp_path / "source", ("droid" if kind == "agent" else "claude",), kind)
    owned, owner, content = _owned(fake_home, kind)
    name = "scrutiny-feature-reviewer" if kind == "agent" else "synced"

    code = loadout.main([command, "--global"])

    assert code == 3
    assert capsys.readouterr().err.splitlines()[-1] == _refusal(owner, owned, kind, name)
    written = owned if kind == "agent" else owned / "org_account/pdf/SKILL.md"
    assert written.read_text() == content


@pytest.mark.integration
@pytest.mark.parametrize(
    "case",
    [
        ("agent", "claude", ".claude/agents/scrutiny-feature-reviewer.md"),
        ("skill", "codex", ".codex/skills/synced/SKILL.md"),
    ],
)
def test_the_same_name_renders_for_a_harness_that_does_not_own_it(
    tmp_path: Path, fake_home: Path, case: tuple[str, str, str]
) -> None:
    kind, harness, rendered = case
    _source(tmp_path / "source", (harness,), kind)

    assert loadout.main(["sync", "--global"]) == 0

    assert (fake_home / rendered).read_text().endswith("Mine.\n")


@pytest.mark.migration_integration
def test_init_refuses_an_agent_it_would_render_onto_a_harness_s_own(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    owned, owner, content = _owned(fake_home, "agent")
    mine = fake_home / ".claude/agents/scrutiny-feature-reviewer.md"
    mine.parent.mkdir(parents=True)
    mine.write_text("---\nname: scrutiny-feature-reviewer\ndescription: Mine.\n---\nMine.\n")
    source = tmp_path / "agent-config"
    source.mkdir()
    init = ["init", "global", str(source), "--harness", "claude", "--harness", "droid"]

    for mode in ("--dry-run", "--yes"):
        assert loadout.main([*init, mode]) == 3
        assert capsys.readouterr().err.splitlines()[-1] == _refusal(
            owner, owned, "agent", "scrutiny-feature-reviewer"
        )

    assert owned.read_text() == content
    assert mine.read_text().endswith("Mine.\n")
    assert list(source.iterdir()) == []
