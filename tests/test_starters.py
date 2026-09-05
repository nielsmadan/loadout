from __future__ import annotations

import json
import shutil
import stat
import subprocess
import sys
from pathlib import Path
from zipfile import ZipFile

import pytest

from loadout import migration_git, migration_transaction
from loadout.artifacts import CATEGORIES, Copied
from loadout.bundled_templates import BUNDLED, STARTERS, bundled_template
from loadout.cli import main
from loadout.discovery import discover
from loadout.emit import render_project
from loadout.errors import LoadoutError
from loadout.machine import machine_config_path
from loadout.migration import plan_migration
from loadout.migration_journal import Journal
from loadout.migration_models import MigrationPlan
from loadout.migration_transaction import (
    MigrationFailure,
    MigrationPreparation,
    apply_migration,
    prepare_migration,
    recover_migration,
    resume_migration,
)
from loadout.project import load_project_config, project_config_path
from loadout.templates import VENDORED, resolve_template, tree_hash, vendored_path

ROOT = Path(__file__).parents[1]


def _git(root: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True
    ).stdout.strip()


def _repo(root: Path) -> None:
    _git(root, "init", "-q")
    _git(root, "config", "user.name", "Starter fixture")
    _git(root, "config", "user.email", "starter@example.invalid")


def _source(fake_home: Path, *names: str) -> tuple[Path, ...]:
    global_root = fake_home / "source"
    global_root.mkdir()
    paths = []
    manifest = []
    for name in names:
        tree = global_root / name / "templates/frontend"
        tree.mkdir(parents=True)
        (tree / "instructions.md").write_text(f"{name} advice\n")
        paths.append(tree)
        manifest.append(f'[[source]]\nname = "{name}"\npath = "{name}"\nuse = ["templates"]\n')
    (global_root / "loadout.toml").write_text(
        'artifacts = "artifacts.toml"\n' + "\n".join(manifest)
    )
    (global_root / "artifacts.toml").write_text("artifact = []\n")
    config = machine_config_path()
    config.parent.mkdir(parents=True)
    config.write_text(f'source = "{global_root}"\n')
    return tuple(paths)


