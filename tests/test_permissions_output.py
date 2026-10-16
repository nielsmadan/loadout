from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from loadout.emit import render_all
from loadout.errors import LoadoutError
from loadout.manifest import MANIFEST_NAME, load_manifest
from loadout.output import Merged
from registration import register

FIXTURES = Path(__file__).parent / "fixtures"
EXPECTED = FIXTURES / "expected" / "default"

# Read from the manifest, so a target added to the fixture is compared without being
# listed here too. The one destination is the [claude] agent block's settings.json.
PERMISSION_OUTPUTS = (
    *(
        str(t.path)
        for t in load_manifest(FIXTURES / MANIFEST_NAME).permissions
        if t.path is not None
    ),
    "destinations/settings.json",
)


def _repo_relative(rendered: dict[Path, Any], root: Path) -> dict[str, Any]:
    """In-repo outputs by their path, and destinations under ~ by file name, the way the
    expected tree holds them: a merged output as the part loadout owns."""
    return {
        str(p.relative_to(root)) if root in p.parents else f"destinations/{p.name}": (
            c.document if isinstance(c, Merged) else c
        )
        for p, c in rendered.items()
    }


def test_every_permission_output_matches_the_expected_output(root: Path) -> None:
    register(root)
    rendered = _repo_relative(render_all(root), root)
    for name in PERMISSION_OUTPUTS:
        expected = (EXPECTED / name).read_text(encoding="utf-8")
        assert rendered[name] == expected, f"{name} differs from its expected output"


def test_every_permission_target_is_rendered(root: Path) -> None:
    register(root)
    rendered = set(_repo_relative(render_all(root), root))
    for name in PERMISSION_OUTPUTS:
        assert name in rendered


def test_rendering_does_not_require_the_output_to_exist(root: Path) -> None:
    """Acceptance criterion 2 — a clean checkout must render."""
    register(root)
    for name in PERMISSION_OUTPUTS:
        (root / name).unlink(missing_ok=True)
    rendered = _repo_relative(render_all(root), root)
    for name in PERMISSION_OUTPUTS:
        if name != "perm/opencode.json":
            assert rendered[name] == (EXPECTED / name).read_text(encoding="utf-8")


def test_rendering_is_deterministic(root: Path) -> None:
    register(root)
    assert render_all(root) == render_all(root)


def test_preserve_keeps_a_foreign_key_at_the_right_index(root: Path) -> None:
    register(root)
    out = root / "perm" / "opencode.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"foreign": {"one": {"type": "remote"}}}, indent=2) + "\n",
        encoding="utf-8",
    )
    rendered = _repo_relative(render_all(root), root)
    doc = json.loads(rendered["perm/opencode.json"])
    assert list(doc) == ["$schema", "model", "provider", "permission", "foreign"]
    assert doc["foreign"] == {"one": {"type": "remote"}}


def test_preserve_omits_the_key_when_the_output_does_not_exist(root: Path) -> None:
    register(root)
    (root / "perm" / "opencode.json").unlink(missing_ok=True)
    rendered = _repo_relative(render_all(root), root)
    doc = json.loads(rendered["perm/opencode.json"])
    assert "foreign" not in doc
    assert list(doc) == ["$schema", "model", "provider", "permission"]


def test_preserve_malformed_json_is_an_error(root: Path) -> None:
    """A corrupt output file must never be silently overwritten — see task 8 fix round 1."""
    register(root)
    out = root / "perm" / "opencode.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LoadoutError, match="invalid JSON"):
        render_all(root)


def test_preserve_non_object_json_is_an_error(root: Path) -> None:
    register(root)
    out = root / "perm" / "opencode.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("[]", encoding="utf-8")
    with pytest.raises(LoadoutError, match="must be a JSON object"):
        render_all(root)


def test_preserve_naming_a_generated_key_is_rejected(root: Path) -> None:
    register(root)
    text = (root / "loadout.toml").read_text(encoding="utf-8")
    text = text.replace('preserve = ["foreign"]', 'preserve = ["permission"]')
    (root / "loadout.toml").write_text(text, encoding="utf-8")
    with pytest.raises(LoadoutError, match="generated key"):
        render_all(root)


