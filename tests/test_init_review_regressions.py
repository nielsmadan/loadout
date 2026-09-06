from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import loadout.migration as planner
from loadout import init_workflow, migration_git, migration_transaction
from loadout.commands import cmd_check, cmd_sync
from loadout.discovery import discover
from loadout.errors import LoadoutError
from loadout.init_options import InitOptions
from loadout.migration import plan_migration
from loadout.migration_journal import Journal
from loadout.migration_models import RootMapping
from loadout.migration_paths import DestinationLayout
from loadout.migration_transaction import apply_migration, prepare_migration, recover_migration
from loadout.native_documents import parse_document
from run_init_corpus import expected_outputs
from test_migration import generated, index, migration, write
from test_migration_transaction import git, repository


@pytest.mark.parametrize(
    "agent,name,content",
    [
        ("claude", ".claude.json", '{"oauthAccount":{"accountUuid":"fixture"}}\n'),
        (
            "codex",
            ".codex/config.toml",
            '# foreign\n[projects."/fixture"]\ntrust_level="trusted"\n',
        ),
        ("pi", ".pi/agent/settings.json", '{"lastChangelogVersion":"fixture"}\n'),
    ],
)
def test_runtime_only_partial_files_apply_and_recover(tmp_path, fake_home, agent, name, content):
    repository(tmp_path)
    destination = write(fake_home, name, content, 0o640)
    plan = plan_migration(discover(tmp_path, scope="global", agents=(agent,)))
    assert plan.complete, plan.preview()
    result = apply_migration(prepare_migration(plan))
    assert destination.read_bytes() == content.encode()
    assert destination.stat().st_mode & 0o777 == 0o640
    assert cmd_check(tmp_path / "loadout") == 0
    assert result.journal is not None
    assert migration_transaction.resume_migration(result.journal).baseline == result.baseline
    assert recover_migration(result.journal).conflicts == ()
    assert destination.read_bytes() == content.encode()


@pytest.mark.parametrize(
    "case",
    [
        (
            "claude",
            ".claude.json",
            '{"oauthAccount":{"fixture":true}}\n',
            "mcp",
            '{"mcpServers":{"fixture":{"command":"fixture"}}}',
            "oauthAccount",
        ),
        (
            "codex",
            ".codex/config.toml",
            '[projects."/fixture"]\ntrust_level="trusted"\n',
            "settings",
            'model="fixture"\n',
            "projects",
        ),
        (
            "pi",
            ".pi/agent/settings.json",
            '{"lastChangelogVersion":"fixture"}\n',
            "settings",
            '{"defaultModel":"fixture"}',
            "lastChangelogVersion",
        ),
    ],
)
def test_runtime_only_partial_resume_then_first_activation(tmp_path, fake_home, monkeypatch, case):
    agent, name, content, category, update, runtime = case
    repository(tmp_path)
    destination = write(fake_home, name, content, 0o640)
    plan = plan_migration(discover(tmp_path, scope="global", agents=(agent,)))
    prepared = prepare_migration(plan)
    step = Journal.step

    def interrupt(journal):
        if journal.operations[journal.next].phase == "ignore":
            raise OSError("fixture interruption")
        step(journal)

    monkeypatch.setattr(Journal, "step", interrupt)
    with pytest.raises(migration_transaction.MigrationFailure) as failure:
        apply_migration(prepared)
    monkeypatch.setattr(Journal, "step", step)
    migration_transaction.resume_migration(failure.value.journal)
    assert destination.read_bytes() == content.encode()
    record = next(
        r
        for r in index(plan)["artifact"]
        if r.get("partial") and r["destination"].endswith(Path(name).name)
    )
    source = plan.inventory.source_root / record["parts"][category]["source"]
    source.write_text(update)
    assert cmd_sync(plan.inventory.source_root) == 0
    document = parse_document(destination.read_text(), destination.suffix[1:])
    assert document[runtime] == parse_document(content, destination.suffix[1:])[runtime]
    authored = parse_document(update, destination.suffix[1:])
    assert all(document[key] == value for key, value in authored.items())
    assert destination.stat().st_mode & 0o777 == 0o640
    assert cmd_check(plan.inventory.source_root) == 0


