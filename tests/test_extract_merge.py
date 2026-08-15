"""Reconciling one source out of several harnesses' extracted rules.

The rule that makes this safe: **divergence is reported, never unioned.** If
Claude allows a command and OpenCode denies it, no single source rule reproduces
both, and picking `allow` renders a wider permission set than what was on disk —
a privilege escalation performed by an onboarding tool. The entry is left out of
the source and named in the report for a person to resolve.

Absence counts as a verdict. A command Claude allows and Codex says nothing about
is drift between two hand-maintained configs, which is the thing a careful person
runs extraction to find.

What is *not* divergence is a harness that structurally cannot express the rule.
Codex's token matcher has no way to say `gamma-*`, so Codex is not a voter on
glob entries — counting its silence as disagreement would suppress a rule every
harness that can express it agrees on.
"""

from __future__ import annotations

import pytest

from loadout.errors import LoadoutError
from loadout.extract import CAPABILITIES, EXTRACTORS, Extraction, Merged, extract, merge_extractions
from loadout.permissions.rules import Rules
from test_extract_roundtrip import CLEAN_SPACE, INVERTED, _render


def _verdicts(merged: Merged, entry: str) -> dict[str, str]:
    for divergence in merged.divergences:
        if divergence.entry == entry:
            return dict(divergence.verdicts)
    raise AssertionError(f"no divergence reported for {entry!r}")


def test_agreeing_harnesses_produce_the_rule() -> None:
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(allow=("git status",))),
            "opencode": Extraction(Rules(allow=("git status",))),
            "pi": Extraction(Rules(allow=("git status",))),
            "codex": Extraction(Rules(allow=("git status",))),
        }
    )
    assert merged.rules.allow == ("git status",)
    assert merged.divergences == ()


def test_a_command_one_harness_denies_is_never_emitted_as_allowed() -> None:
    """The privilege-escalation guard: unioning here would widen the real config."""
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(allow=("curl",))),
            "opencode": Extraction(Rules(deny=("curl",))),
        }
    )
    assert _verdicts(merged, "curl") == {"claude": "allow", "opencode": "deny"}
    assert "curl" not in merged.rules.allow


def test_a_command_missing_from_one_harness_is_reported_as_drift() -> None:
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(allow=("rg",))),
            "codex": Extraction(Rules(allow=("ls",))),
        }
    )
    assert _verdicts(merged, "rg") == {"claude": "allow", "codex": "absent"}
    assert _verdicts(merged, "ls") == {"claude": "absent", "codex": "allow"}
    assert merged.rules.allow == ()


def test_codex_silence_on_a_glob_is_not_divergence() -> None:
    """Codex's token matcher cannot express a glob, so it does not get a vote."""
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(allow=("gamma-*",))),
            "codex": Extraction(Rules()),
            "opencode": Extraction(Rules(allow=("gamma-*",))),
        }
    )
    assert merged.rules.allow == ("gamma-*",)
    assert merged.divergences == ()


def test_codex_silence_on_a_plain_command_is_divergence() -> None:
    """The same silence, on an entry Codex *can* express, is real drift."""
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(allow=("alpha",))),
            "codex": Extraction(Rules()),
            "opencode": Extraction(Rules(allow=("alpha",))),
        }
    )
    assert _verdicts(merged, "alpha") == {
        "claude": "allow",
        "codex": "absent",
        "opencode": "allow",
    }
    assert merged.rules.allow == ()


def test_shell_only_harnesses_do_not_vote_on_mcp_targets() -> None:
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(mcp_allow=("svc/read",))),
            "codex": Extraction(Rules()),
            "pi": Extraction(Rules(mcp_allow=("svc/read",))),
        }
    )
    assert merged.rules.mcp_allow == ("svc/read",)
    assert merged.divergences == ()


