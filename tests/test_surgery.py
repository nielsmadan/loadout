from __future__ import annotations

import json
import tomllib

import pytest

from loadout.errors import LoadoutError
from loadout.surgery import apply_json, apply_toml, reject_nested

OWNED = frozenset({"model", "mcp_servers"})

LIVE = '''# a comment someone wrote
model = "old-model"

developer_instructions = """
keep me
"""

# >>> nono:nolabs-ai-codex >>>
sandbox_mode = "workspace-write"
# <<< nono:nolabs-ai-codex <<<

[mcp_servers.context7]
default_tools_approval_mode = "approve"

[mcp_servers.jina]
default_tools_approval_mode = "approve"

[projects."/Users/me/ac"]
trust_level = "trusted"
'''

DOCUMENT = """model = "gpt-5.6-sol"

[mcp_servers.context7]
default_tools_approval_mode = "approve"
"""


def test_a_server_dropped_from_the_source_is_removed_from_the_file() -> None:
    """The case derived ownership cannot express, and the reason ADR 0017 exists.

    The prototype this ports derived its owned set from the document being written,
    so a server removed from the source was absent from that set, nothing stripped
    it, and it survived every later run — keeping `approve` for a server someone
    may have removed *because* they stopped trusting it.

    `context7` is asserted alongside: without it this passes against a strip that
    removed the whole table tree, which would be a different bug wearing the same
    green.
    """
    result = apply_toml(LIVE, OWNED, DOCUMENT)

    assert "mcp_servers.jina" not in result
    assert "[mcp_servers.context7]" in result
    assert 'default_tools_approval_mode = "approve"' in result


def test_content_loadout_does_not_generate_survives() -> None:
    """Each of these is invisible to a parse-and-serialise round trip, which is why
    the surgery is line-wise. A managed block belongs to another tool, and the
    project tables are machine state Codex writes as projects are opened."""
    result = apply_toml(LIVE, OWNED, DOCUMENT)

    assert "# a comment someone wrote" in result
    assert "# >>> nono:nolabs-ai-codex >>>" in result
    assert 'sandbox_mode = "workspace-write"' in result
    assert '[projects."/Users/me/ac"]' in result
    assert 'developer_instructions = """' in result
    assert "keep me" in result


def test_an_owned_scalar_is_replaced_rather_than_duplicated() -> None:
    result = apply_toml(LIVE, OWNED, DOCUMENT)

    assert 'model = "gpt-5.6-sol"' in result
    assert "old-model" not in result
    assert result.count("model =") == 1


def test_an_owned_scalar_lands_above_the_first_table_header() -> None:
    """TOML reads a bare key after `[table]` as a member of that table, so a key
    inserted below one silently configures something else."""
    result = apply_toml(LIVE, OWNED, DOCUMENT)
    lines = result.splitlines()

    assert lines.index('model = "gpt-5.6-sol"') < next(
        index for index, line in enumerate(lines) if line.startswith("[")
    )


def test_applying_twice_changes_nothing() -> None:
    once = apply_toml(LIVE, OWNED, DOCUMENT)

    assert apply_toml(once, OWNED, DOCUMENT) == once


def test_a_destination_that_does_not_exist_yet_renders_from_empty() -> None:
    """A clean machine has no config.toml, so the merge has to be total on empty."""
    result = apply_toml("", OWNED, DOCUMENT)

    assert 'model = "gpt-5.6-sol"' in result
    assert "[mcp_servers.context7]" in result


def test_a_table_valued_key_is_refused_by_name() -> None:
    """Flattening one would move it under whichever table precedes it, changing
    what it configures rather than failing."""
    with pytest.raises(LoadoutError, match="'nested' is a table"):
        reject_nested({"model": "x", "nested": {"a": 1}}, "codex.settings")


def test_removing_every_server_removes_the_whole_table_tree() -> None:
    """The case that separates declared ownership from derived, and the only one
    that does.

    Stripping by root means a document keeping *any* server still names
    `mcp_servers`, so a derived owned set covers the removed siblings by accident.
    Drop them all — or drop the slice — and a derived set no longer names the root
    at all, nothing strips it, and every server survives with its approval intact.

    `projects` is asserted alongside so this cannot pass against a strip that
    emptied the file.
    """
    document = 'model = "gpt-5.6-sol"\n'

    result = apply_toml(LIVE, OWNED, document)

    assert "mcp_servers" not in result
    assert '[projects."/Users/me/ac"]' in result
    assert 'model = "gpt-5.6-sol"' in result


# --- apply_json -------------------------------------------------------------

JSON_LIVE = """{
  "numStartups": 2724,
  "mcpServers": {
    "jina": {
      "type": "http",
      "url": "https://jina.example"
    }
  },
  "projects": {
    "/repo": {
      "lastCost": 0.5
    }
  }
}"""

JSON_OWNED = frozenset({"mcpServers"})


def document(*names: str) -> str:
    return json.dumps({"mcpServers": {n: {"type": "http", "url": f"https://{n}"} for n in names}})