def _materialize(plan: MigrationPlan, root: Path) -> None:
    for write in plan.source_writes:
        path = root / "loadout" / write.path.relative_to(plan.inventory.source_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(write.content)
        path.chmod(write.mode)


def _init(root: Path, starter: str | None = None) -> list[str]:
    args = ["init", "--project", "--harness", "claude", "--root", str(root)]
    return args + (["--starter", starter] if starter else [])


@pytest.mark.parametrize("name", STARTERS)
def test_bundled_templates_resolve_offline_without_machine_config(
    tmp_path: Path, name: str
) -> None:
    found = resolve_template(name, tmp_path)
    assert found.source == BUNDLED
    assert found.path == bundled_template(name)
    assert {p.name for p in found.path.iterdir() if p.is_dir()} == CATEGORIES
    assert len((found.path / "instructions.md").read_text().split()) > 35
    assert (found.path / "permissions.toml").read_bytes().strip() == b""
    assert (found.path / "mcp.toml").read_bytes().strip() == b""


def test_installed_wheel_renders_bundled_templates_offline(tmp_path: Path) -> None:
    built = subprocess.run(
        ["uv", "build", "--wheel", "--out-dir", str(tmp_path)],
        cwd=ROOT,
        check=False,
        capture_output=True,
    )
    assert built.returncode == 0, built.stderr.decode()
    (wheel,) = tmp_path.glob("*.whl")
    installed = tmp_path / "installed"
    with ZipFile(wheel) as archive:
        archive.extractall(installed)
    script = """
import pathlib, socket, sys
sys.path.insert(0, sys.argv[1])
import loadout
from loadout.artifacts import CATEGORIES
from loadout.templates import resolve_template, copy_tree, declare, record_hash, tree_hash
from loadout.project import load_project_config, project_config_path
from loadout.emit import render_project
assert pathlib.Path(loadout.__file__).is_relative_to(sys.argv[1])
socket.socket = lambda *a, **k: (_ for _ in ()).throw(AssertionError('network access'))
for name in ('frontend', 'backend'):
    root = pathlib.Path(sys.argv[2]) / name
    (root / 'loadout').mkdir(parents=True)
    project_config_path(root).write_text('harnesses = ["claude"]\\n')
    (root / 'loadout/permissions.toml').write_text('')
    found = resolve_template(name, root)
    assert found.source == '(bundled)'
    assert all((found.path / category / '.gitkeep').is_file() for category in CATEGORIES)
    local = root / 'loadout/templates' / name
    copy_tree(found.path, local)
    declare(root, name)
    record_hash(root, name, tree_hash(local))
    config = load_project_config(project_config_path(root))
    assert config.vendored_hash(name) == tree_hash(found.path)
    assert '# ' + name.capitalize() + ' work' in render_project(root)[root / 'CLAUDE.md']
print('both installed starters rendered offline')
"""
    result = subprocess.run(
        [sys.executable, "-I", "-c", script, str(installed), str(tmp_path / "projects")],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.strip() == "both installed starters rendered offline"


def test_named_sources_and_vendoring_keep_priority_over_bundle(
    tmp_path: Path, fake_home: Path
) -> None:
    (source,) = _source(fake_home, "company")
    bundled = bundled_template("frontend")
    assert bundled is not None and (bundled / "instructions.md").is_file()
    assert resolve_template("frontend", tmp_path).path == source
    assert resolve_template("company/frontend", tmp_path).path == source
    local = vendored_path(tmp_path, "frontend")
    local.mkdir(parents=True)
    (local / "instructions.md").write_text("local advice\n")
    machine_config_path().write_text("invalid machine TOML")
    found = resolve_template("frontend", tmp_path)
    assert found.source == VENDORED
    assert (found.path / "instructions.md").read_text() == "local advice\n"
    assert (source / "instructions.md").read_text() == "company advice\n"


def test_duplicate_named_sources_still_refuse_bundle_fallback(
    tmp_path: Path, fake_home: Path
) -> None:
    first, second = _source(fake_home, "company", "personal")
    assert first.is_dir() and second.is_dir() and bundled_template("frontend").is_dir()
    with pytest.raises(LoadoutError, match="ambiguous across sources"):
        resolve_template("frontend", tmp_path)
    assert resolve_template("personal/frontend", tmp_path).path == second


def test_bundle_is_used_when_declared_sources_have_no_match(
    tmp_path: Path, fake_home: Path
) -> None:
    _source(fake_home, "company")
    assert resolve_template("backend", tmp_path).source == BUNDLED
    machine_config_path().write_text("invalid machine TOML")
    with pytest.raises(LoadoutError):
        resolve_template("backend", tmp_path)


@pytest.mark.parametrize("starter", (None, "none", "frontend", "backend"))
def test_plan_freezes_starter_and_preserves_agent_bodies_and_modes(
    tmp_path: Path, starter: str | None
) -> None:
    original = b"Claude rules\r\nKeep spacing.  \r\n"
    shared = b"Codex rules without final newline"
    (tmp_path / "CLAUDE.md").write_bytes(original)
    (tmp_path / "CLAUDE.md").chmod(0o751)
    (tmp_path / "AGENTS.md").write_bytes(shared)
    (tmp_path / "AGENTS.md").chmod(0o640)
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude", "codex")), starter=starter
    )
    assert plan.complete, plan.issues
    assert plan.preview()["starter"] == (starter or "none")
    generated = {w.path.name: w for w in plan.generated_writes}
    for name, body, mode in (("CLAUDE.md", original, 0o751), ("AGENTS.md", shared, 0o640)):
        assert generated[name].content.endswith(body)
        assert generated[name].mode == mode
        assert any(w.content == body for w in plan.source_writes)
        if starter in STARTERS:
            seed = (bundled_template(starter) / "instructions.md").read_bytes().strip()
            assert generated[name].content == seed + b"\n\n" + body
        else:
            assert generated[name].content == body
    assert not (tmp_path / "loadout").exists()
    clone = tmp_path / "clone"
    _materialize(plan, clone)
    config = load_project_config(project_config_path(clone))
    assert config.templates == ((starter,) if starter in STARTERS else ())
    if starter in STARTERS:
        assert config.vendored_hash(starter) == tree_hash(vendored_path(clone, starter))
    outputs = render_project(clone)
    assert isinstance(outputs[clone / "CLAUDE.md"], Copied)
    assert outputs[clone / "CLAUDE.md"].read_bytes() == generated["CLAUDE.md"].content


def test_explicit_routes_leave_nested_instructions_and_commands_intact(tmp_path: Path) -> None:
    nested = tmp_path / "src/AGENTS.md"
    nested.parent.mkdir()
    nested.write_text("Nested instructions\n")
    command = tmp_path / ".claude/commands/review.md"
    command.parent.mkdir(parents=True)
    command.write_text("Review command\n")
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude", "codex")), starter="backend"
    )
    assert plan.complete, plan.issues
    outputs = {w.path: w.content for w in plan.generated_writes}
    assert outputs[nested] == b"Nested instructions\n"
    assert outputs[command] == b"Review command\n"
    assert outputs[tmp_path / "CLAUDE.md"].startswith(b"# Backend work")
    assert outputs[tmp_path / "AGENTS.md"].startswith(b"# Backend work")


