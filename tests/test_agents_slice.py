"""The agents slice end to end: rendered to five harnesses at both scopes, retired,
ignored and guarded like every other slice (S-26)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout.emit import collect_notices, render_global, render_project

AGENT = (
    "---\ndescription: Reviews a diff.\nmodel:\n  claude: sonnet\neffort: max\n---\n\nReview it.\n"
)
HARNESSES = ("claude", "codex", "droid", "opencode", "pi")
GLOBAL_FILES = (
    ".claude/agents/reviewer.md",
    ".codex/agents/reviewer.toml",
    ".factory/droids/reviewer.md",
    ".config/opencode/agents/reviewer.md",
    ".pi/agent/agents/reviewer.md",
)
PROJECT_FILES = (
    ".claude/agents/reviewer.md",
    ".codex/agents/reviewer.toml",
    ".factory/droids/reviewer.md",
    ".opencode/agents/reviewer.md",
    ".pi/agents/reviewer.md",
)


def _global_source(root: Path) -> Path:
    (root / "agents").mkdir(parents=True)
    blocks = "".join(f"\n[{harness}]\n" for harness in HARNESSES)
    (root / "loadout.toml").write_text(f'[[source]]\nname = "test"\npath = "."\n{blocks}')
    (root / "agents" / "reviewer.md").write_text(AGENT)
    return root


def _project(root: Path) -> Path:
    (root / "loadout" / "agents").mkdir(parents=True)
    quoted = ", ".join(f'"{h}"' for h in HARNESSES)
    (root / "loadout" / "config.toml").write_text(f"harnesses = [{quoted}]\n")
    (root / "loadout" / "agents" / "reviewer.md").write_text(AGENT)
    return root


def test_a_global_agent_renders_to_every_harness(tmp_path: Path, fake_home: Path) -> None:
    outputs = render_global(_global_source(tmp_path / "source"))

    assert {str(p.relative_to(fake_home)) for p in outputs if p.stem == "reviewer"} == set(
        GLOBAL_FILES
    )


def test_a_project_agent_renders_to_every_harness(tmp_path: Path) -> None:
    root = _project(tmp_path / "p")

    outputs = render_project(root)

    assert {str(p.relative_to(root)) for p in outputs} >= set(PROJECT_FILES)


@pytest.mark.integration
def test_sync_notes_an_effort_a_harness_cannot_take(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")

    assert loadout.main(["sync", "--root", str(root)]) == 0

    out = capsys.readouterr().out
    assert (
        "note: droid.agents: agent 'reviewer': droid has no effort 'max', so 'high', the "
        "nearest lower level, is written"
    ) in out
    assert "note: opencode.agents: agent 'reviewer': OpenCode agents have no effort field" in out


@pytest.mark.integration
def test_removing_a_global_agent_retires_it_everywhere(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    root = _global_source(tmp_path / "source")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    assert all((fake_home / name).is_file() for name in GLOBAL_FILES)

    (root / "agents" / "reviewer.md").unlink()
    capsys.readouterr()
    assert loadout.main(["sync", "--root", str(root)]) == 0

    out = capsys.readouterr().out
    assert sorted(line for line in out.splitlines() if line.startswith("retired")) == sorted(
        f"retired {fake_home / name}" for name in GLOBAL_FILES
    )


@pytest.mark.integration
def test_project_agents_are_ignored_by_file_and_unignored_on_retirement(
    tmp_path: Path, capsys
) -> None:
    root = _project(tmp_path / "p")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    ignored = (root / ".gitignore").read_text().splitlines()
    assert {f"/{name}" for name in PROJECT_FILES} <= set(ignored)

    (root / "loadout" / "agents" / "reviewer.md").unlink()
    assert loadout.main(["sync", "--root", str(root)]) == 0

    remaining = set((root / ".gitignore").read_text().splitlines())
    assert {f"/{name}" for name in PROJECT_FILES} & remaining == set()
    assert [name for name in PROJECT_FILES if (root / name).exists()] == []


@pytest.mark.integration
def test_an_agent_loadout_did_not_write_is_unmanaged(tmp_path: Path, capsys) -> None:
    root = _project(tmp_path / "p")
    assert loadout.main(["sync", "--root", str(root)]) == 0
    foreign = root / ".claude" / "agents" / "helper.md"
    foreign.write_text("---\nname: helper\ndescription: Helps.\n---\nHelp.\n")
    capsys.readouterr()

    assert loadout.main(["check", "--root", str(root), "--json"]) == 1

    drift = json.loads(capsys.readouterr().out)["drift"]
    assert drift == [{"path": str(foreign), "kind": "unmanaged"}]


def test_a_source_whose_use_omits_agents_says_its_agents_are_not_rendered(
    tmp_path: Path,
) -> None:
    root = _global_source(tmp_path / "source")
    manifest = root / "loadout.toml"
    manifest.write_text(manifest.read_text().replace('path = "."', 'path = "."\nuse = ["skills"]'))

    notes = [notice.render() for notice in collect_notices(root)]

    assert notes == [
        (
            f"test.agents: {root / 'agents'} holds agents, but source 'test' does not list "
            '"agents" in `use`, so none of them is rendered'
        )
    ]