@pytest.mark.parametrize(
    "case",
    [
        (
            "claude",
            ".claude.json",
            "mcp",
            '{"oauthAccount":{"fixture":true},"mcpServers":{}}',
            "oauthAccount",
            "mcpServers",
        ),
        (
            "codex",
            "config.toml",
            "settings",
            'model="fixture"\n[projects."/gone"]\ntrust_level="trusted"\n',
            "projects",
            "model",
        ),
        (
            "pi",
            "settings.json",
            "settings",
            '{"lastChangelogVersion":"fixture","defaultModel":"fixture"}',
            "lastChangelogVersion",
            "defaultModel",
        ),
    ],
)
@pytest.mark.parametrize("source_suffix", ["same", ".backup", ""])
def test_renamed_explicit_documents_keep_destination_ownership(
    tmp_path, fake_home, case, source_suffix
):
    agent, name, category, content, runtime, authored = case
    suffix = Path(name).suffix if source_suffix == "same" else source_suffix
    source = write(tmp_path, "renamed" + suffix, content)
    destination = fake_home / "relocated" / name
    mapping = RootMapping(source, destination, (agent,), "file", category)
    plan = plan_migration(discover(tmp_path, scope="global", agents=(agent,), mappings=(mapping,)))
    assert plan.complete, plan.preview()
    record = next(r for r in index(plan)["artifact"] if r["destination"] == str(destination))
    assert record["partial"] is True
    document = generated(plan, destination).decode()
    assert authored in document
    assert runtime not in document
    assert source in plan.private_paths
    assert source not in plan.checkpoint_paths
    assert runtime not in b"\n".join(w.content for w in plan.source_writes).decode()


@pytest.mark.parametrize("directory", ["projects", "history", "debug"])
def test_untracked_project_runtime_named_directories_keep_instructions(tmp_path, directory):
    path = write(tmp_path, f"{directory}/nested/CLAUDE.md", "project instructions\n")
    assert generated(migration(tmp_path), path) == b"project instructions\n"


@pytest.mark.parametrize("name", ["auth.json", ".credentials.json", "trust.json"])
def test_nested_runtime_files_remain_original_and_outside_both_git_phases(tmp_path, name):
    repository(tmp_path)
    write(tmp_path, ".claude/skills/test/SKILL.md", "inert skill\n")
    runtime = write(tmp_path, f".claude/skills/test/{name}", '{"fixture":"runtime"}\n')
    plan = migration(tmp_path)
    candidate = next(c for c in plan.inventory.candidates if c.path == runtime)
    assert candidate.disposition == "runtime-private exclusion"
    prepared = prepare_migration(plan)
    result = apply_migration(prepared)
    assert runtime.read_bytes() == b'{"fixture":"runtime"}\n'
    assert all(w.content != runtime.read_bytes() for w in plan.source_writes)
    assert prepared.git is not None
    assert runtime.relative_to(tmp_path).as_posix() not in prepared.git.baseline
    assert git(tmp_path, "ls-files", "--", str(runtime)) == b""
    assert result.journal is not None
    assert recover_migration(result.journal).conflicts == ()
    assert runtime.read_bytes() == b'{"fixture":"runtime"}\n'


