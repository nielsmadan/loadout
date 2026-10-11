from __future__ import annotations

import pytest

import loadout.migration as planner
from loadout.migration_transaction import prepare_migration, recover_migration
from run_init_corpus import expected_outputs
from test_migration import migration, write
from test_migration_transaction import git, interrupted_completion, repository

pytestmark = pytest.mark.migration_integration


@pytest.mark.parametrize("name", ["auth.json", ".credentials.json", "trust.json"])
def test_nested_runtime_files_remain_original_and_outside_both_git_phases(tmp_path, name):
    repository(tmp_path)
    write(tmp_path, ".claude/skills/test/SKILL.md", "inert skill\n")
    runtime = write(tmp_path, f".claude/skills/test/{name}", '{"fixture":"runtime"}\n')
    plan = migration(tmp_path)
    candidate = next(c for c in plan.inventory.candidates if c.path == runtime)
    assert candidate.disposition == "runtime-private exclusion"
    prepared = prepare_migration(plan)
    journal = interrupted_completion(prepared)
    assert runtime.read_bytes() == b'{"fixture":"runtime"}\n'
    assert all(w.content != runtime.read_bytes() for w in plan.source_writes)
    assert prepared.git is not None
    assert runtime.relative_to(tmp_path).as_posix() not in prepared.git.baseline
    assert git(tmp_path, "ls-files", "--", str(runtime)) == b""
    assert recover_migration(journal).conflicts == ()
    assert runtime.read_bytes() == b'{"fixture":"runtime"}\n'


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
