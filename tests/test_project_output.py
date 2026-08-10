from __future__ import annotations

import json
from pathlib import Path, PurePosixPath

import pytest

from loadout.emit import render_all, render_project
from loadout.errors import LoadoutError
from loadout.project import PRESET, ProjectTarget

EXPECTED = Path(__file__).parent / "fixtures" / "expected" / "project"

OUTPUTS = (
    ".claude/settings.json",
    ".codex/rules/aiconf.rules",
    "opencode.json",
    ".pi/extensions/pi-permission-system/config.json",
    ".aiconf/mcp-permissions.json",
)


def test_every_project_output_matches_the_expected_output(project: Path) -> None:
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
    with_tier = json.loads(render_project(project)[project / ".claude/settings.json"])
    (project / "loadout" / "permissions.local.toml").unlink()
    without_tier = json.loads(render_project(project)[project / ".claude/settings.json"])

    assert "Bash(kappa only-here:*)" in with_tier["permissions"]["allow"]
    assert "Bash(kappa only-here:*)" not in without_tier["permissions"]["allow"]
    # The committed tier's allow is only overridden while the personal deny is present.
    assert "Bash(zeta:*)" in without_tier["permissions"]["allow"]
    assert "Bash(zeta:*)" in with_tier["permissions"]["deny"]


def test_claude_runtime_settings_file_is_not_a_loadout_output(project: Path) -> None:
    """Claude Code writes .claude/settings.local.json itself; loadout must not own it."""
    rendered = {str(p.relative_to(project)) for p in render_project(project)}
    assert ".claude/settings.local.json" not in rendered


def test_a_dropped_harness_name_is_rejected(project: Path) -> None:
    """antigravity was removed as a target — see docs/decisions/0012. The name must
    now fail loudly rather than silently generating nothing."""
    (project / "loadout" / "config.toml").write_text(
        'harnesses = ["antigravity"]\n', encoding="utf-8"
    )
    with pytest.raises(LoadoutError, match="antigravity"):
        render_project(project)


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


def test_malformed_existing_output_raises_and_tells_the_user_the_way_out(project: Path) -> None:
    """A truncated write or bad hand-edit to a preserve_foreign target must not wedge
    sync — the error must point at `loadout sync` as the remedy, since deleting the
    file and re-running it is the only way out of the read-your-own-output loop."""
    out = project / "opencode.json"
    out.write_text("not json", encoding="utf-8")
    with pytest.raises(LoadoutError, match="loadout sync"):
        render_project(project)


def test_non_dict_existing_output_raises_and_tells_the_user_the_way_out(project: Path) -> None:
    out = project / "opencode.json"
    out.write_text("[1, 2, 3]", encoding="utf-8")
    with pytest.raises(LoadoutError, match="loadout sync"):
        render_project(project)


def test_project_unknown_renderer_raises_loadout_error_not_keyerror(
    project: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The project render path used to index RENDERERS directly, so a bad renderer
    name raised a bare KeyError — exit 4, not the LoadoutError (exit 3) every other
    failure raises. There is no manifest-driven way to inject a bad renderer name
    into PRESET, so this patches the preset directly to exercise the path."""
    bogus = (ProjectTarget(PurePosixPath("bogus.json"), "nope"),)
    monkeypatch.setitem(PRESET, "claude", bogus)
    (project / "loadout" / "config.toml").write_text('harnesses = ["claude"]\n', encoding="utf-8")
    with pytest.raises(LoadoutError, match="unknown renderer"):
        render_project(project)


def test_absent_output_still_renders(project: Path) -> None:
    (project / "opencode.json").unlink(missing_ok=True)
    rendered = {str(p.relative_to(project)): c for p, c in render_project(project).items()}
    assert json.loads(rendered["opencode.json"])["permission"]["bash"]


def test_render_all_includes_project_outputs(project: Path) -> None:
    """The seam: sync and check go through render_all, which must see project targets."""
    rendered = {str(p.relative_to(project)) for p in render_all(project)}
    assert ".claude/settings.json" in rendered
    assert "opencode.json" in rendered


def test_render_all_works_with_no_root_manifest(project: Path) -> None:
    assert not (project / "loadout.toml").exists()
    assert render_all(project)


def test_render_all_errors_when_neither_manifest_exists(tmp_path: Path) -> None:
    with pytest.raises(LoadoutError, match="no manifest"):
        render_all(tmp_path)


def test_render_all_unions_both_scopes_when_both_manifests_exist(root: Path, project: Path) -> None:
    """root and project both scaffold onto the same tmp_path; render_all must return
    outputs from both, not just one — the union claim the task exists to prove."""
    assert root == project
    # render_all also returns destination paths under ~; this test only cares
    # about in-repo outputs, so drop anything not rooted under root.
    rendered = {str(p.relative_to(root)) for p in render_all(root) if root in p.parents}
    assert "out/shared.md" in rendered  # global-only output
    assert ".claude/settings.json" in rendered  # project-only output
    assert "opencode.json" in rendered  # project-only output


def test_render_all_rejects_a_path_collision_between_scopes(root: Path, project: Path) -> None:
    """A global permissions target pointed at the same path a project preset also
    generates must raise, not silently overwrite — construct the collision explicitly
    since the two scopes' natural presets never overlap on their own."""
    manifest = root / "loadout.toml"
    manifest.write_text(
        manifest.read_text(encoding="utf-8").replace(
            'output   = "perm/opencode.json"', 'output   = "opencode.json"'
        ),
        encoding="utf-8",
    )
    with pytest.raises(LoadoutError, match=r"opencode\.json"):
        render_all(root)
