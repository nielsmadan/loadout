from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.emit import render_all
from loadout.errors import LoadoutError

GOLDEN = Path(__file__).parent / "golden"
EXPECTED = GOLDEN / "expected"

PERMISSION_OUTPUTS = (
    "antigravity/settings.json",
    "claude/settings.json",
    "claude/settings.autonomous.json",
    "claude/mcp-permissions.json",
    "codex/rules/permissions.rules",
    "codex/mcp-permissions.toml",
    "opencode/opencode.json",
    "pi/permissions.json",
)


def _repo_relative(rendered: dict[Path, str], root: Path) -> dict[str, str]:
    """render_all also returns destination paths under ~; these tests only care
    about the in-repo outputs, so drop anything not rooted under root."""
    return {str(p.relative_to(root)): c for p, c in rendered.items() if root in p.parents}


def test_every_permission_output_matches_its_frozen_golden(root: Path) -> None:
    rendered = _repo_relative(render_all(root), root)
    for name in PERMISSION_OUTPUTS:
        expected = (EXPECTED / name).read_text(encoding="utf-8")
        assert rendered[name] == expected, f"{name} differs from its golden"


def test_all_eight_permission_targets_are_rendered(root: Path) -> None:
    rendered = set(_repo_relative(render_all(root), root))
    for name in PERMISSION_OUTPUTS:
        assert name in rendered


def test_rendering_does_not_require_the_output_to_exist(root: Path) -> None:
    """Acceptance criterion 2 — a clean checkout must render."""
    for name in PERMISSION_OUTPUTS:
        (root / name).unlink(missing_ok=True)
    rendered = _repo_relative(render_all(root), root)
    for name in PERMISSION_OUTPUTS:
        if name != "opencode/opencode.json":
            assert rendered[name] == (EXPECTED / name).read_text(encoding="utf-8")


def test_rendering_is_deterministic(root: Path) -> None:
    assert render_all(root) == render_all(root)


def test_preserve_keeps_a_foreign_key_at_the_right_index(root: Path) -> None:
    out = root / "opencode" / "opencode.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"mcp": {"jina": {"type": "remote"}}}, indent=2) + "\n",
        encoding="utf-8",
    )
    rendered = _repo_relative(render_all(root), root)
    doc = json.loads(rendered["opencode/opencode.json"])
    assert list(doc) == ["$schema", "model", "provider", "permission", "mcp"]
    assert doc["mcp"] == {"jina": {"type": "remote"}}


def test_preserve_omits_the_key_when_the_output_does_not_exist(root: Path) -> None:
    (root / "opencode" / "opencode.json").unlink(missing_ok=True)
    rendered = _repo_relative(render_all(root), root)
    doc = json.loads(rendered["opencode/opencode.json"])
    assert "mcp" not in doc
    assert list(doc) == ["$schema", "model", "provider", "permission"]


def test_preserve_malformed_json_is_an_error(root: Path) -> None:
    """A corrupt output file must never be silently overwritten — see task 8 fix round 1."""
    out = root / "opencode" / "opencode.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("{not valid json", encoding="utf-8")
    with pytest.raises(LoadoutError, match="invalid JSON"):
        render_all(root)


def test_preserve_non_object_json_is_an_error(root: Path) -> None:
    out = root / "opencode" / "opencode.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text("[]", encoding="utf-8")
    with pytest.raises(LoadoutError, match="must be a JSON object"):
        render_all(root)


def test_preserve_naming_a_generated_key_is_rejected(root: Path) -> None:
    text = (root / "loadout.toml").read_text(encoding="utf-8")
    text = text.replace('preserve = ["mcp"]', 'preserve = ["permission"]')
    (root / "loadout.toml").write_text(text, encoding="utf-8")
    with pytest.raises(LoadoutError, match="generated key"):
        render_all(root)


def test_two_sources_offering_permissions_is_an_error(root: Path, tmp_path: Path) -> None:
    """Acceptance criterion 5 — merging is milestone 4."""
    second = tmp_path / "second"
    second.mkdir(parents=True)
    (second / "permissions.toml").write_text("[shell]\nallow = []\n", encoding="utf-8")
    text = (root / "loadout.toml").read_text(encoding="utf-8")
    text = text.replace(
        '[[source]]\nname = "ac"\npath = "."\n',
        f'[[source]]\nname = "ac"\npath = "."\n\n[[source]]\nname = "second"\npath = "{second}"\n',
        1,
    )
    (root / "loadout.toml").write_text(text, encoding="utf-8")
    with pytest.raises(LoadoutError, match="more than one source"):
        render_all(root)


def test_unknown_renderer_name_is_an_error(root: Path) -> None:
    text = (root / "loadout.toml").read_text(encoding="utf-8")
    (root / "loadout.toml").write_text(
        text + '\n[permissions.bogus]\noutput = "bogus.json"\nrender = "nope"\n',
        encoding="utf-8",
    )
    with pytest.raises(LoadoutError, match="unknown renderer"):
        render_all(root)


def test_missing_base_file_is_an_error(root: Path) -> None:
    (root / "claude" / "settings.base.json").unlink()
    with pytest.raises(LoadoutError, match="base document not found"):
        render_all(root)


def test_base_permissions_key_must_be_an_object(root: Path) -> None:
    base_path = root / "claude" / "settings.base.json"
    doc = json.loads(base_path.read_text(encoding="utf-8"))
    doc["permissions"] = "oops"
    base_path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(LoadoutError, match="permissions"):
        render_all(root)


def test_base_drift_guard() -> None:
    """Acceptance criterion 3 — the two Claude bases differ only by the AFK key."""
    interactive = json.loads((GOLDEN / "claude" / "settings.base.json").read_text(encoding="utf-8"))
    autonomous = json.loads(
        (GOLDEN / "claude" / "settings.autonomous.base.json").read_text(encoding="utf-8")
    )
    stripped = json.loads(json.dumps(interactive))
    stripped["env"].pop("CLAUDE_AFK_TIMEOUT_MS", None)
    assert autonomous == stripped, (
        "the two Claude bases have drifted apart in more than the AFK timeout key"
    )