@pytest.mark.parametrize("operation", ("add", "vendor"))
def test_native_template_commands_refuse_missing_route_before_mutation(
    tmp_path: Path, operation: str
) -> None:
    source = tmp_path / "loadout"
    source.mkdir()
    config = source / "config.toml"
    config.write_text('harnesses = ["claude"]\npresets = false\nartifacts = "artifacts.toml"\n')
    (source / "artifacts.toml").write_text("artifact = []\n")
    before = config.read_bytes()
    assert main(["template", operation, "frontend", "--root", str(tmp_path)]) == 3
    assert config.read_bytes() == before
    assert not (source / "templates").exists()


@pytest.mark.parametrize(
    "relative,content",
    [
        ("permissions.toml", '[shell]\nallow = ["npm"]\n'),
        ("skills/probe/SKILL.md", "Skill\n"),
        ("hooks/config.json", '{"hooks":{}}'),
    ],
)
def test_unsupported_template_contribution_refuses_before_migration(
    tmp_path: Path, fake_home: Path, relative: str, content: str
) -> None:
    (source,) = _source(fake_home, "company")
    extra = source / relative
    extra.parent.mkdir(parents=True, exist_ok=True)
    extra.write_text(content)
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",)), starter="frontend"
    )
    assert not plan.complete
    assert plan.issues[-1].code == "starter-selection"
    assert "cannot compose with native artifact routes" in plan.issues[-1].message
    assert relative in plan.issues[-1].message
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("private", ("ignored", "credential", "symlink", "directory-link"))
def test_starter_overrides_cannot_promote_private_or_external_source(
    tmp_path: Path, fake_home: Path, private: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    (source,) = _source(fake_home, "company")
    path = source / "instructions.md"
    if private == "ignored":
        _repo(source)
        (source / ".gitignore").write_text("instructions.md\n")
    elif private == "credential":
        path.write_text("token = literal-do-not-print\n")
    else:
        secret = fake_home / "external-secret"
        secret.write_text("private external bytes\n")
        if private == "symlink":
            path.unlink()
            path.symlink_to(secret)
        else:
            (source / "external").symlink_to(secret.parent, target_is_directory=True)
        read_bytes = Path.read_bytes

        def read(candidate: Path) -> bytes:
            assert candidate.resolve() != secret, "preview read an external symlink target"
            return read_bytes(candidate)

        monkeypatch.setattr(Path, "read_bytes", read)
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",)), starter="frontend"
    )
    assert not plan.complete
    assert plan.issues[-1].code == "starter-selection"
    assert "literal-do-not-print" not in json.dumps(plan.preview())
    reason = (
        "symlink" if private in {"symlink", "directory-link"} else "private or credential-bearing"
    )
    assert reason in plan.issues[-1].message
    assert "private external bytes" not in json.dumps(plan.preview())
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("agent", ("claude", "codex", "opencode", "pi"))
def test_starter_has_an_explicit_instruction_route_for_each_agent(
    tmp_path: Path, agent: str
) -> None:
    plan = plan_migration(discover(tmp_path, scope="project", agents=(agent,)), starter="frontend")
    assert plan.complete, plan.issues
    output = tmp_path / ("CLAUDE.md" if agent == "claude" else "AGENTS.md")
    assert next(w for w in plan.generated_writes if w.path == output).content.startswith(
        b"# Frontend work"
    )


