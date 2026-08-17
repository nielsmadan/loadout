from __future__ import annotations

import json
from pathlib import Path

import pytest

from loadout.agents import SliceOutput
from loadout.emit import Copied, render_all, render_project
from loadout.errors import LoadoutError
from loadout.project import PROJECT_PRESET

EXPECTED = Path(__file__).parent / "fixtures" / "expected" / "project"

OUTPUTS = (
    ".claude/settings.json",
    ".codex/rules/permissions.rules",
    "opencode.json",
    ".pi/extensions/pi-permission-system/config.json",
    ".claude/mcp-permissions.json",
    "CLAUDE.md",
    "AGENTS.md",
)

# Every harness that has a skills slice gets its own directory, because
# `render_skill` varies its output by harness. Codex has none — verified negative,
# see reference/config.md.
SKILL_DIRS = {"claude": ".claude/skills", "opencode": ".opencode/skills", "pi": ".pi/skills"}
SKILL_FILES = ("from-template/SKILL.md", "probe/SKILL.md", "probe/reference.md")


def skill_outputs(*harnesses: str) -> set[str]:
    return {f"{SKILL_DIRS[h]}/{f}" for h in harnesses for f in SKILL_FILES}


def test_every_project_output_matches_the_expected_output(project: Path) -> None:
    """Everything rendered, not a named list of it.

    Iterating `OUTPUTS` was equivalent while it happened to be the whole tree, and
    stopped being when skills arrived — nine expected files were written and
    compared by nothing, and the feature commit stayed green because of it. A
    green result from a check that never ran is the shape `AGENTS.md` warns about,
    so the comparison now derives its own list from the render.
    """
    rendered = {str(p.relative_to(project)): c for p, c in render_project(project).items()}
    assert set(rendered) == set(OUTPUTS) | skill_outputs(*SKILL_DIRS)
    for name, content in sorted(rendered.items()):
        actual = (
            content.source.read_text(encoding="utf-8") if isinstance(content, Copied) else content
        )
        assert actual == (EXPECTED / name).read_text(encoding="utf-8"), name


def test_every_output_is_rendered(project: Path) -> None:
    rendered = {str(p.relative_to(project)) for p in render_project(project)}
    assert rendered == set(OUTPUTS) | skill_outputs(*SKILL_DIRS)


