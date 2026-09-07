from __future__ import annotations

import tomllib

import pytest

from loadout.errors import LoadoutError
from loadout.permissions.renderers import _fragment_keys, render_codex_settings
from loadout.permissions.rules import EMPTY_RULES
from loadout.surgery import apply_toml, concat_documents

OWNED = frozenset({"skills.max_context_tokens"})
DOCUMENT = "[skills]\nmax_context_tokens = 10000\n"


@pytest.mark.parametrize(
    "existing",
    [
        "skills.max_context_tokens = 4000\nskills.other = true\n",
        '"skills" . "max_context_tokens" = 4000\nskills.other = true\n',
        "['skills'] # personal\n'max_context_tokens' = 4000\nother = true\n",
        "[skills]\nother = true\n\n[skills.nested]\nkeep = 7\n",
        "skills.other = true\n",
    ],
)
def test_nested_keys_support_dotted_and_quoted_toml(existing: str) -> None:
    result = apply_toml(existing, OWNED, DOCUMENT)
    expected = tomllib.loads(existing)
    expected["skills"]["max_context_tokens"] = 10000
    assert tomllib.loads(result) == expected
    assert apply_toml(result, OWNED, DOCUMENT) == result


@pytest.mark.parametrize("delimiter", ['"""', "'''"])
def test_table_and_assignment_lookalikes_inside_strings_are_untouched(delimiter: str) -> None:
    foreign = f"""# untouched
developer_instructions = {delimiter}
[skills]
max_context_tokens = 17
[[skills.config]]
{delimiter}

[skills]
other = [
    "a", # keep
    "b",
]
"""
    result = apply_toml(foreign, OWNED, DOCUMENT)
    assert result.replace("max_context_tokens = 10000\n", "") == foreign
    assert tomllib.loads(result)["skills"] == {"other": ["a", "b"], "max_context_tokens": 10000}
    assert apply_toml(result, OWNED, DOCUMENT) == result


@pytest.mark.parametrize(
    "value", ['"""\nold\n[skills]\nmax_context_tokens = 3\n"""', "[\n1,\n2,\n]"]
)
def test_replacing_multiline_values_removes_the_whole_owned_assignment(value: str) -> None:
    existing = f"[skills]\nmax_context_tokens = {value}\nother = true\n"
    assert (
        apply_toml(existing, OWNED, DOCUMENT)
        == "[skills]\nmax_context_tokens = 10000\nother = true\n"
    )
    assert apply_toml(existing, OWNED, "") == "[skills]\nother = true\n"


@pytest.mark.parametrize(
    "existing",
    [
        "skills = { other = true, max_context_tokens = 4000 }\n",
        "skills = false\n",
        "[[skills]]\nmax_context_tokens = 4000\n",
    ],
)
def test_unrepresentable_parent_shapes_refuse_without_widening_ownership(existing: str) -> None:
    with pytest.raises(LoadoutError, match=r"inline or scalar|array table"):
        apply_toml(existing, OWNED, DOCUMENT)


def test_literal_dotted_keys_are_distinct_from_nested_keys() -> None:
    existing = '"skills.max_context_tokens" = 17\n\n[skills]\nmax_context_tokens = 4000\n'
    result = apply_toml(existing, OWNED, DOCUMENT)
    assert tomllib.loads(result) == {
        "skills.max_context_tokens": 17,
        "skills": {"max_context_tokens": 10000},
    }
    literal = apply_toml(
        result, frozenset({'"skills.max_context_tokens"'}), '"skills.max_context_tokens" = 23\n'
    )
    assert tomllib.loads(literal) == {
        "skills.max_context_tokens": 23,
        "skills": {"max_context_tokens": 10000},
    }


def test_quoted_table_paths_with_brackets_and_escaped_quotes() -> None:
    existing = '[profiles."a.]b\\"c"] # keep header\nother = true\nbudget = 4\n'
    result = apply_toml(
        existing, frozenset({'profiles."a.]b\\"c".budget'}), '[profiles."a.]b\\"c"]\nbudget = 10\n'
    )
    assert result == existing.replace("budget = 4", "budget = 10")
    assert apply_toml(result, frozenset({'profiles."a.]b\\"c".budget'}), "") == existing.replace(
        "budget = 4\n", ""
    )


def test_arrays_of_tables_are_owned_as_one_field() -> None:
    existing = '[skills]\nother = true\n\n[[skills.config]]\npath = "old"\n\n[[skills.config]]\npath = "second"\n'
    content = {"skills": {"config": [{"path": "one"}, {"path": "two"}]}}
    document = render_codex_settings(EMPTY_RULES, content)
    owned = frozenset({"skills.config"})
    result = apply_toml(existing, owned, document)
    assert tomllib.loads(result)["skills"] == {"other": True, **content["skills"]}
    assert apply_toml(result, owned, document) == result
    assert tomllib.loads(apply_toml(result, owned, ""))["skills"] == {"other": True}


def test_array_subtables_stay_with_their_element_when_shape_changes() -> None:
    existing = '[skills]\nother = true\n\n[[skills.config]]\npath = "old-one"\n[skills.config.options]\nx = 1\n\n[[skills.config]]\npath = "old-two"\n'
    content = {"skills": {"config": [{"path": "one"}, {"path": "two", "options": {"x": 2}}]}}
    document = render_codex_settings(EMPTY_RULES, content)
    owned = frozenset({"skills.config"})
    result = apply_toml(existing, owned, document)
    assert tomllib.loads(result)["skills"] == {"other": True, **content["skills"]}
    assert apply_toml(result, owned, document) == result


