"""The plugins renderers as pure functions.

Enablement, not installation: every property here is about naming a plugin the
way one harness names it, and about what happens to a reference the harness has
no way to name at all.
"""

from __future__ import annotations

import tomllib
from typing import Any

import pytest

from loadout.errors import LoadoutError
from loadout.plugins import (
    marketplaces,
    plugins,
    render_claude_plugins,
    render_codex_plugins,
    render_pi_plugins,
    unaddressable,
    unregistered_marketplaces,
)

# Shapes, not realism — the same discipline as tests/fixtures/permissions.toml.
# Both addressable halves, one of each half alone, and per-package options.
FRAGMENT: dict[str, Any] = {
    "marketplaces": {
        "nolabs-ai": {"source_type": "local", "source": "/marketplaces/nolabs-ai"},
    },
    "plugins": {
        "superpowers": {
            "source": "git:github.com/obra/superpowers",
            "marketplace": "claude-plugins-official",
            "pi": {"extensions": []},
        },
        "nono": {"source": "/packages/nono", "marketplace": "nolabs-ai"},
        "market-only": {"marketplace": "nolabs-ai"},
        "source-only": {"source": "npm:pi-skills"},
    },
}


# --- claude ------------------------------------------------------------------


def test_claude_addresses_a_plugin_as_name_at_marketplace() -> None:
    assert render_claude_plugins(FRAGMENT) == {
        "superpowers@claude-plugins-official": True,
        "nono@nolabs-ai": True,
        "market-only@nolabs-ai": True,
    }


def test_claude_keeps_declaration_order() -> None:
    """`enabledPlugins` is a map, so order is not semantic to Claude — but it is
    to `check`, which compares bytes. Emission follows the fragment."""
    rendered = render_claude_plugins(FRAGMENT)
    assert list(rendered) == [
        "superpowers@claude-plugins-official",
        "nono@nolabs-ai",
        "market-only@nolabs-ai",
    ]


def test_claude_skips_a_reference_with_no_marketplace() -> None:
    """Skipped rather than refused. A set holding both halves is the ordinary
    case — `source-only` is there for Pi — so an error would take down a render
    that is doing exactly what it should."""
    assert set(render_claude_plugins(FRAGMENT)) == {
        "superpowers@claude-plugins-official",
        "nono@nolabs-ai",
        "market-only@nolabs-ai",
    }


def test_claude_renders_nothing_from_an_empty_fragment() -> None:
    assert render_claude_plugins({}) == {}


# --- pi ----------------------------------------------------------------------


def test_pi_renders_a_bare_source_string_when_nothing_filters_it() -> None:
    assert render_pi_plugins({"plugins": {"skills": {"source": "npm:pi-skills"}}}) == [
        "npm:pi-skills"
    ]


def test_pi_renders_the_object_form_only_when_the_reference_carries_filters() -> None:
    """Pi's two documented forms, and the object one is what keeps a package's
    skills while muting its extension."""
    assert render_pi_plugins(FRAGMENT) == [
        {"source": "git:github.com/obra/superpowers", "extensions": []},
        "/packages/nono",
        "npm:pi-skills",
    ]


def test_pi_skips_a_reference_with_no_source() -> None:
    """A marketplace is not a source Pi can install from — it has no marketplace
    concept at all — so there is nothing to render."""
    assert render_pi_plugins({"plugins": {"x": {"marketplace": "m"}}}) == []


def test_pi_does_not_alias_the_fragments_options() -> None:
    fragment = {"plugins": {"x": {"source": "s", "pi": {"skills": ["a"]}}}}
    rendered = render_pi_plugins(fragment)
    rendered[0]["skills"].append("b")
    assert fragment["plugins"]["x"]["pi"]["skills"] == ["a"]


# --- codex -------------------------------------------------------------------


def test_codex_renders_enablement_and_the_marketplaces_its_plugins_reach() -> None:
    data = tomllib.loads(render_codex_plugins(FRAGMENT))
    assert data["plugins"] == {
        "superpowers@claude-plugins-official": {"enabled": True},
        "nono@nolabs-ai": {"enabled": True},
        "market-only@nolabs-ai": {"enabled": True},
    }
    assert data["marketplaces"] == {
        "nolabs-ai": {"source_type": "local", "source": "/marketplaces/nolabs-ai"}
    }


def test_codex_quotes_the_at_sign_in_a_plugin_table_header() -> None:
    """`[plugins.nono@nolabs-ai]` is not valid TOML; the key has to be quoted."""
    assert '[plugins."nono@nolabs-ai"]' in render_codex_plugins(FRAGMENT)


