"""Harness facts are restated in several modules; these keep the restatements in step
with the presets (review 4.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout import discovery, migration
from loadout.destinations import resolve_destination
from loadout.discovery import AGENTS_MD_READERS, discover
from loadout.harnesses import GLOBAL_PRESET, KNOWN_HARNESSES, SHARED_SKILL_READERS
from loadout.hooks import HARNESS_PREFIXES
from loadout.migration_models import Candidate
from loadout.notices import _ADAPTED as ADAPTED_EVENTS
from loadout.notices import known_events
from loadout.permissions.renderers import RENDERERS
from loadout.plugins import ADDRESSED_BY
from loadout.project import PROJECT_PRESET
from loadout.subagents import EFFORT_KEY, EFFORT_LEVELS

COMPLETE = {
    "agents.GLOBAL_PRESET": GLOBAL_PRESET,
    "project.PROJECT_PRESET": PROJECT_PRESET,
    "discovery.GLOBAL_ROOTS": discovery.GLOBAL_ROOTS,
    "discovery.PROJECT_ROOTS": discovery.PROJECT_ROOTS,
    "migration.OWNERS": migration.OWNERS,
}


@pytest.mark.parametrize("name", sorted(COMPLETE))
def test_each_harness_table_names_every_harness(name: str) -> None:
    assert set(COMPLETE[name]) == KNOWN_HARNESSES


@pytest.mark.parametrize("scope", ["global", "project"])
def test_managed_paths_name_every_harness_and_the_shared_roots(scope: str) -> None:
    assert set(discovery.MANAGED_PATHS[scope]) == KNOWN_HARNESSES | {"shared"}


def test_runtime_keys_belong_to_a_runtime_document() -> None:
    assert set(discovery.RUNTIME_KEYS) <= set(discovery.RUNTIME_DOCUMENTS) <= KNOWN_HARNESSES


def test_every_global_dormant_route_lies_under_its_own_slice_s_destination(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Review 2, 2.10: compared against every preset destination, a route passed as long
    as it lay under any harness root, which module-config's destinations are. A route
    now has to match its own category: a `settings` document one of the slices that
    take settings, and only a tree may sit anywhere under the harness's own root."""
    routed: list[tuple[Path, tuple[str, ...], str, str]] = []
    dormant = migration._PlanBuilder.dormant

    def record(self, destination, agents, category, format_name="copy"):  # type: ignore[no-untyped-def]
        routed.append((destination, agents, category, format_name))
        return dormant(self, destination, agents, category, format_name)

    monkeypatch.setattr(migration._PlanBuilder, "dormant", record)
    source = fake_home / "agent-config"
    source.mkdir()
    migration.plan_migration(discover(source, scope="global", harnesses=tuple(KNOWN_HARNESSES)))

    def allowed(agent: str, category: str, tree: bool) -> list[Path]:
        slices = GLOBAL_PRESET[agent]
        names = (
            [name for name, spec in slices.items() if spec.takes_settings]
            if category == "settings"
            else [category]
        )
        names += ["module-config"] if tree else []
        return [
            resolve_destination(spec.destination, f"{agent}.{name}")
            for name in names
            if (spec := slices.get(name)) is not None and spec.destination is not None
        ]

    stray = [
        (str(path), agent, category)
        for path, agents, category, format_name in routed
        for agent in agents
        if not any(
            path == wanted or path.is_relative_to(wanted)
            for wanted in allowed(agent, category, format_name == "tree")
        )
    ]
    assert len(routed) > len(KNOWN_HARNESSES)
    assert stray == []


def _harnesses_with(slice_name: str) -> set[str]:
    return {harness for harness, slices in GLOBAL_PRESET.items() if slice_name in slices}


@pytest.mark.parametrize("project", [False, True])
def test_every_harness_adopts_its_permissions_through_a_real_renderer(project: bool) -> None:
    """`_permission_renderer` returned None for a harness it did not name, which skipped
    that harness's permissions at adoption without a word."""
    for harness in KNOWN_HARNESSES:
        path = Path("permissions.rules" if harness == "codex" else "settings.json")
        candidate = Candidate(path, None, path, (harness,), "permissions", "migrated", "")
        assert migration._permission_renderer(candidate, project) in RENDERERS, harness


def test_every_harness_reads_either_claude_md_or_agents_md() -> None:
    claude_md = {
        harness
        for harness, slices in PROJECT_PRESET.items()
        if slices["instructions"].output == "CLAUDE.md"
    }
    assert claude_md.isdisjoint(AGENTS_MD_READERS)
    assert claude_md | set(AGENTS_MD_READERS) == KNOWN_HARNESSES


def test_every_harness_with_hooks_has_a_variable_namespace_or_an_adapter() -> None:
    hooks = _harnesses_with("hooks")
    assert set(HARNESS_PREFIXES).isdisjoint(ADAPTED_EVENTS)
    assert set(HARNESS_PREFIXES) | set(ADAPTED_EVENTS) == hooks
    assert all(known_events(harness) for harness in hooks)


def test_every_harness_with_plugins_says_how_it_addresses_one() -> None:
    assert set(ADDRESSED_BY) == _harnesses_with("plugins")


def test_every_harness_with_agents_says_how_it_spells_effort() -> None:
    """OpenCode alone has no effort key: its agents get a note instead."""
    assert set(EFFORT_LEVELS) == set(EFFORT_KEY)
    assert set(EFFORT_KEY) | {"opencode"} == _harnesses_with("agents")


def test_the_shared_skill_readers_include_the_harness_that_writes_there() -> None:
    writers = {
        harness
        for harness, slices in PROJECT_PRESET.items()
        if (spec := slices.get("skills")) is not None and spec.output == ".agents/skills"
    }
    assert writers and writers <= set(SHARED_SKILL_READERS) <= _harnesses_with("skills")
