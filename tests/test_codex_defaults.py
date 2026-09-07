from __future__ import annotations

import json
import tomllib
from pathlib import Path

import pytest

import loadout
from loadout.emit import check_all, collect_notices, write_all
from loadout.errors import LoadoutError
from loadout.record import read_record, render_record

FRAGMENT = {"model": "gpt-5.6-sol", "model_reasoning_effort": "max"}

EXISTING = """# hand-written
sandbox_mode = "workspace-write"

[projects."/Users/me/one"]
trust_level = "trusted"
"""


def build(root: Path, fragment: dict[str, object]) -> Path:
    (root / "defaults").mkdir(parents=True, exist_ok=True)
    (root / "loadout.toml").write_text(
        '[[source]]\nname = "test"\npath = "."\n\n[codex]\ndefaults = "codex"\n', encoding="utf-8"
    )
    (root / "permissions.toml").write_text('[shell]\nallow = ["ls"]\n', encoding="utf-8")
    (root / "defaults" / "codex.json").write_text(json.dumps(fragment), encoding="utf-8")
    return root


def destination(fake_home: Path) -> Path:
    return fake_home / ".codex" / "config.toml"


def prepare(root: Path, fake_home: Path, fragment: dict[str, object]) -> Path:
    build(root, fragment)
    target = destination(fake_home)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(EXISTING, encoding="utf-8")
    return target


def test_managed_keys_reach_config_toml_without_disturbing_the_rest(
    tmp_path: Path, fake_home: Path
) -> None:
    target = prepare(tmp_path, fake_home, FRAGMENT)

    write_all(tmp_path)
    result = target.read_text(encoding="utf-8")

    assert 'model = "gpt-5.6-sol"' in result
    assert 'sandbox_mode = "workspace-write"' in result
    assert '[projects."/Users/me/one"]' in result
    assert "# hand-written" in result


def test_a_key_removed_from_the_fragment_is_removed_from_config_toml(
    tmp_path: Path, fake_home: Path
) -> None:
    """The reason the record exists.

    Ownership here is derived — the key names are the user's — so the fragment
    alone cannot say a key was ever managed. Without the recorded set unioned in,
    a dropped key is never named, nothing strips it, and it survives every later
    run (ADR 0017).

    `model` is asserted alongside so this cannot pass against a strip that emptied
    the managed keys entirely.
    """
    target = prepare(tmp_path, fake_home, {**FRAGMENT, "plan_mode_reasoning_effort": "max"})
    write_all(tmp_path)
    assert "plan_mode_reasoning_effort" in target.read_text(encoding="utf-8")

    (tmp_path / "defaults" / "codex.json").write_text(json.dumps(FRAGMENT), encoding="utf-8")
    write_all(tmp_path)
    result = target.read_text(encoding="utf-8")

    assert "plan_mode_reasoning_effort" not in result
    assert 'model = "gpt-5.6-sol"' in result
    assert 'sandbox_mode = "workspace-write"' in result


def test_the_record_is_written_beside_its_fragment(tmp_path: Path, fake_home: Path) -> None:
    prepare(tmp_path, fake_home, FRAGMENT)

    write_all(tmp_path)

    record = tmp_path / "defaults" / "codex.owned"
    keys = [line for line in record.read_text(encoding="utf-8").splitlines() if line[:1] != "#"]
    assert keys == ["model", "model_reasoning_effort"]


def test_a_settings_key_nobody_manages_is_left_alone(tmp_path: Path, fake_home: Path) -> None:
    """The slice strips only what it manages, so a machine's own Codex settings
    survive a sync that never mentions them."""
    target = prepare(tmp_path, fake_home, FRAGMENT)

    write_all(tmp_path)

    assert 'sandbox_mode = "workspace-write"' in target.read_text(encoding="utf-8")


def test_a_stale_record_is_reported_as_drift(tmp_path: Path, fake_home: Path) -> None:
    """The record is an input as well as an output, so a hand edit to it silently
    changes what gets stripped. `check` has to say so rather than fix it."""
    prepare(tmp_path, fake_home, FRAGMENT)
    write_all(tmp_path)
    record = tmp_path / "defaults" / "codex.owned"
    record.write_text("# tampered\nmodel\n", encoding="utf-8")

    assert [path for path, _, _ in check_all(tmp_path)] == [record]


def test_a_defaults_fragment_is_not_parsed_as_a_plugins_fragment(
    tmp_path: Path, fake_home: Path
) -> None:
    """Notices read marketplaces out of a *plugins* fragment, which parses the
    document as one. Done for every slice it rejects any other shape: a defaults
    fragment's keys — or a hooks fragment's event names — read as stray sections
    and the whole render fails with `plugins: unknown section(s) model`.

    Latent until a second source-slice kind existed; `hooks` would have hit it
    just as hard on any agent that declared one.
    """
    prepare(tmp_path, fake_home, FRAGMENT)

    assert collect_notices(tmp_path) is not None
    write_all(tmp_path)


