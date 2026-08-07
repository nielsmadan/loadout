from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.emit import render_project
from loadout.errors import LoadoutError

GOLDEN = Path(__file__).parent / "golden" / "project"
EXPECTED = GOLDEN / "expected"

OUTPUTS = (
    ".claude/settings.json",
    ".codex/rules/aiconf.rules",
    "opencode.json",
    ".pi/extensions/pi-permission-system/config.json",
    ".aiconf/mcp-permissions.json",
)


@pytest.fixture
def project(tmp_path: Path) -> Path:
    d = tmp_path / "loadout"
    d.mkdir(parents=True)
    (d / "config.toml").write_text(
        'harnesses = ["claude", "codex", "opencode", "pi"]\n', encoding="utf-8"
    )
    (d / "permissions.toml").write_text(
        (GOLDEN / "source" / "permissions.toml").read_text(encoding="utf-8"),
        encoding="utf-8",
    )
    (d / "permissions.local.toml").write_text("", encoding="utf-8")
    return tmp_path


def test_every_project_output_matches_its_frozen_golden(project: Path) -> None:
    rendered = {str(p.relative_to(project)): c for p, c in render_project(project).items()}
    for name in OUTPUTS:
        assert rendered[name] == (EXPECTED / name).read_text(encoding="utf-8"), name


def test_all_five_outputs_are_rendered(project: Path) -> None:
    rendered = {str(p.relative_to(project)) for p in render_project(project)}
    assert rendered == set(OUTPUTS)


def test_only_enabled_harnesses_are_rendered(project: Path) -> None:
    (project / "loadout" / "config.toml").write_text('harnesses = ["claude"]\n', encoding="utf-8")
    rendered = {str(p.relative_to(project)) for p in render_project(project)}
    assert rendered == {".claude/settings.json", ".aiconf/mcp-permissions.json"}


def test_personal_tier_merges_into_the_output(project: Path) -> None:
    (project / "loadout" / "permissions.local.toml").write_text(
        '[shell]\nallow = ["my-local-tool"]\n', encoding="utf-8"
    )
    rendered = {str(p.relative_to(project)): c for p, c in render_project(project).items()}
    doc = json.loads(rendered[".claude/settings.json"])
    assert "Bash(my-local-tool:*)" in doc["permissions"]["allow"]


def test_personal_deny_beats_committed_allow(project: Path) -> None:
    (project / "loadout" / "permissions.local.toml").write_text(
        '[shell]\ndeny = ["just build"]\n', encoding="utf-8"
    )
    rendered = {str(p.relative_to(project)): c for p, c in render_project(project).items()}
    doc = json.loads(rendered[".claude/settings.json"])
    assert "Bash(just build:*)" in doc["permissions"]["deny"]
    assert "Bash(just build:*)" not in doc["permissions"]["allow"]


def test_missing_personal_tier_is_not_an_error(project: Path) -> None:
    (project / "loadout" / "permissions.local.toml").unlink()
    rendered = {str(p.relative_to(project)): c for p, c in render_project(project).items()}
    assert rendered[".claude/settings.json"] == (EXPECTED / ".claude/settings.json").read_text(
        encoding="utf-8"
    )


def test_claude_runtime_settings_file_is_not_a_loadout_output(project: Path) -> None:
    """Claude Code writes .claude/settings.local.json itself; loadout must not own it."""
    rendered = {str(p.relative_to(project)) for p in render_project(project)}
    assert ".claude/settings.local.json" not in rendered


def test_antigravity_alone_renders_no_outputs(project: Path) -> None:
    """antigravity maps to an empty tuple in PRESET — accepting the name without
    generating anything is deliberate, not an oversight (see project.py PRESET)."""
    (project / "loadout" / "config.toml").write_text(
        'harnesses = ["antigravity"]\n', encoding="utf-8"
    )
    assert render_project(project) == {}


def test_missing_committed_source_is_an_error(project: Path) -> None:
    (project / "loadout" / "permissions.toml").unlink()
    with pytest.raises(LoadoutError, match="not found"):
        render_project(project)


def test_foreign_top_level_keys_survive_a_render(project: Path) -> None:
    out = project / "opencode.json"
    out.write_text(
        json.dumps(
            {"$schema": "https://opencode.ai/config.json", "permission": {"bash": {}}}, indent=2
        )
        + "\n",
        encoding="utf-8",
    )
    doc = json.loads(
        {str(p.relative_to(project)): c for p, c in render_project(project).items()}[
            "opencode.json"
        ]
    )
    assert doc["$schema"] == "https://opencode.ai/config.json"


def test_a_foreign_key_keeps_its_position_ahead_of_the_owned_one(project: Path) -> None:
    out = project / "opencode.json"
    out.write_text(
        json.dumps({"$schema": "x", "permission": {"bash": {}}}, indent=2) + "\n", encoding="utf-8"
    )
    doc = json.loads(
        {str(p.relative_to(project)): c for p, c in render_project(project).items()}[
            "opencode.json"
        ]
    )
    assert list(doc) == ["$schema", "permission"]


def test_the_owned_key_is_regenerated_not_carried_forward(project: Path) -> None:
    """The owned subtree must never feed back — ADR 0001."""
    out = project / "opencode.json"
    out.write_text(
        json.dumps({"permission": {"bash": {"STALE": "allow"}}}, indent=2) + "\n", encoding="utf-8"
    )
    doc = json.loads(
        {str(p.relative_to(project)): c for p, c in render_project(project).items()}[
            "opencode.json"
        ]
    )
    assert "STALE" not in doc["permission"]["bash"]


def test_loadout_only_outputs_do_not_preserve_foreign_keys(project: Path) -> None:
    out = project / ".pi" / "extensions" / "pi-permission-system" / "config.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"INJECTED": 1, "permission": {}}, indent=2) + "\n", encoding="utf-8")
    doc = json.loads(
        {str(p.relative_to(project)): c for p, c in render_project(project).items()}[
            ".pi/extensions/pi-permission-system/config.json"
        ]
    )
    assert "INJECTED" not in doc


def test_absent_output_still_renders(project: Path) -> None:
    (project / "opencode.json").unlink(missing_ok=True)
    rendered = {str(p.relative_to(project)): c for p, c in render_project(project).items()}
    assert json.loads(rendered["opencode.json"])["permission"]["bash"]
