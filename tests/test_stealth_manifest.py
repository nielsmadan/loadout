from __future__ import annotations

from pathlib import Path

import pytest

from loadout.errors import LoadoutError
from loadout.stealth_manifest import (
    STEALTH_EXTRAS,
    STEALTH_SLICES,
    holds_stealth,
    load_stealth_manifest,
)

HEAD = """
[stealth]
targets = ["~/wrksp/app*"]

[[source]]
name = "app"
path = "."
"""

FIXTURE_MANIFEST = Path(__file__).parent / "fixtures" / "loadout.toml"


def stealth(tmp_path: Path, body: str, head: str = HEAD) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "loadout.toml").write_text(head + body, encoding="utf-8")
    return tmp_path


def test_a_valid_stealth_source_parses(tmp_path: Path) -> None:
    folder = stealth(
        tmp_path,
        """
[all]
instructions = ["b", "a"]

[claude]
[codex]
""",
    )
    loaded = load_stealth_manifest(folder)
    assert loaded.settings.targets == ("~/wrksp/app*",)
    assert loaded.harnesses == ("claude", "codex")
    assert [(t.name, t.fragments) for t in loaded.manifest.targets] == [
        ("claude", ("b", "a")),
        ("codex", ("b", "a")),
    ]


def test_a_harness_key_wins_over_the_same_key_in_all_and_all_still_reaches_the_rest(
    tmp_path: Path,
) -> None:
    folder = stealth(
        tmp_path,
        """
[all]
instructions = ["shared"]

[claude]
instructions = ["mine"]

[codex]
""",
    )
    manifest = load_stealth_manifest(folder).manifest
    assert [(t.name, t.fragments) for t in manifest.targets] == [
        ("claude", ("mine",)),
        ("codex", ("shared",)),
    ]


@pytest.mark.parametrize(
    ("block", "slice_name", "reason"),
    [
        ("[claude]\nhooks = []", "hooks", "does not merge"),
        ("[claude]\nplugins = []", "plugins", "does not merge"),
        ("[codex]\nagents = []", "agents", "does not merge"),
        ("[droid]\nmodule-config = []", "module-config", "does not merge"),
        ("[codex]\ndefaults = []", "defaults", "does not merge"),
        ("[claude]\nmcp-permissions = []", "mcp-permissions", "MCP approvals are in `permissions`"),
        ("[claude]\npreserve = []", "preserve", "owns only the keys it writes"),
        ("[pi]\nprofile = 'x'", "profile", "one profile"),
    ],
)
def test_a_disallowed_slice_is_refused_with_its_reason(
    tmp_path: Path, block: str, slice_name: str, reason: str
) -> None:
    harness = block[1 : block.index("]")]
    with pytest.raises(LoadoutError) as raised:
        load_stealth_manifest(stealth(tmp_path, "\n" + block + "\n"))
    assert f"{harness}.{slice_name}" in str(raised.value)
    assert reason in str(raised.value)


def test_a_block_naming_only_instructions_keeps_the_automatic_slices_that_are_allowed(
    tmp_path: Path,
) -> None:
    folder = stealth(tmp_path, '\n[claude]\ninstructions = ["a"]\n')
    manifest = load_stealth_manifest(folder).manifest
    assert [t.name for t in manifest.permissions] == ["claude", "claude-mcp"]
    assert [s.harness for s in manifest.skills] == ["claude"]
    assert [t.name for t in manifest.targets] == ["claude"]
    assert manifest.agents == ()
    assert manifest.module_config == ()
    assert "claude-mcp-permissions" not in {t.name for t in manifest.permissions}


def test_a_named_server_list_is_refused_and_false_switches_the_slice_off(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match="servers are not picked by name"):
        load_stealth_manifest(stealth(tmp_path / "named", '\n[claude]\nmcp = ["x"]\n'))
    off = load_stealth_manifest(stealth(tmp_path / "off", "\n[claude]\nmcp = false\n"))
    assert [t.name for t in off.manifest.permissions] == ["claude"]


def test_an_all_mcp_list_is_refused_naming_all(tmp_path: Path) -> None:
    folder = stealth(tmp_path, '\n[all]\nmcp = ["x"]\n\n[claude]\n')
    with pytest.raises(LoadoutError, match=r"\[all\] mcp: servers are not picked by name"):
        load_stealth_manifest(folder)


@pytest.mark.parametrize("block", ["[claude]\nmcp = []", "[all]\nmcp = []\n\n[claude]"])
def test_an_empty_mcp_list_is_refused_like_a_named_one(tmp_path: Path, block: str) -> None:
    with pytest.raises(LoadoutError, match=r"mcp: servers are not picked by name.*`mcp = false`"):
        load_stealth_manifest(stealth(tmp_path, f"\n{block}\n"))