@pytest.mark.parametrize(
    "name,content,private",
    [
        ("config.json", b'{"apiKey":"synthetic-fixture"}\n', True),
        ("config.toml", b'"api_key" = "synthetic-fixture"\n', True),
        ("config.json", b'{"apiKey":"${FIXTURE_KEY}"}\n', False),
        ("config.toml", b'"api_key" = "${FIXTURE_KEY}"\n', False),
    ],
)
def test_opaque_support_credentials_use_structural_privacy(tmp_path, name, content, private):
    path = write(tmp_path, f".claude/skills/test/{name}", content, 0o640)
    plan = migration(tmp_path)
    candidate = next(c for c in plan.inventory.candidates if c.path == path)
    assert candidate.format == "copy"
    assert candidate.private is private
    copied = next(w for w in plan.source_writes if w.content == content)
    assert copied.private is private
    assert generated(plan, path) == content
    assert next(w.mode for w in plan.generated_writes if w.path == path) == 0o640


def test_interactive_invalid_source_selection_can_be_corrected(tmp_path, fake_home, monkeypatch):
    source = write(tmp_path, "claude/settings.json", '{"model":"source"}')
    destination = write(fake_home, ".claude/settings.json", '{"model":"live"}')
    choices = iter(
        json.dumps({"source": str(path), "destination": str(destination)})
        for path in (source.with_name("typo.json"), source)
    )
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _: next(choices))
    options = InitOptions(
        tmp_path,
        scope="global",
        source=tmp_path,
        agents=("claude",),
        mappings=(RootMapping(source.parent, destination.parent, ("claude",)),),
        yes=True,
    )
    resolved, plan = init_workflow._resolve(options)
    assert plan.complete, plan.preview()
    assert len(resolved.selections) == 1
    assert json.loads(generated(plan, destination)) == {"model": "source"}


def test_private_paths_are_normalized_once_per_filesystem_plan(tmp_path, monkeypatch):
    for number in range(12):
        write(tmp_path, f".claude/skills/test/{number}.bin", "fixture")
    write(tmp_path, ".claude/skills/test/settings.local.json", "{}")
    plan = migration(tmp_path)
    calls = 0
    original = DestinationLayout.normalize

    def counted(self, path):
        nonlocal calls
        calls += 1
        return original(self, path)

    monkeypatch.setattr(DestinationLayout, "normalize", counted)
    migration_transaction._filesystem_plan(plan, None)
    assert calls <= 4 * (len(plan.source_writes) + len(plan.private_paths))


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


def mapped_retirements(root, home, count=3):
    repository(root)
    for number in range(count):
        write(root, f"claude/skills/test/{number:03}.bin", f"original {number}\n")
    mapping = RootMapping(root / "claude", home / ".claude", ("claude",))
    plan = plan_migration(discover(root, scope="global", agents=("claude",), mappings=(mapping,)))
    assert plan.complete, plan.preview()
    return prepare_migration(plan)


def test_retirement_guards_do_not_rescan_all_completed_payloads(tmp_path, fake_home, monkeypatch):
    prepared = mapped_retirements(tmp_path, fake_home, 12)
    calls = 0
    original = migration_transaction._guard_completed

    def counted(journal):
        nonlocal calls
        calls += 1
        original(journal)

    monkeypatch.setattr(migration_transaction, "_guard_completed", counted)
    apply_migration(prepared)
    assert calls <= 6


def test_each_retirement_refreshes_privacy_with_deduplicated_parent_queries(
    tmp_path, fake_home, monkeypatch
):
    prepared = mapped_retirements(tmp_path, fake_home, 12)
    policy = migration_git.privacy_policy
    repository_query = migration_git._privacy_repository
    step = Journal.step
    queries = []
    checks = []
    retired = []

    def counted(parent, cache):
        queries.append(parent)
        return repository_query(parent, cache)

    def checked(root, paths, **kwargs):
        before = len(queries)
        result = policy(root, paths, **kwargs)
        assert len(queries) - before == len({path.parent for path in paths})
        checks.append(result)
        return result

    def retire(journal):
        if journal.operations[journal.next].phase == "retire":
            assert len(checks) > (retired[-1] if retired else 0)
            retired.append(len(checks))
        step(journal)

    monkeypatch.setattr(migration_git, "_privacy_repository", counted)
    monkeypatch.setattr(migration_git, "privacy_policy", checked)
    monkeypatch.setattr(Journal, "step", retire)
    result = apply_migration(prepared)
    assert len(retired) == 12
    assert result.baseline == git(tmp_path, "rev-parse", "HEAD").decode().strip()
    assert len(checks) > retired[-1]


