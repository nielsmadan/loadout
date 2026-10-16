"""A lone `x *` deny on OpenCode or Pi reads as the prefix `x` (P-49).

Since OpenCode v1.1.15 and Pi's permission system v5.12.0 a trailing ` *` also matches the
bare command, so the deny already blocks `x`. Read as a prefix it renders on every harness,
Droid included, and blocks at least as much. Allow and ask stay globs: widening one could
loosen policy on an older version.
"""

from __future__ import annotations

from typing import Any

import pytest

from loadout.extract import extract
from loadout.permissions.renderers import RENDERERS, render_droid
from loadout.permissions.rules import Rules

SCOPES = ("opencode", "pi", "pi-project")


def _document(name: str, bash: dict[str, str]) -> dict[str, Any]:
    if name == "opencode":
        return {"permission": {"bash": {"*": "ask", **bash}}}
    if name == "pi":
        return {"permission": {"*": "ask", "bash": {"*": "ask", **bash}, "mcp": {"*": "ask"}}}
    return {"permission": {"bash": bash}}


@pytest.mark.parametrize("name", SCOPES)
def test_a_lone_trailing_star_deny_reads_as_its_prefix(name: str) -> None:
    rules = extract(name, _document(name, {"rm *": "deny", "git push *": "deny"})).rules

    assert rules.deny == ("rm", "git push")


@pytest.mark.parametrize("name", SCOPES)
@pytest.mark.parametrize("decision", ["allow", "ask"])
def test_a_lone_trailing_star_allow_or_ask_stays_a_glob(name: str, decision: str) -> None:
    rules = extract(name, _document(name, {"git *": decision})).rules

    assert rules.shell(decision) == ("git *",)


@pytest.mark.parametrize("name", SCOPES)
def test_a_trailing_star_deny_beside_its_bare_command_stays_a_glob(name: str) -> None:
    rules = extract(name, _document(name, {"rm": "allow", "rm *": "deny"})).rules

    assert (rules.allow, rules.deny) == (("=rm",), ("rm *",))


@pytest.mark.parametrize("name", SCOPES)
def test_a_deny_with_no_command_before_the_star_stays_as_written(name: str) -> None:
    """Review 5.7: an empty prefix is no command at all, so there is nothing to widen to."""
    rules = extract(name, _document(name, {" *": "deny"})).rules

    assert rules.deny == (" *",)


@pytest.mark.parametrize("name", SCOPES)
def test_a_deny_whose_prefix_is_itself_a_glob_stays_a_glob(name: str) -> None:
    rules = extract(name, _document(name, {"rm -* *": "deny"})).rules

    assert rules.deny == ("rm -* *",)


@pytest.mark.parametrize("name", SCOPES)
def test_the_read_deny_round_trips_and_droid_renders_it(name: str) -> None:
    rules = extract(name, _document(name, {"rm *": "deny"})).rules
    spec = RENDERERS[name]

    rendered = spec.fn(rules, {})  # type: ignore[call-arg, union-attr]

    assert extract(name, rendered).rules == rules
    assert render_droid(rules, {})["commandBlocklist"] == ["rm"]
    assert rules == Rules(deny=("rm",))
