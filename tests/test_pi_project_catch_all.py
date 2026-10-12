"""Pi's project config honours a `*` catch-all, so project scope carries `[shell] default`.

pi-permission-system shallow-merges the project `bash` map over the global one
(`mergeFlatPermissions`: `{...baseVal, ...value}`) and resolves it last-match-wins
(`patterns.findLast`), with `*` an ordinary wildcard. A project `*` is therefore a
catch-all, and loadout writes it when the source states a default.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import loadout
from loadout.extract import extract
from loadout.permissions.renderers import render_pi_project
from loadout.permissions.rules import Rules

CONFIG = Path(".pi/extensions/pi-permission-system/config.json")


def test_a_stated_default_renders_the_catch_all_first() -> None:
    bash = render_pi_project(Rules(allow=("pwd",), default="deny"), {})["permission"]["bash"]

    assert list(bash.items()) == [("*", "deny"), ("pwd", "allow"), ("pwd *", "allow")]


def test_no_stated_default_renders_no_catch_all() -> None:
    bash = render_pi_project(Rules(allow=("pwd",)), {})["permission"]["bash"]

    assert bash == {"pwd": "allow", "pwd *": "allow"}


def test_a_stated_ask_round_trips_as_stated() -> None:
    """At project scope nothing seeds `ask`, so a written `ask` was stated and must
    come back stated: dropping it would hand the decision to the global config."""
    document = render_pi_project(Rules(allow=("pwd",), default="ask"), {})

    extraction = extract("pi-project", document)

    assert extraction.rules == Rules(allow=("pwd",), default="ask")
    assert extraction.notes == ()
    assert render_pi_project(extraction.rules, {}) == document


def test_a_trailing_catch_all_becomes_the_default_and_names_what_it_shadowed() -> None:
    document = {"permission": {"bash": {"echo *": "allow", "*": "ask", "rm": "deny"}, "mcp": {}}}

    extraction = extract("pi-project", document)

    assert extraction.rules == Rules(deny=("=rm",), default="ask")
    assert [note.detail for note in extraction.notes] == [
        (
            "pi-project: 'echo *' (allow) comes before the '*' catch-all, so it never applies "
            "and will be dropped"
        )
    ]
    assert render_pi_project(extraction.rules, {})["permission"]["bash"] == {
        "*": "ask",
        "rm": "deny",
    }


@pytest.mark.migration_integration
def test_project_init_adopts_a_trailing_catch_all(tmp_path: Path, capsys) -> None:
    root = tmp_path / "project"
    (root / CONFIG.parent).mkdir(parents=True)
    (root / CONFIG).write_text(
        json.dumps({"permission": {"bash": {"echo *": "allow", "*": "ask", "rm": "deny"}}})
    )

    argv = ["init", "project", "--root", str(root), "--harness", "pi", "--yes"]
    assert loadout.main(argv) == 0

    assert "'echo *' (allow) comes before the '*' catch-all" in capsys.readouterr().out
    source = (root / "loadout" / "permissions.toml").read_text()
    assert 'default = "ask"' in source
    assert json.loads((root / CONFIG).read_text())["permission"]["bash"] == {
        "*": "ask",
        "rm": "deny",
    }
