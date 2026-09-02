from __future__ import annotations

import re
from pathlib import Path

from loadout.agents import GLOBAL_PRESET
from loadout.emit import Merged, render_global
from loadout.project import PROJECT_PRESET
from loadout.skills import discover_skills, render_skill, split_frontmatter

ROOT = Path(__file__).parents[1]
SKILL = ROOT / "src" / "loadout" / "_skills" / "loadout"


def _frontmatter() -> dict[str, str]:
    lines, _ = split_frontmatter((SKILL / "SKILL.md").read_text(encoding="utf-8"))
    assert lines is not None
    return dict(line.split(":", 1) for line in lines)


def _capability_matrix() -> dict[str, tuple[str, str, str]]:
    text = (SKILL / "references" / "configuration.md").read_text(encoding="utf-8")
    matrix = text.split("## Capability matrix\n", 1)[1].split("\n## ", 1)[0]
    rows: dict[str, tuple[str, str, str]] = {}
    for line in matrix.splitlines():
        if not line.startswith("| `"):
            continue
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        rows[cells[0].strip("`")] = (cells[1], cells[2], cells[3])
    return rows


def _global_capability(artifact: str) -> str:
    text = (SKILL / "references" / "configuration.md").read_text(encoding="utf-8")
    matrix = text.split("## Capability matrix\n", 1)[1].split("\n## ", 1)[0]
    row = next(line for line in matrix.splitlines() if line.startswith(f"| `{artifact}` |"))
    return row.split("|")[4].strip()


def _settings_reaches_output(root: Path, agent: str) -> bool:
    root.mkdir()
    (root / "loadout.toml").write_text(
        f'[[source]]\nname = "test"\npath = "."\n\n[{agent}]\nsettings = "model"\n',
        encoding="utf-8",
    )
    (root / "permissions.toml").write_text("[shell]\n", encoding="utf-8")
    settings = root / "settings"
    settings.mkdir()
    settings.joinpath("model.json").write_text(
        '{"loadout-settings-probe": "survived"}\n', encoding="utf-8"
    )
    documents = (
        output.document if isinstance(output, Merged) else output
        for output in render_global(root).values()
    )
    return any(
        "loadout-settings-probe" in document for document in documents if isinstance(document, str)
    )


def test_loadout_skill_has_portable_frontmatter() -> None:
    frontmatter = _frontmatter()
    assert frontmatter["name"].strip() == "loadout"
    assert frontmatter["description"].strip().startswith("Use when ")
    assert len("\n".join(f"{key}:{value}" for key, value in frontmatter.items())) <= 1024


def test_every_relative_skill_link_resolves() -> None:
    document = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    targets = re.findall(r"\[[^]]+\]\(([^)]+)\)", document)
    assert targets
    assert all((SKILL / target).is_file() for target in targets)


def test_every_harness_receives_the_same_skill_body() -> None:
    (skill,) = discover_skills(SKILL.parent)
    rendered = {render_skill(skill, harness) for harness in sorted(GLOBAL_PRESET)}
    assert len(rendered) == 1


def test_named_global_profile_syncs_that_profile() -> None:
    skill = (SKILL / "SKILL.md").read_text(encoding="utf-8")
    reference = (SKILL / "references" / "configuration.md").read_text(encoding="utf-8")
    command = "loadout sync --global --profile <name>"
    assert command in skill
    assert command in reference


def test_capability_matrix_pins_every_scope_mapping() -> None:
    expected = {
        "permissions": (
            "`loadout/permissions.local.toml`",
            "`loadout/permissions.toml`",
            "`permissions.toml` from selected sources",
        ),
        "mcp-permissions": (
            "`loadout/permissions.local.toml`",
            "`loadout/permissions.toml`",
            "MCP policy from selected `permissions.toml` sources",
        ),
        "mcp": (
            "unsupported",
            "`loadout/mcp.toml`",
            "`mcp.toml` from selected sources",
        ),
        "instructions": (
            "unsupported",
            "`loadout/config.toml` and `loadout/instructions/*.md`",
            "manifest selection and `instructions/*.md` fragments",
        ),
        "skills": (
            "unsupported",
            "supported for `claude`, `opencode`, `pi` via `loadout/skills/<name>/`; Codex unsupported",
            "`skills/<name>/` trees from selected sources",
        ),
        "settings": (
            "unsupported",
            "unsupported",
            "supported for `claude`, `opencode` via `settings/<name>.json` fragments; Codex uses `defaults`; Pi unsupported",
        ),
        "defaults": (
            "unsupported",
            "unsupported",
            "Codex top-level settings via `defaults/<name>.json` fragments",
        ),
        "hooks": (
            "unsupported",
            "unsupported",
            "`hooks/<name>.json` fragments selected by agents offering hooks",
        ),
        "plugins": (
            "unsupported",
            "unsupported",
            "`plugins/<name>.json` fragments selected by agents offering plugins",
        ),
        "module-config": (
            "unsupported",
            "unsupported",
            "Pi module files under `module-config/pi/<relative path>`",
        ),
        "templates": (
            "unsupported",
            "declarations and vendored copies under `loadout/templates/`",
            "definitions under `templates/<name>/` in declared sources",
        ),
        "harnesses": (
            "unsupported",
            "`harnesses` in `loadout/config.toml`",
            "declared agent blocks or legacy targets",
        ),
        "profiles": (
            "unsupported",
            "unsupported",
            "`loadout.toml` plus `<profile>.toml` files",
        ),
    }
    preset_slices = {
        name
        for preset in (GLOBAL_PRESET, PROJECT_PRESET)
        for slices in preset.values()
        for name in slices
    }
    assert set(expected) == preset_slices | {"settings", "templates", "harnesses", "profiles"}
    assert _capability_matrix() == expected


def test_settings_capability_names_only_agents_whose_render_preserves_settings(
    tmp_path: Path,
) -> None:
    actual = {
        agent
        for agent in sorted(GLOBAL_PRESET)
        if _settings_reaches_output(tmp_path / agent, agent)
    }
    documented = {
        agent for agent in GLOBAL_PRESET if f"`{agent}`" in _global_capability("settings")
    }
    assert actual == {"claude", "opencode"}
    assert documented == actual


def test_project_skill_capability_names_only_agents_with_a_destination() -> None:
    actual = {agent for agent, slices in PROJECT_PRESET.items() if "skills" in slices}
    documented = {
        agent for agent in PROJECT_PRESET if f"`{agent}`" in _capability_matrix()["skills"][1]
    }
    assert actual == {"claude", "opencode", "pi"}
    assert documented == actual