@pytest.mark.parametrize("operation", ("add", "vendor"))
def test_populated_native_template_command_preflights_before_mutation(
    tmp_path: Path, fake_home: Path, operation: str
) -> None:
    plan = plan_migration(discover(tmp_path, scope="project", agents=("claude",)))
    assert plan.complete, plan.issues
    _materialize(plan, tmp_path)
    (upstream,) = _source(fake_home, "company")
    (upstream / "permissions.toml").write_text('[shell]\nallow = ["npm"]\n')
    before = project_config_path(tmp_path).read_bytes()
    assert main(["template", operation, "frontend", "--root", str(tmp_path)]) == 3
    assert project_config_path(tmp_path).read_bytes() == before
    assert not vendored_path(tmp_path, "frontend").exists()


def test_template_changes_after_preview_block_preparation(tmp_path: Path, fake_home: Path) -> None:
    (source,) = _source(fake_home, "company")
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",)), starter="frontend"
    )
    assert plan.complete, plan.issues
    (source / "instructions.md").write_text("Changed since preview\n")
    with pytest.raises(LoadoutError, match="changed"):
        prepare_migration(plan)
    assert list(tmp_path.iterdir()) == []


def _interrupted_starter(
    prepared: MigrationPreparation, phase: str, monkeypatch: pytest.MonkeyPatch
) -> Path:
    def fail(*args: object) -> None:
        raise KeyboardInterrupt()

    real_replace = migration_git.replace_index
    count = 0

    def replace_index(path: Path, before: bytes | None, after: bytes | None) -> None:
        nonlocal count
        count += 1
        real_replace(path, before, after)
        if count == 2:
            raise KeyboardInterrupt()

    with monkeypatch.context() as patch:
        if phase == "checkpoint":
            patch.setattr(migration_git, "checkpoint", fail)
        elif phase == "finish":
            patch.setattr(migration_transaction, "_finish", fail)
        else:
            assert phase == "after-index"
            patch.setattr(migration_git, "replace_index", replace_index)
        with pytest.raises(MigrationFailure) as failure:
            apply_migration(prepared)
    assert (
        Journal.load(failure.value.journal).status
        == {
            "checkpoint": "checkpoint",
            "finish": "apply",
            "after-index": "stage-index",
        }[phase]
    )
    return failure.value.journal


def _upstream_privacy_rules(
    upstream_repo: Path, fake_home: Path, policy: str
) -> tuple[Path, Path | None]:
    if policy == "gitignore":
        rules = upstream_repo / ".gitignore"
        rules.write_text("")
        return rules, None
    rules = fake_home / "upstream-ignore-rules"
    rules.write_text("")
    if policy == "core-excludes":
        link = fake_home / "upstream-excludes"
        _git(upstream_repo, "config", "core.excludesFile", str(link))
    else:
        link = upstream_repo / ".git/info/exclude"
        link.unlink()
    link.symlink_to(rules)
    return rules, link