def test_codex_omits_a_marketplace_no_rendered_plugin_names() -> None:
    fragment = {
        "marketplaces": {"unused": {"source_type": "local", "source": "/unused"}},
        "plugins": {"x": {"source": "s"}},
    }
    assert tomllib.loads(render_codex_plugins(fragment)) == {}


def test_codex_enables_a_plugin_whose_marketplace_is_registered_elsewhere() -> None:
    """A marketplace added by hand is still a marketplace; withholding the plugin
    because loadout cannot register it would switch off something that works."""
    data = tomllib.loads(render_codex_plugins({"plugins": {"x": {"marketplace": "m"}}}))
    assert data == {"plugins": {"x@m": {"enabled": True}}}


def test_codex_carries_a_marketplaces_own_keys_through_untouched() -> None:
    """Codex owns that table's schema, so the fragment's keys are passed on
    rather than filtered against a list assembled from one machine."""
    fragment = {
        "marketplaces": {"m": {"source_type": "git", "source": "u", "ref": "v1"}},
        "plugins": {"x": {"marketplace": "m"}},
    }
    assert tomllib.loads(render_codex_plugins(fragment))["marketplaces"]["m"]["ref"] == "v1"


def test_codex_renders_only_the_banner_for_an_empty_fragment() -> None:
    rendered = render_codex_plugins({})
    assert tomllib.loads(rendered) == {}
    assert rendered.startswith("# Generated by loadout")


def test_the_codex_banner_states_a_consequence_rather_than_a_prohibition() -> None:
    """A prohibition travels: a session read `do not edit` off one file and
    applied it to another with the same basename."""
    assert "Edits to this file are replaced on the next sync." in render_codex_plugins({})


# --- reports -----------------------------------------------------------------


def test_unaddressable_names_the_key_each_harness_needs() -> None:
    assert unaddressable(FRAGMENT, "claude") == ("source-only: no marketplace",)
    assert unaddressable(FRAGMENT, "codex") == ("source-only: no marketplace",)
    assert unaddressable(FRAGMENT, "pi") == ("market-only: no source",)


def test_unregistered_marketplaces_reports_what_the_harness_does_not_know() -> None:
    """Claude keeps its registry in a file carrying `lastUpdated` and
    `installLocation`, so this is reported and never written."""
    assert unregistered_marketplaces(FRAGMENT, frozenset({"nolabs-ai"})) == (
        "claude-plugins-official",
    )


def test_unregistered_marketplaces_reports_each_name_once() -> None:
    assert unregistered_marketplaces(FRAGMENT, frozenset()) == (
        "claude-plugins-official",
        "nolabs-ai",
    )


def test_a_fully_registered_document_reports_nothing() -> None:
    known = frozenset({"claude-plugins-official", "nolabs-ai"})
    assert unregistered_marketplaces(FRAGMENT, known) == ()


# --- the fragment format -----------------------------------------------------


def test_a_plugin_name_at_the_top_level_is_refused() -> None:
    """The mistake the two-section shape exists to catch. Left lenient it renders
    nothing at all, which reads as a plugin that is simply off."""
    with pytest.raises(LoadoutError, match="unknown section"):
        plugins({"superpowers": {"marketplace": "m"}})


def test_an_unknown_key_on_a_reference_is_refused() -> None:
    """`source` and `marketplace` are loadout's own vocabulary, so a typo here is
    loadout's to catch — unlike a nested block, whose schema a harness owns."""
    with pytest.raises(LoadoutError, match="unknown key"):
        plugins({"plugins": {"x": {"sources": "s"}}})


def test_a_non_string_source_is_refused() -> None:
    with pytest.raises(LoadoutError, match="must be a string"):
        plugins({"plugins": {"x": {"source": ["s"]}}})


def test_a_reference_that_is_not_a_table_is_refused() -> None:
    with pytest.raises(LoadoutError, match="must be a table"):
        plugins({"plugins": {"x": "npm:thing"}})


def test_both_sections_are_optional() -> None:
    assert plugins({"marketplaces": {}}) == {}
    assert marketplaces({"plugins": {}}) == {}


def test_a_renderer_does_not_mutate_the_fragment_it_was_handed() -> None:
    """ADR 0001's other half: a renderer is pure, and the fragment is composed
    once and handed to three of them."""
    before = repr(FRAGMENT)
    render_claude_plugins(FRAGMENT)
    render_codex_plugins(FRAGMENT)
    render_pi_plugins(FRAGMENT)
    assert repr(FRAGMENT) == before