def _add_second_source(root: Path, tmp_path: Path, body: str) -> Path:
    second = tmp_path / "second"
    second.mkdir(parents=True, exist_ok=True)
    (second / "permissions.toml").write_text(body, encoding="utf-8")
    text = (root / "loadout.toml").read_text(encoding="utf-8")
    text = text.replace(
        '[[source]]\nname = "test"\npath = "."\n',
        f'[[source]]\nname = "test"\npath = "."\n\n[[source]]\nname = "second"\npath = "{second}"\n',
        1,
    )
    (root / "loadout.toml").write_text(text, encoding="utf-8")
    return second


def test_a_second_source_contributes_its_allow_entries(root: Path, tmp_path: Path) -> None:
    """Spec 1 §1 — a company or team repo is just another [[source]]."""
    register(root)
    _add_second_source(root, tmp_path, '[shell]\nallow = ["from-second"]\n')
    rendered = _repo_relative(render_all(root), root)
    assert "Bash(from-second:*)" in rendered["perm/claude.json"]


def test_a_later_source_deny_beats_an_earlier_source_allow(root: Path, tmp_path: Path) -> None:
    """Deny wins across sources, not just within one — ADR 0002."""
    register(root)
    _add_second_source(root, tmp_path, '[shell]\ndeny = ["alpha"]\n')
    doc = json.loads(_repo_relative(render_all(root), root)["perm/claude.json"])
    assert "Bash(alpha:*)" in doc["permissions"]["deny"]
    assert "Bash(alpha:*)" not in doc["permissions"]["allow"]


def test_source_order_sets_emission_order(root: Path, tmp_path: Path) -> None:
    """OpenCode and Pi are last-match-wins, so tier order is manifest order."""
    register(root)
    _add_second_source(root, tmp_path, '[shell]\nallow = ["from-second"]\n')
    doc = json.loads(_repo_relative(render_all(root), root)["perm/claude.json"])
    allow = doc["permissions"]["allow"]
    assert allow.index("Bash(alpha:*)") < allow.index("Bash(from-second:*)")


def test_unknown_renderer_name_is_an_error(root: Path) -> None:
    register(root)
    text = (root / "loadout.toml").read_text(encoding="utf-8")
    (root / "loadout.toml").write_text(
        text + '\n[permissions.bogus]\noutput = "bogus.json"\nrender = "nope"\n',
        encoding="utf-8",
    )
    with pytest.raises(LoadoutError, match="unknown renderer"):
        render_all(root)


def test_missing_base_file_is_an_error(root: Path) -> None:
    """`base` still names a path — permissions.opencode is the target that uses it."""
    register(root)
    (root / "bases" / "opencode.base.json").unlink()
    with pytest.raises(LoadoutError, match="base document not found"):
        render_all(root)


def test_base_permissions_key_must_be_an_object(root: Path) -> None:
    base_path = root / "bases" / "opencode.base.json"
    doc = json.loads(base_path.read_text(encoding="utf-8"))
    doc["permissions"] = "oops"
    base_path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(LoadoutError, match="permissions"):
        render_all(root)


def test_a_missing_settings_fragment_names_the_slice(root: Path) -> None:
    register(root)
    (root / "settings" / "claude.json").unlink()
    with pytest.raises(LoadoutError, match="settings not found in any source"):
        render_all(root)


def test_settings_composes_later_fragments_over_earlier_ones(root: Path) -> None:
    """The [claude] block names two fragments; the delta merges into a nested map
    rather than replacing it."""
    register(root)
    doc = json.loads(_repo_relative(render_all(root), root)["destinations/settings.json"])
    assert doc["afkTimeoutMs"] == "2147483647"
    assert doc["afterKey"] == {"nested": True, "addedByTheDelta": True}


def test_one_settings_fragment_matches_the_base_spelling_byte_for_byte(root: Path) -> None:
    """`settings = "claude"` and `base = "bases/claude.base.json"` are one input."""
    register(root)
    rendered = _repo_relative(render_all(root), root)
    text = (root / "loadout.toml").read_text(encoding="utf-8")
    (root / "loadout.toml").write_text(
        text.replace('settings = "claude"\n', 'base     = "bases/claude.base.json"\n', 1),
        encoding="utf-8",
    )
    assert (
        _repo_relative(render_all(root), root)["perm/claude.json"] == rendered["perm/claude.json"]
    )


def test_base_and_settings_together_is_an_error(root: Path) -> None:
    register(root)
    text = (root / "loadout.toml").read_text(encoding="utf-8")
    (root / "loadout.toml").write_text(
        text.replace(
            'settings = "claude"\n', 'settings = "claude"\nbase     = "bases/claude.base.json"\n', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(LoadoutError, match="two spellings of the same input"):
        render_all(root)