@pytest.mark.parametrize(
    "case",
    [
        pytest.param((policy, phase, True), id=f"{policy}-{phase}-True")
        for policy in ("gitignore", "core-excludes", "info-exclude")
        for phase in ("prepare", "apply", "checkpoint", "finish", "after-index")
    ]
    + [
        pytest.param((policy, phase, False), id=f"{policy}-{phase}-False")
        for policy in ("core-excludes", "info-exclude")
        for phase in ("prepare", "finish")
    ],
)
def test_starter_upstream_privacy_remains_guarded(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    case: tuple[str, str, bool],
) -> None:
    policy, phase, ignored = case
    _repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("Original body\n")
    (source,) = _source(fake_home, "company")
    upstream_repo = source.parents[1]
    _repo(upstream_repo)
    rules, link = _upstream_privacy_rules(upstream_repo, fake_home, policy)
    original = source / "instructions.md"
    assert migration_git.ignored_original(original) is False
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",)), starter="frontend"
    )
    assert plan.complete, plan.issues
    prepared = prepare_migration(plan) if phase != "prepare" else None
    journal = None
    if phase in {"checkpoint", "finish", "after-index"}:
        assert prepared is not None
        journal = _interrupted_starter(prepared, phase, monkeypatch)
    head = migration_git.identity(tmp_path)
    index = migration_git.index_bytes(tmp_path / ".git/index")
    rule = "templates/frontend/instructions.md\n" if ignored else "unrelated-future-file\n"
    rules.write_text(rule)
    assert migration_git.ignored_original(original) is ignored
    if not ignored:
        assert (
            _git(upstream_repo, "check-ignore", "unrelated-future-file") == "unrelated-future-file"
        )
    if link is not None:
        assert link.readlink() == rules
    with pytest.raises(LoadoutError, match="privacy"):
        if phase == "prepare":
            prepare_migration(plan)
        elif phase == "apply":
            assert prepared is not None
            apply_migration(prepared)
        else:
            assert journal is not None
            resume_migration(journal)
    assert migration_git.identity(tmp_path) == head
    assert migration_git.index_bytes(tmp_path / ".git/index") == index
    if journal is not None:
        assert recover_migration(journal).conflicts == ()
    assert rules.read_text() == rule
    assert original.read_text() == "company advice\n"
    assert not (tmp_path / "loadout/templates/frontend/instructions.md").exists()


@pytest.mark.parametrize("phase", ("prepare", "apply", "checkpoint"))
@pytest.mark.parametrize("addition", ("file", "nested-file", "empty-directory"))
def test_starter_inventory_additions_invalidate_approval(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    phase: str,
    addition: str,
) -> None:
    _repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("Original body\n")
    (source,) = _source(fake_home, "company")
    (source / "skills").mkdir()
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",)), starter="frontend"
    )
    assert plan.complete, plan.issues
    prepared = prepare_migration(plan) if phase != "prepare" else None
    journal = None
    if phase == "checkpoint":
        assert prepared is not None
        journal = _interrupted_starter(prepared, phase, monkeypatch)
    if addition == "file":
        (source / "permissions.toml").write_text('[shell]\nallow = ["npm"]\n')
    elif addition == "nested-file":
        (source / "skills/SKILL.md").write_text("New skill\n")
    else:
        (source / "new-empty-category").mkdir()
    with pytest.raises(LoadoutError, match="changed"):
        if phase == "prepare":
            prepare_migration(plan)
        elif phase == "apply":
            assert prepared is not None
            apply_migration(prepared)
        else:
            assert journal is not None
            resume_migration(journal)
    if addition != "empty-directory":
        fresh = plan_migration(
            discover(tmp_path, scope="project", agents=("claude",)), starter="frontend"
        )
        assert "cannot compose" in fresh.issues[-1].message
    assert not (tmp_path / "loadout").exists()