def test_applying_what_is_already_there_is_identity() -> None:
    """The property `check` rests on: it compares the destination against the
    destination-with-loadout's-keys-applied, so a no-op edit must produce the
    same bytes or every run reports drift."""
    same = json.dumps({"mcpServers": json.loads(JSON_LIVE)["mcpServers"]})

    assert apply_json(JSON_LIVE, JSON_OWNED, same) == JSON_LIVE


def test_an_owned_key_is_replaced_where_it_sits() -> None:
    """Not popped and re-added. Moving it to the end would reshuffle the file
    every time the harness rewrote it, which `check` would report as drift."""
    applied = json.loads(apply_json(JSON_LIVE, JSON_OWNED, document("other")))

    assert list(applied) == ["numStartups", "mcpServers", "projects"]
    assert list(applied["mcpServers"]) == ["other"]


def test_keys_loadout_does_not_own_survive_untouched() -> None:
    applied = json.loads(apply_json(JSON_LIVE, JSON_OWNED, document("other")))
    original = json.loads(JSON_LIVE)

    assert applied["numStartups"] == original["numStartups"]
    assert applied["projects"] == original["projects"]


def test_an_owned_key_the_document_drops_is_removed() -> None:
    """What makes deleting a server delete it — the gap that kept Claude's mcp
    add-only while `claude mcp add-json` was the merge step."""
    applied = json.loads(apply_json(JSON_LIVE, JSON_OWNED, json.dumps({})))

    assert "mcpServers" not in applied
    assert applied["numStartups"] == 2724


def test_a_key_the_file_lacks_is_appended() -> None:
    applied = json.loads(apply_json('{"a": 1}', JSON_OWNED, document("jina")))

    assert list(applied) == ["a", "mcpServers"]


def test_the_files_own_trailing_newline_convention_is_kept() -> None:
    """Claude writes this file without one; imposing a newline would read as
    drift forever, and stripping an existing one would too."""
    assert not apply_json(JSON_LIVE, JSON_OWNED, document("jina")).endswith("\n")
    assert apply_json(JSON_LIVE + "\n", JSON_OWNED, document("jina")).endswith("\n")


def test_an_absent_destination_starts_from_an_empty_object() -> None:
    applied = json.loads(apply_json("", JSON_OWNED, document("jina")))

    assert list(applied["mcpServers"]) == ["jina"]


def test_a_destination_that_is_not_an_object_is_refused() -> None:
    with pytest.raises(LoadoutError, match="not an object"):
        apply_json("[1, 2]", JSON_OWNED, document("jina"))


def test_a_multiline_destination_value_is_refused_not_corrupted() -> None:
    """Replacing the assignment line would orphan the lines below it and the file
    would stop parsing — verified against ~/.codex/config.toml's developer_instructions,
    which is why that key stayed outside loadout."""
    existing = 'model = "old"\ndeveloper_instructions = """\nbody\n"""\nother = 1\n'

    with pytest.raises(LoadoutError, match="across several lines"):
        apply_toml(
            existing, frozenset({"developer_instructions"}), 'developer_instructions = "new"\n'
        )


def test_a_newline_inside_an_escaped_value_merges_fine() -> None:
    """The distinction the guard rests on: the renderer escapes a multi-line value
    onto one line, and that form replaces cleanly. Refusing on the value rather than
    the destination's shape would block this, which works."""
    existing = 'model = "old"\ndeveloper_instructions = "a\\nb\\n"\nother = 1\n'

    out = apply_toml(
        existing, frozenset({"developer_instructions"}), 'developer_instructions = "c\\nd\\n"\n'
    )

    assert tomllib.loads(out)["developer_instructions"] == "c\nd\n"
    assert tomllib.loads(out)["other"] == 1


def test_an_unowned_multiline_value_is_left_alone() -> None:
    """The guard fires only for a key loadout claims; a multi-line value belonging to
    the harness passes through untouched, which is the whole point of the merge."""
    existing = 'developer_instructions = """\nbody\n"""\nmodel = "old"\n'

    out = apply_toml(existing, frozenset({"model"}), 'model = "new"\n')

    assert tomllib.loads(out)["developer_instructions"] == "body\n"
    assert tomllib.loads(out)["model"] == "new"


def test_a_multiline_owned_key_is_removed_when_no_value_is_rendered() -> None:
    """Declaring a key owned and rendering nothing for it is how loadout evicts a key
    another tool keeps writing — nono's `developer_instructions` block on Codex.
    Removing needs none of the care replacing does: skip to the closing delimiter."""
    existing = 'model = "keep"\ndeveloper_instructions = """\nnono says something\n"""\nother = 1\n'

    out = apply_toml(existing, frozenset({"developer_instructions"}), "")

    parsed = tomllib.loads(out)
    assert "developer_instructions" not in parsed
    assert parsed["model"] == "keep" and parsed["other"] == 1
    assert "nono says" not in out


def test_removing_a_multiline_key_leaves_no_orphaned_body() -> None:
    """The failure this guards: the body lines are not assignments, so nothing else
    would drop them and the file would stop parsing."""
    existing = 'developer_instructions = """\nline one\nline two\n"""\nmodel = "keep"\n'

    out = apply_toml(existing, frozenset({"developer_instructions"}), "")

    assert "line one" not in out and "line two" not in out
    assert tomllib.loads(out) == {"model": "keep"}