SKILL_OVERRIDES = """# Personal skill choices
[[skills.config]] # keep this comment
path = '/skills/one'
enabled = false

[[skills.config]]
path = "/skills/two"
enabled = true
"""


@pytest.mark.parametrize("initial", ["", "max_context_tokens = 4000\n"])
def test_nested_defaults_preserve_skill_overrides_and_unrelated_bytes(
    tmp_path: Path, fake_home: Path, initial: str
) -> None:
    target = prepare(tmp_path, fake_home, {"skills": {"max_context_tokens": 10000}})
    original = (
        EXISTING + "\n[skills] # hand-written\n" + initial + "other = true\n\n" + SKILL_OVERRIDES
    )
    target.write_text(original, encoding="utf-8")

    write_all(tmp_path)
    result = target.read_text(encoding="utf-8")

    assert tomllib.loads(result)["skills"] == {
        "max_context_tokens": 10000,
        "other": True,
        "config": [
            {"path": "/skills/one", "enabled": False},
            {"path": "/skills/two", "enabled": True},
        ],
    }
    expected_foreign = original.replace(initial, "", 1) if initial else original
    assert result.replace("max_context_tokens = 10000\n", "") == expected_foreign
    assert read_record(tmp_path / "defaults/codex.owned") == {"skills.max_context_tokens"}
    assert check_all(tmp_path) == []
    write_all(tmp_path)
    assert target.read_text(encoding="utf-8") == result


def test_nested_removal_leaves_sibling_settings_and_array_tables(
    tmp_path: Path, fake_home: Path
) -> None:
    target = prepare(tmp_path, fake_home, {"skills": {"max_context_tokens": 10000}})
    target.write_text(EXISTING + "\n" + SKILL_OVERRIDES, encoding="utf-8")
    write_all(tmp_path)
    assert tomllib.loads(target.read_text())["skills"]["max_context_tokens"] == 10000

    (tmp_path / "defaults/codex.json").write_text('{"skills": {}}\n')
    write_all(tmp_path)
    result = target.read_text()

    assert tomllib.loads(result)["skills"] == tomllib.loads(SKILL_OVERRIDES)["skills"]
    assert SKILL_OVERRIDES in result
    assert read_record(tmp_path / "defaults/codex.owned") == frozenset()
    assert check_all(tmp_path) == []


def test_nested_default_updates_and_explicit_removal(tmp_path: Path, fake_home: Path) -> None:
    target = prepare(tmp_path, fake_home, {"skills": {"max_context_tokens": 4000, "other": True}})
    write_all(tmp_path)
    assert tomllib.loads(target.read_text())["skills"]["max_context_tokens"] == 4000
    fragment = tmp_path / "defaults/codex.json"
    fragment.write_text('{"skills": {"max_context_tokens": 10000, "other": true}}')
    write_all(tmp_path)
    assert tomllib.loads(target.read_text())["skills"]["max_context_tokens"] == 10000
    fragment.write_text('{"skills": {"other": true}, "$remove": ["skills.max_context_tokens"]}')
    write_all(tmp_path)
    assert tomllib.loads(target.read_text())["skills"] == {"other": True}
    assert read_record(tmp_path / "defaults/codex.owned") == {
        "skills.max_context_tokens",
        "skills.other",
    }
    assert check_all(tmp_path) == []


@pytest.mark.parametrize(
    "content",
    [
        {"mcp_servers": {"test": {"url": "https://example.test"}}},
        {"skills": {"max_context_tokens": 10000}, "$remove": ["skills"]},
        {"skills": {"max_context_tokens": 10000}, "$remove": ["skills.max_context_tokens"]},
    ],
)
def test_overlapping_defaults_ownership_is_refused(
    tmp_path: Path, fake_home: Path, content: dict[str, object]
) -> None:
    target = prepare(tmp_path, fake_home, content)
    with pytest.raises(LoadoutError, match=r"owned|both"):
        write_all(tmp_path)
    assert target.read_text() == EXISTING


def test_recorded_nested_ownership_cannot_overlap_another_slice(
    tmp_path: Path, fake_home: Path
) -> None:
    target = prepare(tmp_path, fake_home, {"skills": {"max_context_tokens": 10000}})
    (tmp_path / "defaults/codex.owned").write_text(
        render_record(frozenset({"mcp_servers.test.url"}))
    )
    with pytest.raises(LoadoutError, match="already owned by another slice"):
        write_all(tmp_path)
    assert target.read_text() == EXISTING