def test_file_free_starter_records_its_root_but_remains_unresolved(
    tmp_path: Path, fake_home: Path
) -> None:
    (source,) = _source(fake_home, "company")
    (source / "instructions.md").unlink()
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",)), starter="frontend"
    )
    assert plan.issues[-1].code == "reconstruction-failed"
    assert any(s.path == source and s.kind == "directory" and s.digest for s in plan.preconditions)
    with pytest.raises(LoadoutError, match="fully resolved"):
        prepare_migration(plan)
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize(
    "case",
    [
        pytest.param((side, provenance, shape), id=f"{shape}-{provenance}-{side}")
        for shape in ("file", "directory")
        for provenance in ("clean", "modified", "missing")
        for side in ("vendored", "upstream")
    ],
)
def test_native_template_sync_rejects_symlinks_before_content_reads(
    tmp_path: Path,
    fake_home: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    case: tuple[str, str, str],
) -> None:
    side, provenance, shape = case
    (upstream,) = _source(fake_home, "company")
    plan = plan_migration(
        discover(tmp_path, scope="project", agents=("claude",)), starter="frontend"
    )
    assert plan.complete, plan.issues
    _materialize(plan, tmp_path)
    local = vendored_path(tmp_path, "frontend")
    config = project_config_path(tmp_path)
    if provenance == "modified":
        (local / "instructions.md").write_text("Local advice\n")
    elif provenance == "missing":
        config.write_text(config.read_text().split("[template.frontend]")[0])
    secret_dir = fake_home / "private-fixture"
    secret_dir.mkdir()
    secret = secret_dir / "instructions.md"
    secret.write_text("PRIVATE FIXTURE CONTENT MUST NOT APPEAR\n")
    tree = local if side == "vendored" else upstream
    if shape == "file":
        unsafe = tree / "instructions.md"
        unsafe.unlink()
        unsafe.symlink_to(secret)
    else:
        unsafe = tree / "private-link"
        unsafe.symlink_to(secret_dir, target_is_directory=True)
    before = config.read_bytes()
    real_open = Path.open
    reads = []

    def open_path(path: Path, *args, **kwargs):
        if path.resolve().is_relative_to(secret_dir):
            reads.append(path)
        return real_open(path, *args, **kwargs)

    capsys.readouterr()
    with monkeypatch.context() as patch:
        patch.setattr(Path, "open", open_path)
        status = main(["template", "sync", "frontend", "--root", str(tmp_path)])
    output = capsys.readouterr()
    assert reads == [], "sync read a private symlink target before refusing it"
    assert "PRIVATE FIXTURE CONTENT MUST NOT APPEAR" not in output.out + output.err
    assert status == 3
    assert "symlink" in output.err
    assert config.read_bytes() == before
    assert unsafe.is_symlink()


