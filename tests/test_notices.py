"""The reporting surface: four reports that previously reached nobody.

Every notice is advisory. Each describes a source that rendered successfully
while doing less than it says — a plugin left switched off, a hook that will
never fire — so none is drift and none changes an exit code. The tests that
matter most are the two at the bottom: a notice must not be mistaken for drift,
and a clean source must produce silence, or the surface becomes noise nobody
reads.
"""

from __future__ import annotations

import pytest

from loadout.notices import (
    OPENCODE_SKILL_FLAGS,
    Notice,
    known_events,
    notices_for,
    opencode_skills_race,
    unpermitted_servers,
)
from loadout.permissions.rules import Rules
from loadout.servers import Server


def test_an_event_the_harness_does_not_know_is_reported() -> None:
    found = notices_for("claude", "hooks", {"NoSuchEvent": []})

    assert [n.message for n in found] == ["NoSuchEvent is not in this harness's known event list"]


def test_a_known_event_is_silent() -> None:
    assert notices_for("claude", "hooks", {"PreToolUse": []}) == ()


def test_an_adapted_harness_reports_a_missing_mapping_instead() -> None:
    """On Claude an unknown event may still fire — its list is a lower bound. On
    OpenCode the list is loadout's own translation table, so an event outside it
    provably does not fire: there is no adapter branch to run it. Same call,
    different certainty, and the wording has to carry that."""
    found = notices_for("opencode", "hooks", {"WorktreeCreate": []})

    assert [n.message for n in found] == [
        "WorktreeCreate has no adapter mapping, so it will not fire"
    ]


def test_known_events_differ_per_harness() -> None:
    assert "PreToolUse" in known_events("claude")
    assert "PreToolUse" in known_events("opencode")
    assert known_events("nonesuch") == frozenset()


def test_a_plugin_the_harness_cannot_address_is_reported() -> None:
    """Pi addresses by source; a marketplace-only reference has nothing to name."""
    document = {"plugins": {"superpowers": {"marketplace": "official"}}}

    found = notices_for("pi", "plugins", document)

    assert [n.message for n in found] == ["superpowers: no source — left switched off"]


def test_an_unregistered_marketplace_is_reported() -> None:
    document = {"plugins": {"superpowers": {"marketplace": "official"}}}

    found = notices_for("claude", "plugins", document, known_marketplaces=frozenset())

    assert found[0].message.startswith("marketplace 'official' is not registered")


def test_a_registered_marketplace_is_silent() -> None:
    document = {"plugins": {"superpowers": {"marketplace": "official"}}}

    assert notices_for("claude", "plugins", document, frozenset({"official"})) == ()


def test_a_harness_addressing_by_source_skips_the_marketplace_check() -> None:
    """Pi never resolves a marketplace, so an unregistered one is not its problem
    — reporting it there would be advice the user cannot act on."""
    document = {"plugins": {"superpowers": {"source": "git:example", "marketplace": "official"}}}

    assert notices_for("pi", "plugins", document, frozenset()) == ()


def test_a_notice_names_its_agent_and_slice() -> None:
    found = notices_for("codex", "hooks", {"Nope": []})

    assert found[0].agent == "codex"
    assert found[0].slice == "hooks"
    assert found[0].render() == "codex.hooks: Nope is not in this harness's known event list"


@pytest.mark.parametrize("slice_name", ["permissions", "instructions", "skills", "settings"])
def test_a_slice_with_no_reporter_is_silent(slice_name: str) -> None:
    """Only hooks and plugins report today. A slice with nothing to say must say
    nothing rather than fall through to a default."""
    assert notices_for("claude", slice_name, {"anything": {}}) == ()


def test_a_notice_is_not_drift() -> None:
    """The distinction the whole surface rests on. Drift means generated output
    disagrees with its source and `check` exits 1. A notice means the source
    itself asked for something the harness cannot carry — the output is correct
    and the exit code must not move."""
    assert Notice(agent="claude", slice="hooks", message="x").render().startswith("claude.hooks:")


@pytest.mark.parametrize("flag", OPENCODE_SKILL_FLAGS)
def test_either_flag_stops_the_opencode_skills_report(flag: str) -> None:
    """`disableClaudeCodeSkills` is `broad || direct` in OpenCode's runtime flags,
    so a check reading only the specific name reports a collision at whoever set
    the broad one. Both names are parametrised rather than one asserted, because a
    single-name test passes against exactly that bug."""
    assert not opencode_skills_race({flag: "1"})


@pytest.mark.parametrize("value", ["1", "true", "TRUE", "yes", "on", " 1 "])
def test_the_flag_is_read_the_way_a_boolean_env_var_is_written(value: str) -> None:
    assert not opencode_skills_race({OPENCODE_SKILL_FLAGS[0]: value})


@pytest.mark.parametrize("environ", [{}, {OPENCODE_SKILL_FLAGS[0]: ""}, {"OPENCODE_PURE": "1"}])
def test_an_unset_or_empty_flag_leaves_the_race_reported(environ: dict[str, str]) -> None:
    assert opencode_skills_race(environ)


def test_an_explicit_off_is_not_mistaken_for_on() -> None:
    """`=0` means the collision is live, so silence there would be the false
    negative that matters — the user has said the opposite of what we need."""
    assert opencode_skills_race({OPENCODE_SKILL_FLAGS[0]: "0"})
    assert opencode_skills_race({OPENCODE_SKILL_FLAGS[1]: "false"})


def test_a_defined_server_with_no_policy_is_reported() -> None:
    servers = {"jina": Server(name="jina", transport="http", url="https://x")}

    assert unpermitted_servers(servers, Rules()) == ("jina",)


def test_a_server_covered_by_a_wildcard_is_silent() -> None:
    servers = {"jina": Server(name="jina", transport="http", url="https://x")}

    assert unpermitted_servers(servers, Rules(mcp_allow=("jina/*",))) == ()


def test_a_server_covered_by_one_tool_is_silent() -> None:
    """Any policy naming the server means the user knows it exists; the notice is
    for a definition nobody has said anything about."""
    servers = {"jina": Server(name="jina", transport="http", url="https://x")}

    assert unpermitted_servers(servers, Rules(mcp_allow=("jina/search",))) == ()
