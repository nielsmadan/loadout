from __future__ import annotations

from pathlib import PurePosixPath

import pytest

from loadout.agents import GLOBAL_PRESET, agent_slices, known_agents
from loadout.manifest import resolve_destination
from loadout.permissions.renderers import RENDERERS
from loadout.project import PROJECT_PRESET

RELOCATION_VARIABLE = {
    "claude": "CLAUDE_CONFIG_DIR",
    "codex": "CODEX_HOME",
    "opencode": "XDG_CONFIG_HOME",
    "pi": "PI_CODING_AGENT_DIR",
}


def test_the_preset_covers_exactly_the_supported_agents() -> None:
    """antigravity was dropped (ADR 0012); nothing else has been added quietly."""
    assert known_agents() == {"claude", "codex", "opencode", "pi"}


@pytest.mark.parametrize("agent", sorted(GLOBAL_PRESET))
def test_every_renderer_named_by_the_preset_exists(agent: str) -> None:
    for name, output in agent_slices(agent).items():
        if output.renderer is not None:
            assert output.renderer in RENDERERS, f"{agent}.{name}"


@pytest.mark.parametrize("agent", sorted(GLOBAL_PRESET))
def test_a_slice_is_written_somewhere_exactly_one_way(agent: str) -> None:
    """Either a destination or a staged output, never both and never neither."""
    for name, output in agent_slices(agent).items():
        assert (output.destination is None) != (output.output is None), f"{agent}.{name}"


@pytest.mark.parametrize("agent", sorted(GLOBAL_PRESET))
def test_destinations_follow_the_harness_config_variable(agent: str) -> None:
    """ADR 0011's table lives here so a manifest never spells a variable out."""
    variable = RELOCATION_VARIABLE[agent]
    for name, output in agent_slices(agent).items():
        if output.destination is not None:
            assert output.destination.startswith(f"${{{variable}:-"), f"{agent}.{name}"


@pytest.mark.parametrize("agent", sorted(GLOBAL_PRESET))
def test_every_destination_resolves_to_the_documented_default(
    agent: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the variable unset the fallback must reproduce the harness's own
    default path — the preset is only safe if it is a no-op unrelocated."""
    defaults = {
        "claude": "/.claude/",
        "codex": "/.codex/",
        "opencode": "/.config/opencode/",
        "pi": "/.pi/agent/",
    }
    monkeypatch.delenv(RELOCATION_VARIABLE[agent], raising=False)
    for name, output in agent_slices(agent).items():
        if output.destination is None:
            continue
        resolved = str(resolve_destination(output.destination, f"{agent}.{name}"))
        assert defaults[agent] in resolved, f"{agent}.{name} -> {resolved}"


def test_a_set_variable_relocates_every_slice_of_that_agent(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CLAUDE_CONFIG_DIR", "/moved")
    resolved = [
        str(resolve_destination(o.destination, "claude"))
        for o in agent_slices("claude").values()
        if o.destination is not None
    ]
    assert resolved and all(p.startswith("/moved/") for p in resolved)


def test_only_codexs_config_toml_slices_are_staged() -> None:
    """A staged slice's destination is another tool's merge step, not a file a
    harness reads. Recorded as a shape rather than an exception so it is not lost.

    Three of the four are Codex's, for one reason: `config.toml` carries
    `[projects.…]`, model settings and everything else Codex keeps, so loadout
    cannot rewrite it from a source. Claude's `mcp` is staged for a different
    reason — `${CLAUDE_CONFIG_DIR}/.claude.json` is runtime state loadout does
    not own either, and `claude mcp add-json` is the merge step outside it
    (ADR 0004). Every other slice writes a file it owns outright.
    """
    staged = {
        (agent, name)
        for agent, slices in GLOBAL_PRESET.items()
        for name, output in slices.items()
        if output.output is not None
    }
    assert staged == {
        ("codex", "mcp-permissions"),
        ("codex", "plugins"),
        ("codex", "mcp"),
        ("claude", "mcp"),
    }


def test_settings_is_never_a_source_slice() -> None:
    """Settings is the residual every file starts from, not one slice's input.

    It was a `source_slice` briefly; that made it one contributor among many, so
    whichever slice sorted first supplied the document and the rest of the file
    was lost. The residual is taken once, ahead of every slice.
    """
    named = {
        output.source_slice
        for slices in GLOBAL_PRESET.values()
        for output in slices.values()
        if output.source_slice is not None
    }
    assert "settings" not in named


def test_a_contributor_names_where_its_content_comes_from() -> None:
    """`owned_key` makes a slice produce one key's value; it needs its own
    fragments to produce it from, so the two always travel together — except
    `mcp`, whose content is `mcp.toml` itself, read directly rather than
    composed from named fragments, so it has no `source_slice` to name."""
    for agent, slices in GLOBAL_PRESET.items():
        for name, output in slices.items():
            if output.owned_key is not None and name != "mcp":
                assert output.source_slice is not None, f"{agent}.{name}"


@pytest.mark.parametrize("agent", sorted(GLOBAL_PRESET))
def test_a_named_source_slice_is_one_the_agent_could_declare(agent: str) -> None:
    """`source_slice` names a slice, not an arbitrary key — otherwise a target
    would read fragments from a block key that means something else."""
    for name, output in agent_slices(agent).items():
        if output.source_slice is not None:
            assert output.source_slice in ("settings", *agent_slices(agent)), f"{agent}.{name}"


@pytest.mark.parametrize("agent", sorted(PROJECT_PRESET))
def test_every_renderer_named_by_the_project_preset_exists(agent: str) -> None:
    for name, output in PROJECT_PRESET[agent].items():
        if output.renderer is not None:
            assert output.renderer in RENDERERS, f"{agent}.{name}"


@pytest.mark.parametrize("agent", sorted(PROJECT_PRESET))
def test_a_project_slice_is_written_relative_to_the_repo(agent: str) -> None:
    """`output` and `destination` are two kinds of path, and project scope may only
    use the first. A destination is a machine path template (ADR 0011); one here
    would put an absolute path from this machine into a committed project's
    generated tree, which is the same reason project scope carries no [[source]]."""
    for name, output in PROJECT_PRESET[agent].items():
        assert output.output is not None, f"{agent}.{name}"
        assert output.destination is None, f"{agent}.{name}"
        path = PurePosixPath(output.output)
        assert not path.is_absolute(), f"{agent}.{name}"
        assert ".." not in path.parts, f"{agent}.{name}"
        assert "${" not in output.output, f"{agent}.{name}"


def test_the_two_presets_agree_on_which_harnesses_exist() -> None:
    """One type, two tables — but a harness in one and not the other would render
    at one scope and silently generate nothing at the other."""
    assert set(PROJECT_PRESET) == set(GLOBAL_PRESET)