def test_literal_keys_are_quoted_in_the_ownership_record(tmp_path: Path, fake_home: Path) -> None:
    target = prepare(
        tmp_path,
        fake_home,
        {"skills": {"max_context_tokens": 10000}, "skills.max_context_tokens": 17},
    )
    write_all(tmp_path)
    assert read_record(tmp_path / "defaults/codex.owned") == {
        '"skills.max_context_tokens"',
        "skills.max_context_tokens",
    }
    assert tomllib.loads(target.read_text())["skills.max_context_tokens"] == 17
    (tmp_path / "defaults/codex.json").write_text('{"skills": {"max_context_tokens": 10000}}')
    write_all(tmp_path)
    assert tomllib.loads(target.read_text()) == {
        **tomllib.loads(EXISTING),
        "skills": {"max_context_tokens": 10000},
    }
    assert check_all(tmp_path) == []


def test_global_cli_syncs_nested_defaults_without_force(tmp_path: Path, fake_home: Path) -> None:
    target = prepare(tmp_path, fake_home, {"skills": {"max_context_tokens": 10000}})
    machine = fake_home / ".config/loadout/config.toml"
    machine.parent.mkdir(parents=True)
    machine.write_text(f"source = {json.dumps(str(tmp_path))}\n")
    target.write_text(EXISTING + "\n" + SKILL_OVERRIDES)
    fragment = tmp_path / "defaults/codex.json"
    for value in (10000, 4000, None):
        fragment.write_text(json.dumps({"skills": {"max_context_tokens": value}}))
        assert loadout.main(["sync", "--global"]) == 0
        expected = tomllib.loads(SKILL_OVERRIDES)["skills"]
        if value is not None:
            expected["max_context_tokens"] = value
        assert tomllib.loads(target.read_text())["skills"] == expected
        assert SKILL_OVERRIDES in target.read_text()
        assert loadout.main(["check", "--global"]) == 0


@pytest.mark.parametrize("keep_literal", [True, False])
def test_legacy_literal_ownership_does_not_claim_nested_overrides(
    tmp_path: Path, fake_home: Path, keep_literal: bool
) -> None:
    literal = {"skills.config": [{"path": "/managed/new", "enabled": True}]}
    target = prepare(tmp_path, fake_home, literal if keep_literal else {})
    foreign = EXISTING + "\n" + SKILL_OVERRIDES
    target.write_text(foreign + '\n[["skills.config"]]\npath = "/managed/old"\n')
    record = tmp_path / "defaults/codex.owned"
    record.write_text(
        "# Keys loadout manages in this destination. Generated; edit the fragment instead.\nskills.config\n"
    )

    write_all(tmp_path)
    result = target.read_text()
    assert tomllib.loads(result) == {**tomllib.loads(foreign), **(literal if keep_literal else {})}
    assert result.startswith(foreign)
    assert read_record(record) == (frozenset({'"skills.config"'}) if keep_literal else frozenset())
    assert check_all(tmp_path) == []
    write_all(tmp_path)
    assert target.read_text() == result


def test_file_backed_sync_preserves_foreign_crlf_bytes(tmp_path: Path, fake_home: Path) -> None:
    target = prepare(tmp_path, fake_home, {"skills": {"max_context_tokens": 10000}})
    foreign = (EXISTING + "\n" + SKILL_OVERRIDES).replace("\n", "\r\n").encode()
    target.write_bytes(foreign)
    fragment = tmp_path / "defaults/codex.json"
    for value in (10000, 4000, None):
        fragment.write_text(json.dumps({"skills": {"max_context_tokens": value}}))
        assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
        result = target.read_bytes()
        assert result.startswith(foreign)
        expected = tomllib.loads(foreign.decode())
        if value is not None:
            expected["skills"]["max_context_tokens"] = value
        assert tomllib.loads(result.decode()) == expected
        assert loadout.main(["check", "--root", str(tmp_path)]) == 0
        assert loadout.main(["sync", "--root", str(tmp_path)]) == 0
        assert target.read_bytes() == result


def test_first_array_sync_is_immediately_clean(tmp_path: Path, fake_home: Path) -> None:
    content = {"skills": {"config": [{"path": "/one"}, {"path": "/two"}]}}
    target = prepare(tmp_path, fake_home, content)
    write_all(tmp_path)
    result = target.read_bytes()
    assert tomllib.loads(result.decode()) == {**tomllib.loads(EXISTING), **content}
    assert check_all(tmp_path) == []
    write_all(tmp_path)
    assert target.read_bytes() == result


def test_unknown_ownership_format_refuses_before_destination_writes(
    tmp_path: Path, fake_home: Path
) -> None:
    target = prepare(tmp_path, fake_home, {"skills": {"max_context_tokens": 10000}})
    record = tmp_path / "defaults/codex.owned"
    record.write_text("# loadout-owned-format: 99\nskills.config\n")
    with pytest.raises(LoadoutError, match="unsupported ownership record format"):
        write_all(tmp_path)
    assert target.read_text() == EXISTING
    assert record.read_text() == "# loadout-owned-format: 99\nskills.config\n"
