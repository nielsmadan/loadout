"""The inverses of the three plugins renderers.

Two properties, per `docs/reference/extraction.md`:

1. `extract(render(x)) == carried(x)`. Here `carried` is a **real projection**,
   unlike hooks' identity: each harness can state only the half of a reference it
   addresses by, so Claude and Codex carry `marketplace` and drop `source`, and
   Pi carries `source` and drops `marketplace`.
2. `render(extract(doc)) == doc`, wherever `notes` is empty.

`pi-plugins` is the one place those two come apart, and deliberately. Its
document round trip closes — a package is rendered from its `source`, which
survives exactly — while `notes` is never empty, because the *name* a reference
is filed under is not in Pi's document at all and has to be derived. Reporting an
invented identifier is the point; suppressing the note to satisfy a biconditional
would hide it.
"""

from __future__ import annotations

from typing import Any

import pytest

from loadout.errors import LoadoutError
from loadout.extract import extract_codex_plugins, extract_value
from loadout.plugins import render_claude_plugins, render_codex_plugins, render_pi_plugins

FRAGMENT: dict[str, Any] = {
    "marketplaces": {"nolabs-ai": {"source_type": "local", "source": "/marketplaces/nolabs-ai"}},
    "plugins": {
        "superpowers": {
            "source": "git:github.com/obra/superpowers",
            "marketplace": "claude-plugins-official",
            "pi": {"extensions": []},
        },
        "nono": {"source": "/packages/nono", "marketplace": "nolabs-ai"},
    },
}


def claude_file(fragment: dict[str, Any]) -> dict[str, Any]:
    """What the composing loop writes: the residual, then each owned key."""
    return {"model": "opus", "enabledPlugins": render_claude_plugins(fragment)}


def pi_file(fragment: dict[str, Any]) -> dict[str, Any]:
    return {"packages": render_pi_plugins(fragment)}


# --- property 1: extract(render(x)) == carried(x) ----------------------------


def test_claude_carries_the_marketplace_and_drops_the_source() -> None:
    assert extract_value("claude-plugins", claude_file(FRAGMENT)).value == {
        "plugins": {
            "superpowers": {"marketplace": "claude-plugins-official"},
            "nono": {"marketplace": "nolabs-ai"},
        }
    }


def test_codex_carries_the_marketplace_registration_as_well() -> None:
    """The difference that makes Codex's half of the slice renderable: its
    registration lives in the file loadout writes, so it reads back too."""
    assert extract_codex_plugins(render_codex_plugins(FRAGMENT)).value == {
        "marketplaces": {
            "nolabs-ai": {"source_type": "local", "source": "/marketplaces/nolabs-ai"}
        },
        "plugins": {
            "superpowers": {"marketplace": "claude-plugins-official"},
            "nono": {"marketplace": "nolabs-ai"},
        },
    }


def test_pi_carries_the_source_and_its_filters() -> None:
    assert extract_value("pi-plugins", pi_file(FRAGMENT)).value == {
        "plugins": {
            "superpowers": {
                "source": "git:github.com/obra/superpowers",
                "pi": {"extensions": []},
            },
            "nono": {"source": "/packages/nono"},
        }
    }


def test_extraction_does_not_alias_the_document() -> None:
    document = pi_file(FRAGMENT)
    extracted = extract_value("pi-plugins", document).value
    extracted["plugins"]["superpowers"]["pi"]["extensions"].append("x")
    assert document["packages"][0]["extensions"] == []


# --- property 2: render(extract(doc)) == doc ---------------------------------


def test_claude_re_renders_the_document_it_read() -> None:
    document = claude_file(FRAGMENT)
    extraction = extract_value("claude-plugins", document)
    assert extraction.notes == ()
    assert render_claude_plugins(extraction.value) == document["enabledPlugins"]


def test_codex_re_renders_the_document_it_read() -> None:
    rendered = render_codex_plugins(FRAGMENT)
    extraction = extract_codex_plugins(rendered)
    assert extraction.notes == ()
    assert render_codex_plugins(extraction.value) == rendered


def test_pi_re_renders_the_document_it_read_though_the_name_was_derived() -> None:
    document = pi_file(FRAGMENT)
    assert render_pi_plugins(extract_value("pi-plugins", document).value) == document["packages"]