def test_mcp_disagreement_is_reported_and_withheld() -> None:
    merged = merge_extractions(
        {
            "claude-mcp": Extraction(Rules(mcp_allow=("svc/purge",))),
            "codex-mcp": Extraction(Rules(mcp_deny=("svc/purge",))),
        }
    )
    assert _verdicts(merged, "svc/purge") == {"claude-mcp": "allow", "codex-mcp": "deny"}
    assert "svc/purge" not in merged.rules.mcp_allow


def test_harness_specific_passthroughs_survive_the_merge() -> None:
    """claude_extra and opencode_extra have exactly one source, so they cannot diverge."""
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(claude_extra_allow=("Read(//tmp/**)",))),
            "opencode": Extraction(Rules(opencode_extra={"webfetch": "allow"})),
        }
    )
    assert merged.rules.claude_extra_allow == ("Read(//tmp/**)",)
    assert merged.rules.opencode_extra == {"webfetch": "allow"}
    assert merged.divergences == ()


def test_notes_from_every_harness_reach_the_report() -> None:
    codex = extract("codex", _render("codex", Rules(allow=("gamma-*",)), {}))
    merged = merge_extractions({"codex": codex, "claude": Extraction(Rules())})
    assert [note.detail for note in merged.notes] == [note.detail for note in codex.notes]


def test_harnesses_agreeing_a_rule_appears_twice_keep_both_entries() -> None:
    """A source may list an entry in two categories — the fixture's `zeta` does.

    Where every harness's document still shows both, the shadowed entry is real
    source content and survives.
    """
    both = Rules(allow=("zeta", "eta"), deny=("zeta",))
    merged = merge_extractions({"claude": Extraction(both), "codex": Extraction(both)})
    assert merged.rules.allow == ("zeta", "eta")
    assert merged.rules.deny == ("zeta",)
    assert merged.divergences == ()


def test_a_rule_one_harness_lists_twice_and_another_once_is_reported() -> None:
    """Pi's map keeps one decision per pattern, so the shadowed `allow` is gone.

    Collapsing to the surviving `deny` would silently drop source content, and
    inventing the `allow` back would widen Pi. Neither is guessed at.
    """
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(allow=("zeta",), deny=("zeta",))),
            "pi": Extraction(Rules(deny=("zeta",))),
        }
    )
    assert _verdicts(merged, "zeta") == {"claude": "allow+deny", "pi": "deny"}
    assert merged.rules.allow == ()
    assert merged.rules.deny == ()


def test_an_undeclared_harness_is_refused_rather_than_ignored() -> None:
    """A harness excluded from the vote cannot veto, so its denials would vanish.

    Defaulting an unknown name to "not a voter" turns adding a renderer into a
    silent widening of the source; the merge refuses instead.
    """
    with pytest.raises(LoadoutError, match="capability"):
        merge_extractions({"newharness": Extraction(Rules(deny=("curl",)))})


def test_every_inverted_renderer_declares_a_capability() -> None:
    """Scoped to what extraction inverts, not to `RENDERERS`.

    `merge_extractions` reconciles permission verdicts; the hooks renderers have
    no verdict to cast. That a renderer may only be absent if it was named is
    pinned by `test_no_renderer_lacks_an_inverse_without_being_named`, so this
    cannot quietly drop a permissions renderer.
    """
    assert sorted(CAPABILITIES) == sorted(EXTRACTORS)


@pytest.mark.parametrize("rules", CLEAN_SPACE, ids=range(len(CLEAN_SPACE)))
def test_a_machine_rendered_from_one_source_merges_back_to_it(rules: Rules) -> None:
    """End to end: render to every harness, extract each, reconcile — no divergence.

    A source rendered to all nine documents is by construction internally
    consistent, so any divergence reported here is the extractor inventing drift
    that is not on disk.
    """
    extractions = {name: extract(name, _render(name, rules, {})) for name in ("claude", *INVERTED)}
    merged = merge_extractions(extractions)
    assert merged.divergences == ()
    assert merged.rules == rules
