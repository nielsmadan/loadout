from __future__ import annotations

import re
from pathlib import Path

from loadout.commands import DRIFT_ADVICE
from loadout.emit import render_global
from loadout.harnesses import GLOBAL_PRESET
from loadout.migration_models import Inventory, MigrationPlan
from loadout.output import Merged
from loadout.project import PROJECT_PRESET
from loadout.skills import discover_skills, render_skill, split_frontmatter
from registration import register

ROOT = Path(__file__).parents[1]
SKILL = ROOT / "src" / "loadout" / "_skills" / "loadout"
PORTING = "Porting a change made outside loadout"
RESOLVING = "Resolving conflicting copies at init"


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


def _settings_reaches_output(root: Path, agent: str, extra: str = "") -> bool:
    root.mkdir()
    (root / "loadout.toml").write_text(
        f'[[source]]\nname = "test"\npath = "."\n\n[{agent}]\nsettings = "model"\n{extra}',
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
        for output in render_global(register(root)).values()
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
            "`loadout/skills/<name>.local/SKILL.md`, replacing the shared skill of that name",
            "supported for `claude`, `codex`, `droid`, `opencode`, `pi` via `loadout/skills/<name>/SKILL.md`",
            "`skills/<name>/SKILL.md` from selected sources",
        ),
        "agents": (
            "unsupported",
            "supported for `claude`, `codex`, `droid`, `opencode`, `pi` via `loadout/agents/<name>.md`",
            "`agents/<name>.md` from selected sources",
        ),
        "settings": (
            "unsupported",
            "supported for `claude`, `droid`, `opencode` via `loadout/settings/<harness>.json`",
            (
                "supported for `claude`, `droid`, `opencode` via `settings/<name>.json` "
                "fragments, and for Pi only when `[pi]` also declares `plugins`; Codex uses "
                "`defaults`"
            ),
        ),
        "defaults": (
            "unsupported",
            "unsupported",
            "Codex top-level and nested settings via `defaults/<name>.json` fragments",
        ),
        "hooks": (
            "unsupported",
            "Droid via `loadout/hooks.json`",
            "`hooks/<name>.json` fragments selected by agents offering hooks",
        ),
        "plugins": (
            "unsupported",
            "Droid via `loadout/plugins.json`",
            "`plugins/<name>.json` fragments selected by agents offering plugins",
        ),
        "module-config": (
            "unsupported",
            "unsupported",
            (
                "supported for `claude`, `droid`, `opencode`, `pi` via "
                "`module-config/<agent>/<relative path>`; Codex unsupported"
            ),
        ),
        "templates": (
            "unsupported",
            "declarations and vendored copies under `loadout/templates/`",
            "definitions under `templates/<name>/` in declared sources",
        ),
        "harnesses": (
            "unsupported",
            "`harnesses` in `loadout/config.toml`",
            "declared agent blocks",
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
    assert actual == {"claude", "droid", "opencode"}
    assert documented == actual


def test_pi_settings_reach_output_beside_a_plugins_declaration(tmp_path: Path) -> None:
    assert _settings_reaches_output(tmp_path / "pi", "pi", "plugins = []\n")
    assert "Pi only when `[pi]` also declares `plugins`" in _global_capability("settings")


def test_project_settings_capability_names_only_agents_whose_file_keeps_other_keys() -> None:
    actual = {
        agent for agent, slices in PROJECT_PRESET.items() if slices["permissions"].preserve_foreign
    }
    documented = {
        agent for agent in PROJECT_PRESET if f"`{agent}`" in _capability_matrix()["settings"][1]
    }
    assert actual == {"claude", "droid", "opencode"}
    assert documented == actual


def test_project_skill_capability_names_only_agents_with_a_destination() -> None:
    actual = {agent for agent, slices in PROJECT_PRESET.items() if "skills" in slices}
    documented = {
        agent for agent in PROJECT_PRESET if f"`{agent}`" in _capability_matrix()["skills"][1]
    }
    assert actual == {"claude", "codex", "droid", "opencode", "pi"}
    assert documented == actual


def _section(path: Path, heading: str) -> str:
    return path.read_text(encoding="utf-8").split(f"## {heading}\n", 1)[1].split("\n## ", 1)[0]


def test_porting_covers_every_kind_the_cli_sends_to_the_skill() -> None:
    porting = _section(SKILL / "references" / "configuration.md", PORTING)
    sent = {kind for kind, advice in DRIFT_ADVICE.items() if "/loadout" in advice}

    assert sent == {"hand-edited", "owned-key", "owned-key-removed", "unknown", "unmanaged"}
    assert {kind for kind in sent if f"`{kind}`" in porting} == sent


def test_conflict_resolution_reads_the_key_init_reports() -> None:
    resolving = _section(SKILL / "references" / "onboarding.md", RESOLVING)
    inventory = Inventory(Path("/p"), Path("/p/loadout"), "project", (), (), (), ())

    assert "unadopted" in MigrationPlan(inventory).preview()
    assert "`unadopted`" in resolving


def test_troubleshooting_sends_both_cases_to_their_procedures() -> None:
    troubleshooting = _section(SKILL / "SKILL.md", "Troubleshooting")

    assert f"“{PORTING}”" in troubleshooting
    assert f"“{RESOLVING}”" in troubleshooting
