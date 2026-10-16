"""Codex's rules files are Starlark, and a hand-written one need not use loadout's spelling.

Codex itself appends `prefix_rule(pattern=[...], decision="allow")` to
`~/.codex/rules/default.rules`, and its documentation shows a multi-line call with
comments, `justification`, `match` and `not_match`. Every rule is either carried
into the source or refused by name: a forbidden rule that vanished would loosen
the user's policy (S-10).
"""

from __future__ import annotations

import pytest

from loadout.errors import LoadoutError
from loadout.extract import extract
from loadout.permissions.renderers import render_codex, render_codex_project
from loadout.permissions.rules import Rules

SCOPES = {"codex": render_codex, "codex-project": render_codex_project}

DOCUMENTED = """\
# Prompt before running commands with the prefix `gh pr view` outside the sandbox.
prefix_rule(
    # The prefix to match.
    pattern = ["gh", "pr", "view"],

    # The action to take when Codex requests to run a matching command.
    decision = "prompt",

    # Optional rationale for why this rule exists.
    justification = "Viewing PRs is allowed with approval",

    # `match` and `not_match` are optional "inline unit tests" where you can
    # provide examples of commands that should (or should not) match this rule.
    match = [
        "gh pr view 7888",
        "gh pr view --repo openai/codex",
        "gh pr view 7888 --json title,body,comments",
    ],
    not_match = [
        # Does not match because the `pattern` must be an exact prefix.
        "gh pr --repo openai/codex view 7888",
    ],
)
"""


@pytest.mark.parametrize("name", sorted(SCOPES))
@pytest.mark.parametrize(
    "spelling",
    [
        'prefix_rule(pattern=["rm", "-rf"], decision="forbidden")\n',
        "prefix_rule(pattern = ['rm', '-rf'], decision = 'forbidden')\n",
        'prefix_rule(\n    pattern = [\n        "rm",\n        "-rf",\n    ],\n    decision = "forbidden",\n)\n',
        'prefix_rule(decision="forbidden", pattern=["rm", "-rf"])  # never\n',
    ],
    ids=["codex-written", "single-quoted", "multi-line", "reordered-with-comment"],
)
def test_a_forbidden_rule_in_any_spelling_adopts_as_a_deny(name: str, spelling: str) -> None:
    extraction = extract(name, spelling)

    assert extraction.rules == Rules(deny=("rm -rf",))
    assert 'prefix_rule(pattern = ["rm", "-rf"], decision = "forbidden")' in SCOPES[name](
        extraction.rules
    )


def test_the_spelling_codex_writes_adopts_into_shell_allow() -> None:
    extraction = extract("codex", 'prefix_rule(pattern=["git", "status"], decision="allow")\n')

    assert extraction.rules == Rules(allow=("git status",))
    assert extraction.notes == ()


def test_a_rule_without_a_decision_is_an_allow_as_codex_documents() -> None:
    assert extract("codex", 'prefix_rule(pattern = ["ls"])\n').rules == Rules(allow=("ls",))


def test_a_union_of_literals_adopts_as_one_rule_per_alternative() -> None:
    extraction = extract(
        "codex", 'prefix_rule(pattern = ["git", ["push", "reset"]], decision = "forbidden")\n'
    )

    assert extraction.rules == Rules(deny=("git push", "git reset"))


def test_the_documented_example_adopts_and_names_what_it_drops() -> None:
    extraction = extract("codex", DOCUMENTED)

    assert extraction.rules == Rules(ask=("gh pr view",))
    assert [note.detail for note in extraction.notes] == [
        (
            "codex: line 2: justification, match, not_match of the rule for 'gh pr view' "
            "will be dropped, which does not change which commands run"
        )
    ]


@pytest.mark.parametrize(
    ("document", "quoted"),
    [
        ('prefix_rule(pattern = ["rm"], decision = "forbidden", when = "always")\n', "when"),
        ('prefix_rule(pattern = ["rm"], decision = "deny")\n', "'deny'"),
        ('prefix_rule(["rm"], "forbidden")\n', "positional"),
        (
            'network_rule(host = "example.com", protocol = "https", decision = "forbidden")\n',
            "only prefix_rule calls can be adopted: network_rule(",
        ),
        (
            'RM = ["rm"]\nprefix_rule(pattern = RM, decision = "forbidden")\n',
            'only prefix_rule calls can be adopted: RM = ["rm"]',
        ),
        ('prefix_rule(pattern = ["rm"], decision = "forbidden"\n', "not valid"),
        ('prefix_rule(pattern = [], decision = "forbidden")\n', "pattern"),
        ('prefix_rule(pattern = {["rm"]: 1}, decision = "forbidden")\n', "pattern"),
        ('prefix_rule(pattern = ["rm"], decision = ["forbidden"])\n', "decision"),
        (
            f'prefix_rule(pattern = [{", ".join(['["a", "b"]'] * 20)}], decision = "forbidden")\n',
            "1048576",
        ),
    ],
    ids=[
        "unknown-parameter",
        "unknown-decision",
        "positional",
        "network-rule",
        "variable",
        "unparseable",
        "empty-pattern",
        "unhashable-literal",
        "unhashable-decision",
        "expansion",
    ],
)
def test_a_rule_loadout_cannot_read_refuses_with_the_line_quoted(
    document: str, quoted: str
) -> None:
    with pytest.raises(LoadoutError) as raised:
        extract("codex", document)

    message = str(raised.value)
    assert "line " in message
    assert quoted in message


@pytest.mark.parametrize(
    "pattern",
    ['["rm", "-rf /"]', '["rm", "*"]', '["=rm"]'],
    ids=["whitespace-token", "star-token", "exact-mark"],
)
def test_a_rule_the_global_renderer_cannot_reproduce_is_refused(pattern: str) -> None:
    """Each would come back as a different rule: split in two, skipped as a glob,
    or read as an exact entry. For a deny that is a loosening."""
    with pytest.raises(LoadoutError, match="cannot be carried"):
        extract("codex", f'prefix_rule(pattern = {pattern}, decision = "forbidden")\n')


@pytest.mark.parametrize("name", sorted(SCOPES))
def test_loadouts_own_spelling_still_round_trips(name: str) -> None:
    rules = Rules(allow=("git status",), ask=("git commit",), deny=("git push", "rm -rf"))
    document = SCOPES[name](rules)

    extraction = extract(name, document)

    assert extraction.rules == rules
    assert SCOPES[name](extraction.rules) == document
