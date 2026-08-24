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
from loadout.extract import (
    ABSENT,
    CAPABILITIES,
    CATCH_ALL,
    EXTRACTORS,
    Extraction,
    Merged,
    extract,
    merge_extractions,
)
from loadout.permissions.rules import Rules
from test_extract_roundtrip import CLEAN_SPACE, INVERTED, _render


def _verdicts(merged: Merged, entry: str, kind: str | None = None) -> dict[str, str]:
    """`entry` alone is not a key: `*` is legal for a shell rule and for the
    catch-all, and one merge can report both. Pass `kind` when it matters."""
    for divergence in merged.divergences:
        if divergence.entry == entry and (kind is None or divergence.kind == kind):
            return dict(divergence.verdicts)
    raise AssertionError(f"no divergence reported for {entry!r} (kind={kind})")


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


def test_the_carriers_agreeing_on_a_catch_all_default_produce_it() -> None:
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(allow=("ls",))),
            "codex": Extraction(Rules(allow=("ls",))),
            "opencode": Extraction(Rules(allow=("ls",), default="allow")),
            "pi": Extraction(Rules(allow=("ls",), default="allow")),
        }
    )
    assert merged.rules.default == "allow"
    assert merged.divergences == ()


def test_claude_and_codex_do_not_vote_on_the_catch_all_default() -> None:
    """Their catch-alls are `defaultMode` and `approval_policy`, in files these
    renderers do not write — silence there is structural, not disagreement."""
    merged = merge_extractions(
        {
            "claude": Extraction(Rules(allow=("ls",))),
            "codex": Extraction(Rules(allow=("ls",))),
            "opencode": Extraction(Rules(allow=("ls",), default="deny")),
        }
    )
    assert merged.rules.default == "deny"
    assert merged.divergences == ()


def test_a_catch_all_the_two_carriers_disagree_on_settles_on_the_strictest() -> None:
    merged = merge_extractions(
        {
            "opencode": Extraction(Rules(allow=("ls",), default="allow")),
            "pi": Extraction(Rules(allow=("ls",))),
        }
    )
    assert _verdicts(merged, "*", CATCH_ALL) == {"opencode": "allow", "pi": "ask"}
    # Reported, but not withheld the way an entry is: withholding a catch-all
    # drops it to `ask`, the MIDDLE verdict, so a stated deny would come back a
    # prompt. The loser's rule still comes through, so both harnesses were read.
    assert merged.rules.default is None  # strictest here IS ask, i.e. unstated
    assert merged.rules.allow == ("ls",)


def test_a_deny_catch_all_is_never_loosened_by_a_disagreement() -> None:
    """The escalation this rule exists to stop. Withholding would render `ask`."""
    merged = merge_extractions(
        {
            "opencode": Extraction(Rules(allow=("ls",), default="deny")),
            "pi": Extraction(Rules(allow=("ls",), default="allow")),
        }
    )
    assert merged.rules.default == "deny"
    assert _verdicts(merged, "*", CATCH_ALL) == {"opencode": "deny", "pi": "allow"}
    assert merged.rules.allow == ("ls",)


def test_a_withheld_rule_stops_the_catch_all_being_stated_permissively() -> None:
    """A dropped entry falls through to the catch-all, so while one is outstanding
    the catch-all may not be looser than the `ask` it used to fall through to.
    Without this, adopting these two files renders `curl` executable."""
    merged = merge_extractions(
        {
            "opencode": Extraction(Rules(deny=("curl",), default="allow")),
            "pi": Extraction(Rules(default="allow")),
        }
    )
    assert _verdicts(merged, "curl", "shell") == {"opencode": "deny", "pi": ABSENT}
    assert merged.rules.deny == ()
    assert merged.rules.default is None, "an allow catch-all would swallow the dropped deny"


def test_an_unwithheld_merge_still_states_a_permissive_catch_all() -> None:
    """The guard above must not fire on agreement, or `default` never survives."""
    merged = merge_extractions(
        {
            "opencode": Extraction(Rules(deny=("curl",), default="allow")),
            "pi": Extraction(Rules(deny=("curl",), default="allow")),
        }
    )
    assert merged.divergences == ()
    assert merged.rules.default == "allow"
    assert merged.rules.deny == ("curl",)


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
