"""Harness facts are restated in several modules; these keep the restatements in step
with the presets (review 4.1)."""

from __future__ import annotations

from pathlib import Path

import pytest

from loadout import discovery, migration
from loadout.agents import GLOBAL_PRESET
from loadout.destinations import resolve_destination
from loadout.discovery import discover
from loadout.harnesses import KNOWN_HARNESSES
from loadout.project import PROJECT_PRESET

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


def test_every_global_dormant_route_lies_under_a_preset_destination(
    fake_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    routed: list[Path] = []
    dormant = migration._PlanBuilder.dormant

    def record(self, destination, *args, **kwargs):  # type: ignore[no-untyped-def]
        routed.append(destination)
        return dormant(self, destination, *args, **kwargs)

    monkeypatch.setattr(migration._PlanBuilder, "dormant", record)
    source = fake_home / "agent-config"
    source.mkdir()
    migration.plan_migration(discover(source, scope="global", harnesses=tuple(KNOWN_HARNESSES)))
    presets = [
        resolve_destination(spec.destination, f"{agent}.{name}")
        for agent, slices in GLOBAL_PRESET.items()
        for name, spec in slices.items()
        if spec.destination is not None
    ]

    assert routed
    assert [p for p in routed if not any(p == d or p.is_relative_to(d) for d in presets)] == []
