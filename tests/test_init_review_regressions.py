from __future__ import annotations

import pytest

import loadout.migration as planner
from run_init_corpus import expected_outputs
from test_migration import migration, write

pytestmark = pytest.mark.migration_integration


def test_corpus_oracle_catches_joint_planner_renderer_omission(tmp_path, monkeypatch):
    original = write(tmp_path, "CLAUDE.md", "must reconstruct\n")
    opaque = planner._PlanBuilder.opaque

    def omit(self, candidate, *, existing=True):
        if candidate.path != original:
            opaque(self, candidate, existing=existing)

    monkeypatch.setattr(planner._PlanBuilder, "opaque", omit)
    plan = migration(tmp_path)
    assert original not in {w.path for w in plan.generated_writes}
    assert (
        next(c for c in plan.inventory.candidates if c.path == original).disposition == "migrated"
    )
    with pytest.raises(AssertionError, match="output inventory"):
        expected_outputs(plan.preview())