def test_mcp_permissions_gets_claudes_reason_only_for_claude(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError) as codex:
        load_stealth_manifest(stealth(tmp_path / "c", "\n[codex]\nmcp-permissions = []\n"))
    assert (
        "codex.mcp-permissions is refused in a stealth source: a stealth source does not render it"
        in str(codex.value)
    )
    with pytest.raises(LoadoutError) as shared:
        load_stealth_manifest(
            stealth(tmp_path / "a", "\n[all]\nmcp-permissions = []\n\n[claude]\n")
        )
    assert "applies to no declared harness: Claude's MCP approvals are in `permissions`" in str(
        shared.value
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("\n[instructions.x]\n\n[claude]\n", r"\[instructions\.\*\] is refused"),
        ("\n[permissions.x]\n\n[claude]\n", r"\[permissions\.\*\] is refused"),
        ("\n[claude]\n", "extends and remove are refused"),
    ],
)
def test_the_old_spellings_and_remove_are_refused(tmp_path: Path, body: str, message: str) -> None:
    head = 'remove = ["x"]\n' + HEAD if "extends and remove" in message else HEAD
    with pytest.raises(LoadoutError, match=message):
        load_stealth_manifest(stealth(tmp_path, body, head))


@pytest.mark.parametrize(
    ("targets_line", "message"),
    [
        ("targets = []", "non-empty list"),
        ("targets = [1]", "non-empty list"),
        ('targets = ["~/x"]\nbogus = 1', "unknown key.*bogus"),
    ],
)
def test_bad_stealth_table_entries_are_refused(
    tmp_path: Path, targets_line: str, message: str
) -> None:
    head = HEAD.replace('targets = ["~/wrksp/app*"]', targets_line)
    with pytest.raises(LoadoutError, match=message):
        load_stealth_manifest(stealth(tmp_path, "\n[claude]\n", head))


@pytest.mark.parametrize("key", ["follow_worktrees", "git_hook"])
def test_a_reserved_key_set_true_is_refused_as_not_available_yet(tmp_path: Path, key: str) -> None:
    head = HEAD.replace('targets = ["~/wrksp/app*"]', f'targets = ["~/x"]\n{key} = true')
    folder = stealth(tmp_path, "\n[claude]\n", head)
    with pytest.raises(LoadoutError) as raised:
        load_stealth_manifest(folder)
    assert str(raised.value) == (
        f"{folder / 'loadout.toml'}: `[stealth] {key}` is not available yet; it arrives in a "
        "later release — set it to false or remove it"
    )


def test_a_relative_target_is_refused(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match="absolute or"):
        load_stealth_manifest(
            stealth(tmp_path / "b", "\n[claude]\n", HEAD.replace("~/wrksp/app*", "wrksp/app*"))
        )


def test_extends_is_refused_and_invalid_toml_names_the_file(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match=r"one profile, its loadout\.toml"):
        load_stealth_manifest(stealth(tmp_path / "a", "\n[claude]\n", 'extends = "x"\n' + HEAD))
    broken = tmp_path / "b"
    broken.mkdir()
    (broken / "loadout.toml").write_text("[stealth\n", encoding="utf-8")
    with pytest.raises(LoadoutError, match=r"loadout\.toml: invalid TOML"):
        load_stealth_manifest(broken)


def test_an_all_key_reaches_only_the_harnesses_that_allow_it(tmp_path: Path) -> None:
    folder = stealth(tmp_path, '\n[all]\nsettings = "local"\n\n[claude]\n[codex]\n')
    permissions = {t.name: t for t in load_stealth_manifest(folder).manifest.permissions}
    assert permissions["claude"].settings == ("local",)
    assert permissions["codex"].settings == ()


def test_an_all_key_no_declared_harness_allows_is_refused(tmp_path: Path) -> None:
    folder = stealth(tmp_path, '\n[all]\nsettings = "local"\n\n[codex]\n')
    with pytest.raises(LoadoutError, match="applies to no declared harness"):
        load_stealth_manifest(folder)


def test_holds_stealth_source_tells_a_stealth_source_from_a_global_manifest(tmp_path: Path) -> None:
    folder = stealth(tmp_path / "o", "\n[claude]\n")
    assert holds_stealth(folder)
    assert not holds_stealth(FIXTURE_MANIFEST.parent)
    assert not holds_stealth(tmp_path / "missing")
    (tmp_path / "scalar").mkdir()
    (tmp_path / "scalar" / "loadout.toml").write_text("stealth = 1\n", encoding="utf-8")
    assert not holds_stealth(tmp_path / "scalar")


def test_every_harness_allows_the_same_slices_and_only_claude_and_droid_take_settings() -> None:
    assert {h: sorted(s) for h, s in STEALTH_SLICES.items()} == {
        h: ["instructions", "mcp", "permissions", "skills"] for h in STEALTH_SLICES
    }
    assert sorted(h for h, extras in STEALTH_EXTRAS.items() if "settings" in extras) == [
        "claude",
        "droid",
    ]