# --- what does not survive ---------------------------------------------------


def test_a_plugin_switched_off_in_the_file_is_reported_not_extracted() -> None:
    """`false` has no representation in a fragment: absence is how a plugin is
    off, so extracting it as something would switch it back on."""
    extraction = extract_value("claude-plugins", {"enabledPlugins": {"x@m": False}})
    assert extraction.value == {}
    assert [note.detail for note in extraction.notes] == [
        "claude: x@m is False, which is not enabled"
    ]


def test_a_key_that_is_not_name_at_marketplace_is_reported() -> None:
    extraction = extract_value("claude-plugins", {"enabledPlugins": {"bare": True}})
    assert extraction.value == {}
    assert extraction.notes[0].kind == "unrecognised"


def test_a_codex_plugin_table_key_loadout_does_not_write_is_reported() -> None:
    document = '[plugins."x@m"]\nenabled = true\nautoupdate = true\n'
    extraction = extract_codex_plugins(document)
    assert extraction.value == {"plugins": {"x": {"marketplace": "m"}}}
    assert [note.detail for note in extraction.notes] == ["codex: x@m carries autoupdate"]


def test_every_pi_entry_reports_the_name_it_had_to_invent() -> None:
    extraction = extract_value("pi-plugins", {"packages": ["npm:pi-skills"]})
    assert extraction.value == {"plugins": {"pi-skills": {"source": "npm:pi-skills"}}}
    assert [note.detail for note in extraction.notes] == [
        "pi: npm:pi-skills carries no plugin name; filed as 'pi-skills'"
    ]


@pytest.mark.parametrize(
    ("source", "name"),
    [
        ("npm:pi-skills", "pi-skills"),
        ("npm:@gotgenes/pi-permission-system@23.0.0", "pi-permission-system"),
        ("git:github.com/obra/superpowers", "superpowers"),
        ("git:github.com/user/repo@v1", "repo"),
        ("https://github.com/user/repo", "repo"),
        ("/Users/someone/.config/packages/nono/pi", "pi"),
        ("./relative/pkg/", "pkg"),
    ],
)
def test_a_pi_name_comes_from_the_last_segment_ahead_of_the_pinned_ref(
    source: str, name: str
) -> None:
    """Pi's three documented source syntaxes (`docs/packages.md`, shipped). The
    ref is dropped from the name only — the reference keeps the source verbatim,
    so a pin survives and re-rendering is byte-identical."""
    extracted = extract_value("pi-plugins", {"packages": [source]}).value
    assert list(extracted["plugins"]) == [name]
    assert extracted["plugins"][name]["source"] == source


def test_an_object_entry_that_filters_nothing_is_reported_as_renormalised() -> None:
    """`pi install` writes this for a package with nothing to filter — the live
    settings.json has one — and Pi's docs make the two forms equivalent. The
    renderer emits the string, so the bytes change and extraction says so."""
    extraction = extract_value("pi-plugins", {"packages": [{"source": "npm:pi-skills"}]})
    assert render_pi_plugins(extraction.value) == ["npm:pi-skills"]
    assert "renders as a string" in " ".join(note.detail for note in extraction.notes)


def test_two_packages_deriving_one_name_keep_both_references() -> None:
    """The source is unique by construction, so the collision falls back to it.
    Merging them would drop a package the machine actually loads."""
    packages = ["git:github.com/one/tools", "git:github.com/two/tools"]
    extracted = extract_value("pi-plugins", {"packages": packages}).value
    assert list(extracted["plugins"]) == ["tools", "git:github.com/two/tools"]


# --- registry ----------------------------------------------------------------


def test_an_unknown_name_is_an_error_rather_than_a_silent_empty() -> None:
    with pytest.raises(LoadoutError, match="no value extractor"):
        extract_value("codex-plugins", "")


def test_a_missing_key_extracts_empty_rather_than_failing() -> None:
    """A settings.json with no plugins is the common case, not an error."""
    assert extract_value("claude-plugins", {"model": "opus"}).value == {}
    assert extract_value("pi-plugins", {"model": "opus"}).value == {}