def test_concat_preserves_multiline_scalar_bytes_above_tables() -> None:
    scalar = 'developer_instructions = """\n[skills]\nkeep\n"""\n'
    combined = concat_documents((DOCUMENT, scalar))
    assert scalar in combined
    assert tomllib.loads(combined) == {
        "developer_instructions": "[skills]\nkeep\n",
        "skills": {"max_context_tokens": 10000},
    }


def test_foreign_crlf_spacing_and_missing_final_newline_survive() -> None:
    existing = '# keep\r\n\r\n\r\n[skills]\r\nother = true\r\n\r\n[project]\r\nname = "x"'
    result = apply_toml(existing, OWNED, DOCUMENT)
    assert result.replace("max_context_tokens = 10000\n", "") == existing
    assert apply_toml(result, OWNED, DOCUMENT) == result


def test_invalid_toml_is_refused_before_edits() -> None:
    with pytest.raises(LoadoutError, match="cannot edit invalid TOML"):
        apply_toml("[skills\n", OWNED, DOCUMENT)


def test_inserting_into_a_header_without_a_final_newline() -> None:
    result = apply_toml("[skills]", OWNED, DOCUMENT)
    assert result == DOCUMENT
    assert apply_toml(result, OWNED, DOCUMENT) == result


def test_non_ascii_keys_round_trip_through_rendering_and_ownership() -> None:
    content = {"profiles": {"coding 🐍": {"budget": 10000}}}
    document = render_codex_settings(EMPTY_RULES, content)
    owned = _fragment_keys(content)
    assert owned == {'profiles."coding 🐍".budget'}
    result = apply_toml("", owned, document)
    assert tomllib.loads(result) == content
    assert apply_toml(result, owned, document) == result


@pytest.mark.parametrize("existing", ["", "[skills]\nconfig = []\n", "[skills]\nconfig = false\n"])
def test_first_array_insertion_is_idempotent(existing: str) -> None:
    content = {"skills": {"config": [{"path": "one"}, {"path": "two"}]}}
    document = concat_documents((render_codex_settings(EMPTY_RULES, content),))
    owned = _fragment_keys(content)
    result = apply_toml(existing, owned, document)
    assert tomllib.loads(result) == content
    assert apply_toml(result, owned, document) == result


@pytest.mark.parametrize("separator", ["\u0085", "\u2028", "\u2029"])
@pytest.mark.parametrize("lookalike", ["[skills]", "skills.max_context_tokens = 4"])
def test_unicode_separators_stay_inside_comments(separator: str, lookalike: str) -> None:
    existing = f"# personal{separator}{lookalike}\nmax_context_tokens = 4000\n"
    result = apply_toml(existing, OWNED, DOCUMENT)
    assert result.startswith(existing)
    assert tomllib.loads(result) == {
        "max_context_tokens": 4000,
        "skills": {"max_context_tokens": 10000},
    }
    assert apply_toml(result, OWNED, DOCUMENT) == result


@pytest.mark.parametrize("delimiter", ['"""', "'''", "["])
def test_multiline_values_require_linear_parsing_work(
    monkeypatch: pytest.MonkeyPatch, delimiter: str
) -> None:
    body = ('"instruction",\n' if delimiter == "[" else "instruction text\n") * 500
    closing = "]" if delimiter == "[" else delimiter
    foreign = f"developer_instructions = {delimiter}\n{body}{closing}\n"
    loads = tomllib.loads
    parsed_bytes = 0

    def counted_loads(text: str) -> dict[str, object]:
        nonlocal parsed_bytes
        parsed_bytes += len(text)
        return loads(text)

    monkeypatch.setattr(tomllib, "loads", counted_loads)
    result = apply_toml(foreign, OWNED, DOCUMENT)
    assert result.startswith(foreign)
    assert loads(result) == {**loads(foreign), "skills": {"max_context_tokens": 10000}}
    assert parsed_bytes < len(foreign) * 4


@pytest.mark.parametrize(
    "value",
    [
        r'"brackets [ ] { } # quotes \" and slash \\"',
        '"""line\nends with a quote""""',
        '"""line\nends with two quotes"""""',
        "'''line\nends with a quote''''",
        "'''line\nends with two quotes'''''",
        r'"""escaped quote \""" is still text' + '\nclosing"""',
        '"""\\\n  content\\\n    [skills]\n"""',
        '[\n{ name = "a]#[]", things = [{ x = 1 }] }, # ]\n"""\n[skills]\n""",\n]',
    ],
)
def test_statement_scanner_preserves_string_and_container_boundaries(value: str) -> None:
    foreign = f'developer_instructions = {value}\nother = "kept"\n'
    expected = tomllib.loads(foreign)
    result = apply_toml(foreign, OWNED, DOCUMENT)
    assert result.startswith(foreign)
    assert tomllib.loads(result) == {**expected, "skills": {"max_context_tokens": 10000}}
    assert apply_toml(result, OWNED, DOCUMENT) == result

    replaced = apply_toml(
        foreign, frozenset({"developer_instructions"}), 'developer_instructions = "new"\n'
    )
    assert tomllib.loads(replaced) == {"developer_instructions": "new", "other": "kept"}