def test_only_enabled_harnesses_are_rendered(project: Path) -> None:
    config = project / "loadout" / "config.toml"
    config.write_text(
        config.read_text(encoding="utf-8").replace(
            '"claude", "codex", "opencode", "pi"', '"claude"'
        ),
        encoding="utf-8",
    )
    rendered = {str(p.relative_to(project)) for p in render_project(project)}
    assert rendered == {
        ".claude/settings.json",
        ".claude/mcp-permissions.json",
        "CLAUDE.md",
    } | skill_outputs("claude")


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
    into PROJECT_PRESET, so this patches the preset directly to exercise the path."""
    bogus = {"permissions": SliceOutput(renderer="nope", output="bogus.json")}
    monkeypatch.setitem(PROJECT_PRESET, "claude", bogus)
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


def _vendor(bare_project: Path, name: str, permissions: str) -> Path:
    tree = bare_project / "loadout" / "templates" / name
    tree.mkdir(parents=True)
    (tree / "permissions.toml").write_text(permissions, encoding="utf-8")
    return tree


def _declare(bare_project: Path, *names: str) -> None:
    config = bare_project / "loadout" / "config.toml"
    quoted = ", ".join(f'"{name}"' for name in names)
    config.write_text(
        config.read_text(encoding="utf-8") + f"templates = [{quoted}]\n", encoding="utf-8"
    )


def test_a_template_rule_reaches_the_output(bare_project: Path) -> None:
    _vendor(bare_project, "web", '[shell]\nallow = ["vite build"]\n')
    _declare(bare_project, "web")
    doc = json.loads(render_project(bare_project)[bare_project / ".claude/settings.json"])
    assert "Bash(vite build:*)" in doc["permissions"]["allow"]


def test_a_project_deny_beats_a_template_allow(bare_project: Path) -> None:
    """The template is the lowest tier, so deny-wins resolves against it.

    The second rule is what makes this a real test: without it, the assertions
    pass whether or not the template merged at all, because an unmerged allow is
    also an absent allow.
    """
    _vendor(bare_project, "web", '[shell]\nallow = ["vite build", "vite preview"]\n')
    _declare(bare_project, "web")
    (bare_project / "loadout" / "permissions.local.toml").write_text(
        '[shell]\ndeny = ["vite build"]\n', encoding="utf-8"
    )
    doc = json.loads(render_project(bare_project)[bare_project / ".claude/settings.json"])
    assert "Bash(vite preview:*)" in doc["permissions"]["allow"]
    assert "Bash(vite build:*)" in doc["permissions"]["deny"]
    assert "Bash(vite build:*)" not in doc["permissions"]["allow"]


def test_templates_merge_in_declared_order(bare_project: Path) -> None:
    """Emission order decides which rule applies on OpenCode and Pi, so the
    declared order has to survive into the output."""
    _vendor(bare_project, "one", '[shell]\nallow = ["one-tool"]\n')
    _vendor(bare_project, "two", '[shell]\nallow = ["two-tool"]\n')
    _declare(bare_project, "one", "two")
    doc = json.loads(render_project(bare_project)[bare_project / ".claude/settings.json"])
    allow = doc["permissions"]["allow"]
    assert allow.index("Bash(one-tool:*)") < allow.index("Bash(two-tool:*)")


def test_a_template_rule_is_emitted_before_the_projects_own(bare_project: Path) -> None:
    """Lowest tier first, because OpenCode and Pi are last-match-wins."""
    _vendor(bare_project, "web", '[shell]\nallow = ["vite build"]\n')
    _declare(bare_project, "web")
    doc = json.loads(render_project(bare_project)[bare_project / "opencode.json"])
    keys = list(doc["permission"]["bash"])
    assert keys.index("vite build") < keys.index("alpha")


def test_a_template_carrying_no_permissions_is_not_an_error(bare_project: Path) -> None:
    """`railway` in the live source offers skills only; a source's `use` already
    covers that shape, so a template offering one slice needs no special case."""
    (bare_project / "loadout" / "templates" / "railway" / "skills").mkdir(parents=True)
    _declare(bare_project, "railway")
    assert render_project(bare_project)


def test_an_unresolvable_template_fails_the_render(bare_project: Path) -> None:
    _declare(bare_project, "missing")
    with pytest.raises(LoadoutError, match="no machine config"):
        render_project(bare_project)


def _instruct(bare_project: Path, *names: str) -> None:
    config = bare_project / "loadout" / "config.toml"
    quoted = ", ".join(f'"{name}"' for name in names)
    config.write_text(
        config.read_text(encoding="utf-8") + f"instructions = [{quoted}]\n", encoding="utf-8"
    )


def _fragment(bare_project: Path, name: str, body: str) -> None:
    directory = bare_project / "loadout" / "instructions"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{name}.md").write_text(body, encoding="utf-8")


def test_the_two_instruction_documents_are_byte_identical(project: Path) -> None:
    """Codex, OpenCode and Pi share one repo-root AGENTS.md, so a per-agent order
    could not be honoured. One order is what makes the two documents equal by
    construction rather than by luck."""
    rendered = render_project(project)
    assert rendered[project / "CLAUDE.md"] == rendered[project / "AGENTS.md"]


def test_instruction_blocks_appear_in_declared_order_below_the_template(project: Path) -> None:
    """All three positions are asserted, not just the winner: an assertion that
    only pins the first block passes against a render that dropped the rest, and
    `testing` before `conventions` is the reverse of the sorted order, so a
    directory listing cannot produce this."""
    document = render_project(project)[project / "AGENTS.md"]
    template = document.index("Contributed by the `web` template")
    testing = document.index("Declared first while sorting second")
    conventions = document.index("Declared second while sorting first")
    assert template < testing < conventions


def test_a_project_declaring_no_instructions_generates_neither_document(
    bare_project: Path,
) -> None:
    rendered = {str(p.relative_to(bare_project)) for p in render_project(bare_project)}
    assert rendered == {
        ".claude/settings.json",
        ".claude/mcp-permissions.json",
        ".codex/rules/permissions.rules",
        "opencode.json",
        ".pi/extensions/pi-permission-system/config.json",
    } | {
        # The template is gone with `bare_project`, so only the project's own skill
        # survives — which is also what proves the template supplied the other one.
        f"{SKILL_DIRS[h]}/{f}"
        for h in SKILL_DIRS
        for f in ("probe/SKILL.md", "probe/reference.md")
    }


def test_a_template_contributes_instructions_without_being_named(bare_project: Path) -> None:
    """A template is a tier, not a fragment: adopting `web` brings its prose with
    no entry in the project's order. The project fragment is declared too, and
    still present, so this cannot pass against a render that emitted only the
    template."""
    tree = _vendor(bare_project, "web", "[shell]\nallow = []\n")
    (tree / "instructions.md").write_text("From the template.", encoding="utf-8")
    _declare(bare_project, "web")
    _fragment(bare_project, "own", "From the project.")
    _instruct(bare_project, "own")

    document = render_project(bare_project)[bare_project / "AGENTS.md"]
    assert document.index("From the template.") < document.index("From the project.")


def test_an_unknown_instruction_fragment_fails_the_render(bare_project: Path) -> None:
    _instruct(bare_project, "nope")
    with pytest.raises(LoadoutError, match="nope"):
        render_project(bare_project)


def test_codex_gets_no_project_skills(project: Path) -> None:
    """A verified negative, not an omission: the 0.147.0 binary has no
    project-relative skills path, and its extra-roots mechanism is a setting in
    `.codex/config.toml`, which loadout does not own."""
    rendered = {str(p.relative_to(project)) for p in render_project(project)}
    assert not [p for p in rendered if p.startswith(".codex/") and "skills" in p]
    assert ".codex/rules/permissions.rules" in rendered


def test_each_harness_gets_its_own_flavour_of_a_skill(project: Path) -> None:
    """The reason skills cannot share a directory the way instructions do:
    `render_skill` takes a harness and varies its output by it. Asserting all
    three differ from each other — and that each carries its own marker — is what
    a pairwise-inequality check alone would not give."""
    rendered = render_project(project)
    bodies = {
        harness: rendered[project / directory / "probe" / "SKILL.md"]
        for harness, directory in SKILL_DIRS.items()
    }
    assert "Claude-only" in bodies["claude"]
    assert "OpenCode-only" in bodies["opencode"]
    assert "Pi-only" in bodies["pi"]
    assert len({*bodies.values()}) == 3


def test_a_project_skill_beats_a_template_skill_of_the_same_name(project: Path) -> None:
    """Templates are the lowest tier here as they are for permissions. The second
    assertion is what makes this a real test: without it, `TEMPLATE_LOSER` being
    absent passes whether or not the template's skills were ever scanned."""
    rendered = render_project(project)
    probe = rendered[project / ".claude/skills/probe/SKILL.md"]
    assert "TEMPLATE_LOSER" not in probe
    assert "Declared by the project" in probe
    assert project / ".claude/skills/from-template/SKILL.md" in rendered


def test_a_supporting_file_is_copied_rather_than_rendered(project: Path) -> None:
    """Naming the source rather than its decoded text is what preserves a mode —
    a skill's `scripts/` are executable and that does not survive a `str`."""
    rendered = render_project(project)
    carried = rendered[project / ".claude/skills/probe/reference.md"]
    assert isinstance(carried, Copied)
    assert carried.source.read_text(encoding="utf-8").startswith("Supporting file")