@pytest.mark.parametrize("starter", STARTERS)
def test_cli_starter_is_staged_reconstructs_and_allows_first_source_edits(
    tmp_path: Path, starter: str, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path)
    (tmp_path / "CLAUDE.md").write_text("Original project advice\n")
    assert main([*_init(tmp_path, starter), "--dry-run", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["starter"] == starter
    assert f"loadout/templates/{starter}/instructions.md" in preview["stage_paths"]
    assert main([*_init(tmp_path, starter), "--yes"]) == 0
    assert main(["check", "--root", str(tmp_path)]) == 0
    config = load_project_config(project_config_path(tmp_path))
    assert config.vendored_hash(starter) == tree_hash(vendored_path(tmp_path, starter))
    staged = _git(tmp_path, "diff", "--cached", "--name-only").splitlines()
    assert f"loadout/templates/{starter}/instructions.md" in staged
    assert _git(tmp_path, "show", "HEAD:CLAUDE.md") == "Original project advice"
    assert config.artifacts is not None
    for category, value in (
        ("permissions", {"permissions": {"allow": ["Read"]}}),
        ("settings", {"model": "chosen"}),
        ("hooks", {"hooks": {}}),
    ):
        part = next(p for r in config.artifacts.records for p in r.parts if p.category == category)
        (config.artifacts.source_root / part.source).write_text(json.dumps(value))
    instruction = next(r.parts[0] for r in config.artifacts.records if r.template_instructions)
    (config.artifacts.source_root / instruction.source).write_text("Updated project advice\n")
    assert main(["sync", "--root", str(tmp_path)]) == 0
    assert main(["check", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "CLAUDE.md").read_text().endswith("Updated project advice\n")
    assert json.loads((tmp_path / ".claude/settings.json").read_text()) == {
        "permissions": {"allow": ["Read"]},
        "model": "chosen",
        "hooks": {},
    }
    clone = tmp_path / "clone"
    shutil.copytree(
        tmp_path / "loadout", clone / "loadout", ignore=shutil.ignore_patterns(".loadout-state")
    )
    assert main(["sync", "--root", str(clone)]) == 0
    assert (clone / "CLAUDE.md").read_bytes() == (tmp_path / "CLAUDE.md").read_bytes()


def test_repeat_init_reports_usable_template_command_and_safe_sync(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path)
    assert main([*_init(tmp_path), "--yes"]) == 0
    config = project_config_path(tmp_path)
    before = config.read_bytes()
    capsys.readouterr()
    assert main([*_init(tmp_path, "frontend"), "--yes", "--json"]) == 2
    preview = json.loads(capsys.readouterr().out)
    assert "loadout template vendor frontend" in preview["issues"][0]["message"]
    assert config.read_bytes() == before
    assert main(["template", "vendor", "frontend", "--root", str(tmp_path)]) == 0
    assert main(["sync", "--root", str(tmp_path)]) == 0
    assert main(["template", "sync", "frontend", "--root", str(tmp_path)]) == 0
    instructions = vendored_path(tmp_path, "frontend") / "instructions.md"
    instructions.write_text("Local template edits\n")
    assert main(["sync", "--root", str(tmp_path)]) == 0
    assert main(["template", "sync", "frontend", "--root", str(tmp_path)]) == 1
    assert instructions.read_text() == "Local template edits\n"
    assert (tmp_path / "CLAUDE.md").read_text() == "Local template edits\n\n"


def test_native_template_sync_refuses_unsupported_upstream_without_changes(
    tmp_path: Path, fake_home: Path
) -> None:
    _repo(tmp_path)
    (upstream,) = _source(fake_home, "company")
    assert main([*_init(tmp_path, "frontend"), "--yes"]) == 0
    (upstream / "instructions.md").write_text("Updated company advice\n")
    assert main(["template", "sync", "frontend", "--root", str(tmp_path)]) == 0
    assert main(["sync", "--root", str(tmp_path)]) == 0
    assert (tmp_path / "CLAUDE.md").read_text() == "Updated company advice\n\n"
    config = project_config_path(tmp_path)
    before = config.read_bytes()
    (upstream / "permissions.toml").write_text('[shell]\nallow = ["unsafe *"]\n')
    assert main(["template", "sync", "frontend", "--root", str(tmp_path)]) == 3
    assert config.read_bytes() == before
    local = vendored_path(tmp_path, "frontend")
    assert load_project_config(config).vendored_hash("frontend") == tree_hash(local)


@pytest.mark.parametrize("starter", STARTERS)
def test_global_activation_and_unresolved_project_do_not_mutate(
    tmp_path: Path, starter: str, capsys: pytest.CaptureFixture[str]
) -> None:
    assert (
        main(
            [
                "init",
                "--global",
                "--harness",
                "claude",
                "--root",
                str(tmp_path),
                "--starter",
                starter,
                "--yes",
                "--json",
            ]
        )
        == 2
    )
    preview = json.loads(capsys.readouterr().out)
    assert preview["issues"][-1]["code"] == "starter-scope"
    assert list(tmp_path.iterdir()) == []
    assert main(["init", "--root", str(tmp_path), "--starter", starter, "--yes", "--json"]) == 2
    preview = json.loads(capsys.readouterr().out)
    assert preview["issues"] and not preview["complete"]
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("answer", ("", "frontend", "backend"))
def test_interactive_starter_then_cancel_never_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, answer: str
) -> None:
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    replies = iter((answer, "n"))
    monkeypatch.setattr("builtins.input", lambda prompt: next(replies))
    assert main(_init(tmp_path)) == 0
    assert list(tmp_path.iterdir()) == []


def test_starter_source_and_output_participate_in_resume_and_recovery(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _repo(tmp_path)
    original = tmp_path / "CLAUDE.md"
    original.write_text("Original body\n")
    original.chmod(0o640)

    def fail(*args, **kwargs):
        raise LoadoutError("checkpoint interrupted")

    with monkeypatch.context() as patch:
        patch.setattr(migration_git, "checkpoint", fail)
        assert main([*_init(tmp_path, "frontend"), "--yes", "--json"]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "interrupted"
    assert main(["init", "--resume", result["journal"], "--yes", "--json"]) == 0
    assert original.read_text().startswith("# Frontend work\n")
    assert stat.S_IMODE(original.stat().st_mode) == 0o640
    assert main(["init", "--recover", result["journal"], "--yes", "--json"]) == 0
    assert original.read_bytes() == b"Original body\n"
    assert stat.S_IMODE(original.stat().st_mode) == 0o640
    assert not (vendored_path(tmp_path, "frontend") / "instructions.md").exists()
