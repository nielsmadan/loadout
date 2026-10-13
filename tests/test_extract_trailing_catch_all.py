"""A hand-written bash map may put its `*` catch-all after other entries.

OpenCode and Pi are last-match-wins, so an entry before the `*` never applies.
Extraction reads the `*` as `[shell] default`, drops the entries it shadows and
names each one. Keeping them would render them after the `*`, where they would
start to apply: a loosening when one is an allow.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

import loadout
from loadout.extract import extract
from loadout.permissions.renderers import render_opencode, render_pi
from loadout.permissions.rules import Rules

BASH = {"echo *": "allow", "echo": "allow", "*": "ask", "rm *": "deny", "rm": "deny"}
DOCUMENTS: dict[str, dict[str, Any]] = {
    "opencode": {"permission": {"bash": BASH}},
    "pi": {"permission": {"*": "allow", "bash": BASH, "mcp": {"*": "ask"}}},
}
RENDERERS = {"opencode": render_opencode, "pi": render_pi}


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_a_trailing_catch_all_becomes_the_default_and_names_what_it_shadowed(name: str) -> None:
    extraction = extract(name, DOCUMENTS[name])

    assert extraction.rules == Rules(deny=("rm",))
    assert [note.detail for note in extraction.notes] == [
        f"{name}: {pattern!r} (allow) comes before the '*' catch-all, so it never applies "
        "and will be dropped"
        for pattern in ("echo *", "echo")
    ]


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_the_rendered_map_keeps_the_effective_policy(name: str) -> None:
    """`echo` asked before (the `*` came after it) and asks after; `rm` still denies."""
    rules = extract(name, DOCUMENTS[name]).rules

    bash = RENDERERS[name](rules, {})["permission"]["bash"]

    assert bash == {"*": "ask", "rm": "deny", "rm *": "deny"}


@pytest.mark.parametrize("name", sorted(DOCUMENTS))
def test_a_trailing_catch_all_carries_its_own_verdict(name: str) -> None:
    document = json.loads(
        json.dumps(DOCUMENTS[name]).replace('"*": "ask", "rm', '"*": "deny", "rm')
    )

    assert extract(name, document).rules.default == "deny"


def _opencode_init(fake_home: Path, tmp_path: Path, scope: str) -> tuple[int, Path]:
    (fake_home / ".gitconfig").write_text(
        "[user]\nname = Fixture\nemail = fixture@example.invalid\n"
    )
    document = json.dumps({"permission": {"bash": BASH}})
    if scope == "global":
        config = fake_home / ".config" / "opencode"
        config.mkdir(parents=True)
        (config / "opencode.json").write_text(document)
        source = tmp_path / "source"
        source.mkdir()
        argv = ["init", "global", str(source), "--harness", "opencode", "--yes"]
        return loadout.main(argv), source / "loadout"
    project = tmp_path / "project"
    project.mkdir()
    (project / "opencode.json").write_text(document)
    argv = ["init", "project", "--root", str(project), "--harness", "opencode", "--yes"]
    return loadout.main(argv), project / "loadout"


@pytest.mark.migration_integration
@pytest.mark.parametrize("scope", ["global", "project"])
def test_opencode_init_adopts_a_trailing_catch_all_and_names_the_shadowed_allow(
    tmp_path: Path, fake_home: Path, scope: str, capsys
) -> None:
    code, source = _opencode_init(fake_home, tmp_path, scope)

    assert code == 0
    out = capsys.readouterr().out
    assert "'echo *' (allow) comes before the '*' catch-all" in out
    assert "'echo' (allow) comes before the '*' catch-all" in out
    assert 'deny = ["rm"]' in (source / "permissions.toml").read_text()


@pytest.mark.migration_integration
def test_pi_global_init_adopts_a_trailing_catch_all(
    tmp_path: Path, fake_home: Path, capsys
) -> None:
    config = fake_home / ".pi" / "agent" / "extensions" / "pi-permission-system"
    config.mkdir(parents=True)
    (config / "config.json").write_text(json.dumps(DOCUMENTS["pi"]))
    source = tmp_path / "source"
    source.mkdir()

    assert loadout.main(["init", "global", str(source), "--harness", "pi", "--yes"]) == 0

    assert "'echo *' (allow) comes before the '*' catch-all" in capsys.readouterr().out
    rendered = json.loads((config / "config.json").read_text())
    assert rendered["permission"]["bash"] == {"*": "ask", "rm": "deny", "rm *": "deny"}