@pytest.mark.parametrize("changed", ["output", "source", "parent", "privacy", "index"])
def test_mid_retirement_changes_refuse_before_the_next_original(
    tmp_path, fake_home, monkeypatch, changed
):
    prepared = mapped_retirements(tmp_path, fake_home)
    retirements = [o for o in prepared.operations if o.phase == "retire"]
    output = fake_home / ".claude/skills/test/001.bin"
    source = next(w.path for w in prepared.plan.source_writes if w.content == b"original 1\n")
    original = Journal.step
    retired = 0

    def mutate(journal):
        nonlocal retired
        operation = journal.operations[journal.next]
        original(journal)
        if operation.phase != "retire":
            return
        retired += 1
        if retired != 1:
            return
        if changed == "output":
            output.write_bytes(b"hostile replacement\n")
        elif changed == "source":
            source.write_bytes(b"hostile source\n")
        elif changed == "parent":
            parent = output.parent
            parent.rename(parent.with_name("moved"))
            parent.symlink_to(parent.with_name("moved"), target_is_directory=True)
        elif changed == "privacy":
            (tmp_path / ".git/info/exclude").write_text("loadout/\n")
        else:
            (tmp_path / ".git/index").write_bytes(b"changed index")

    monkeypatch.setattr(Journal, "step", mutate)
    with pytest.raises(migration_transaction.MigrationFailure):
        apply_migration(prepared)
    assert retired == 1
    assert retirements[1].path.read_bytes() == retirements[1].before.content


@pytest.mark.parametrize("changed", ["bytes", "mode"])
def test_each_retirement_checks_exact_replacement_even_with_unchanged_fingerprint(
    tmp_path, fake_home, monkeypatch, changed
):
    prepared = mapped_retirements(tmp_path, fake_home)
    monkeypatch.setattr(migration_transaction, "_entry_fingerprint", lambda _: ())
    original = Journal.step
    retired = 0

    def mutate(journal):
        nonlocal retired
        operation = journal.operations[journal.next]
        original(journal)
        if operation.phase == "retire":
            retired += 1
            if retired == 1:
                output = fake_home / ".claude/skills/test/001.bin"
                if changed == "bytes":
                    output.write_bytes(b"changed output\n")
                else:
                    output.chmod(0o700)

    monkeypatch.setattr(Journal, "step", mutate)
    with pytest.raises(LoadoutError, match="output changed"):
        apply_migration(prepared)
    assert retired == 1


@pytest.mark.parametrize("mapping", ["missing", "ambiguous"])
def test_retirement_without_replacement_mapping_keeps_full_output_guards(
    tmp_path, fake_home, monkeypatch, mapping
):
    prepared = mapped_retirements(tmp_path, fake_home)
    monkeypatch.setattr(migration_transaction, "_entry_fingerprint", lambda _: ())
    original = Journal.step
    retired = 0

    def mutate(journal):
        nonlocal retired
        operation = journal.operations[journal.next]
        original(journal)
        if operation.phase != "retire":
            return
        retired += 1
        if retired == 1:
            if mapping == "missing":
                journal.metadata.pop("retirement_outputs")
            else:
                journal.metadata["retirement_outputs"] = {
                    str(journal.operations[journal.next].path): []
                }
            (fake_home / ".claude/skills/test/001.bin").write_bytes(b"changed output\n")

    monkeypatch.setattr(Journal, "step", mutate)
    with pytest.raises(LoadoutError, match="migration entry changed"):
        apply_migration(prepared)
    assert retired == 1
    assert (tmp_path / "claude/skills/test/001.bin").read_bytes() == b"original 1\n"
