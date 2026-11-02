"""A plugin reference can say `enabled = false`.

Before this the source had no word for off, so init could only note a plugin a harness listed
as switched off, and Claude would then fall back to the plugin's `defaultEnabled`, on by
default. The user's ruling: carry it. Claude and Droid render `false`; Codex and Pi have
nothing to switch off, since a plugin they are not given is not loaded, so they omit it.
"""

from __future__ import annotations

import tomllib
from typing import Any

import pytest

from loadout.documents import merge_documents
from loadout.errors import LoadoutError
from loadout.extract import extract_codex_plugins, extract_value
from loadout.plugins import (
    plugins,
    render_claude_plugins,
    render_codex_plugins,
    render_droid_plugins,
    render_pi_plugins,
    unregistered_marketplaces,
)

FRAGMENT: dict[str, Any] = {
    "marketplaces": {
        "tools": {"source": {"source": "github", "repo": "a/tools"}},
        "old": {"source": {"source": "github", "repo": "a/old"}},
    },
    "plugins": {
        "lint": {"source": "npm:lint", "marketplace": "tools"},
        "legacy": {"source": "npm:legacy", "marketplace": "old", "enabled": False},
    },
}


def test_claude_renders_a_switched_off_plugin_as_false() -> None:
    assert render_claude_plugins(FRAGMENT) == {"lint@tools": True, "legacy@old": False}


def test_droid_renders_false_and_keeps_the_marketplace_it_names() -> None:
    assert render_droid_plugins(FRAGMENT) == {
        "enabledPlugins": {"lint@tools": True, "legacy@old": False},
        "extraKnownMarketplaces": {
            "tools": {"source": {"source": "github", "repo": "a/tools"}},
            "old": {"source": {"source": "github", "repo": "a/old"}},
        },
    }


def test_codex_omits_a_switched_off_plugin_and_the_marketplace_only_it_names() -> None:
    assert tomllib.loads(render_codex_plugins(FRAGMENT)) == {
        "marketplaces": {"tools": {"source": {"source": "github", "repo": "a/tools"}}},
        "plugins": {"lint@tools": {"enabled": True}},
    }


def test_pi_omits_a_switched_off_package() -> None:
    assert render_pi_plugins(FRAGMENT) == ["npm:lint"]


def test_enabled_true_renders_as_a_reference_without_it() -> None:
    fragment = {"plugins": {"lint": {"marketplace": "tools", "enabled": True}}}
    assert render_claude_plugins(fragment) == {"lint@tools": True}


def test_a_profile_overlay_switches_a_plugin_off() -> None:
    base = {"plugins": {"lint": {"marketplace": "tools"}, "fmt": {"marketplace": "tools"}}}
    overlay = {"plugins": {"lint": {"enabled": False}}}
    assert render_claude_plugins(merge_documents(base, overlay)) == {
        "lint@tools": False,
        "fmt@tools": True,
    }


def test_a_switched_off_plugin_s_marketplace_needs_no_registration() -> None:
    assert unregistered_marketplaces(FRAGMENT, frozenset()) == ("tools",)


@pytest.mark.parametrize("value", ["false", 0, None])
def test_enabled_must_be_a_boolean(value: object) -> None:
    with pytest.raises(LoadoutError, match=r"legacy\.enabled must be true or false"):
        plugins({"plugins": {"legacy": {"marketplace": "old", "enabled": value}}})


# --- read back ---------------------------------------------------------------------


@pytest.mark.parametrize("harness", ["claude", "droid"])
def test_a_false_entry_is_adopted_as_switched_off_and_renders_back(harness: str) -> None:
    document = {"enabledPlugins": {"lint@tools": True, "legacy@old": False}}
    extraction = extract_value(f"{harness}-plugins", document)
    assert extraction.notes == ()
    assert extraction.value == {
        "plugins": {
            "lint": {"marketplace": "tools"},
            "legacy": {"marketplace": "old", "enabled": False},
        }
    }
    assert render_claude_plugins(extraction.value) == document["enabledPlugins"]


def test_a_droid_marketplace_named_only_by_a_switched_off_plugin_is_adopted() -> None:
    document = {
        "enabledPlugins": {"legacy@old": False},
        "extraKnownMarketplaces": {"old": {"source": {"source": "github", "repo": "a/old"}}},
    }
    extraction = extract_value("droid-plugins", document)
    assert extraction.notes == ()
    assert render_droid_plugins(extraction.value) == document


def test_a_codex_plugin_switched_off_is_adopted_as_switched_off() -> None:
    extraction = extract_codex_plugins('[plugins."legacy@old"]\nenabled = false\n')
    assert extraction.notes == ()
    assert extraction.value == {"plugins": {"legacy": {"marketplace": "old", "enabled": False}}}


@pytest.mark.parametrize("value", ["yes", 1])
def test_a_claude_entry_that_is_not_a_boolean_is_reported(value: object) -> None:
    extraction = extract_value("claude-plugins", {"enabledPlugins": {"x@m": value}})
    assert extraction.value == {}
    assert [(note.kind, note.text) for note in extraction.notes] == [
        ("unrecognised", f"claude: x@m is {value!r}, which is neither true nor false")
    ]
