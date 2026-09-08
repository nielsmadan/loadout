from __future__ import annotations

import base64
import hashlib
import os
import stat
import uuid
from concurrent.futures import Executor, ThreadPoolExecutor
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import migration_git
from .artifacts import FrozenFile
from .deployment import (
    DeploymentEntry,
    DeploymentPlan,
    DeploymentScope,
    FileChange,
    Receipt,
    ScopePlan,
    _receipt_bytes,
    _validate_state,
    apply_deployment,
    atomic_install,
)
from .discovery import entry_state, path_preconditions
from .errors import LoadoutError
from .git_hooks import HookPlan, hooks_directory, local_directory
from .migration_journal import Image, Journal, Operation, protected, snapshot
from .migration_models import EntryState, MigrationPlan, SourceWrite
from .migration_paths import DestinationLayout, entry_path
from .migration_validation import validate_plan
from .native_documents import apply_document, key_fingerprints, parse_document


class MigrationFailure(LoadoutError):
    def __init__(self, message: str, journal: Path) -> None:
        self.journal = journal
        super().__init__(f"{message}; resume or recover migration journal {journal}")


@dataclass(frozen=True)
class MigrationPreparation:
    plan: MigrationPlan = field(repr=False)
    git: migration_git.GitPreparation | None
    operations: tuple[Operation, ...] = field(repr=False)
    preconditions: tuple[EntryState, ...] = field(repr=False)
    directory_names: tuple[tuple[Path, tuple[str, ...]], ...] = field(repr=False)
    additions: tuple[str, ...] = ()
    removals: tuple[str, ...] = ()
    staged_ignore: tuple[str, bytes] | None = field(default=None, repr=False)
    deployment: DeploymentPlan | None = field(default=None, repr=False)
    hooks: HookPlan | None = None

    def preview(self) -> dict[str, Any]:
        return {
            **self.plan.preview(),
            "git": self.git.preview() if self.git else None,
            "git_hooks": self.hooks.preview() if self.hooks else None,
            "stage_paths": list(self.additions),
            "unstage_paths": list(self.removals),
            "operations": [
                {"path": str(o.path), "phase": o.phase, "action": o.after.kind}
                for o in self.operations
            ],
            "recovery_directory": str(self.plan.inventory.root / ".loadout-state/migrations"),
        }


@dataclass(frozen=True)
class MigrationResult:
    journal: Path | None
    baseline: str | None
    written: tuple[Path, ...]
    staged: tuple[str, ...]
    already_initialized: bool = False


@dataclass(frozen=True)
class RecoveryResult:
    journal: Path
    conflicts: tuple[Path, ...]
    baseline: str | None


class _Builder:
    def __init__(self) -> None:
        self.operations: list[Operation] = []
        self.images: dict[Path, Image] = {}

    def image(self, path: Path) -> Image:
        if path in self.images:
            return self.images[path]
        if any(
            parent in self.images and self.images[parent].kind in {"absent", "directory"}
            for parent in path.parents
        ):
            return Image()
        return snapshot(path)

    def put(self, path: Path, image: Image, phase: str) -> None:
        before = self.image(path)
        if before == image:
            return
        self.operations.append(Operation(path, before, image, phase))
        self.images[path] = image

    def parents(self, path: Path, phase: str, *, private: tuple[Path, ...] = ()) -> None:
        for parent in reversed(path.parents):
            image = self.image(parent)
            mode = 0o700 if any(parent.is_relative_to(p) for p in private) else 0o755
            if image.kind == "absent":
                self.put(parent, Image("directory", mode=mode), phase)
            elif image.kind != "directory":
                raise LoadoutError(f"migration parent must be a directory: {parent}")
            elif any(parent.is_relative_to(p) for p in private) and image.mode & 0o077:
                raise LoadoutError(f"private source directory is publicly accessible: {parent}")


def _verify(states: tuple[EntryState, ...]) -> None:
    for before in states:
        if entry_state(before.path, read=before.digest is not None) != before:
            raise LoadoutError(
                f"migration input or destination changed after preview: {before.path}"
            )


def _layout(plan: MigrationPlan) -> DestinationLayout:
    home = dict(plan.inventory.destination_environment).get("HOME")
    return DestinationLayout(
        plan.inventory.root, plan.inventory.mappings, Path(home) if home else None
    )


def _links(plan: MigrationPlan, outputs: tuple[Path, ...]) -> tuple[Path, ...]:
    boundaries = (plan.inventory.root.resolve(), plan.inventory.source_root.resolve())
    links: set[Path] = set()
    for path in outputs:
        for ancestor in reversed((path, *path.parents)):
            if not ancestor.is_symlink():
                continue
            if any(root.is_relative_to(ancestor) for root in boundaries):
                raise LoadoutError(f"migration cannot replace a source boundary: {ancestor}")
            if not any(
                entry_path(state.path) == ancestor and state.kind == "symlink"
                for state in plan.preconditions
            ):
                raise LoadoutError(f"uninventoried destination symlink: {ancestor}")
            links.add(ancestor)
            break
    return tuple(sorted(links))


def _mirror(
    builder: _Builder,
    path: Path,
    outputs: tuple[Path, ...],
    plan: MigrationPlan,
) -> None:
    originals = {entry_path(o.path): o for o in plan.originals}
    retired = tuple(entry_path(p) for p in plan.obsolete)
    for child in sorted(path.iterdir()):
        adopted = tuple(p for p in outputs if p == child or p.is_relative_to(child))
        if adopted:
            if child in adopted:
                continue
            if not child.is_dir():
                raise LoadoutError(f"destination topology conflicts with a file: {child}")
            builder.put(child, Image("directory", mode=0o755), "topology")
            _mirror(builder, child, outputs, plan)
            continue
        original = originals.get(child) or originals.get(entry_path(child))
        if original is None:
            if child.is_dir() and not child.is_symlink():
                builder.put(child, Image("directory", mode=0o755), "topology")
                _mirror(builder, child, outputs, plan)
                continue
            raise LoadoutError(f"uninventoried entry under a destination symlink: {child}")
        if original.action == "retain":
            target = entry_path(child)
            if any(target.is_relative_to(p) for p in retired):
                raise LoadoutError(f"retained runtime entry depends on a retired source: {child}")
            builder.put(child, Image("symlink", os.fsencode(target), 0o777), "topology")


def _topology(builder: _Builder, plan: MigrationPlan, outputs: tuple[Path, ...]) -> None:
    for link in _links(plan, outputs):
        directory = link.is_dir()
        builder.put(link, Image(), "topology")
        if directory:
            builder.put(link, Image("directory", mode=0o755), "topology")
            _mirror(builder, link, outputs, plan)


def _frozen_outputs(plan: MigrationPlan) -> dict[Path, FrozenFile]:
    layout = _layout(plan)
    result: dict[Path, FrozenFile] = {}
    for output in plan.generated_writes:
        path = layout.normalize(output.path)
        content, mode = output.content, output.mode
        if output.owned is not None:
            actual = snapshot(output.path.resolve())
            if actual.kind not in {"file", "absent"}:
                raise LoadoutError(f"partial destination must be a document: {path}")
            content = apply_document(
                actual.content.decode(), frozenset(output.owned), content.decode(), output.format
            ).encode()
            if not content and output.emit_empty:
                content = b"{}\n" if output.format == "json" else b""
            if actual.kind == "absent" and not content and not output.emit_empty:
                continue
            mode = actual.mode if actual.kind == "file" else 0o600
        result[path] = FrozenFile(content, mode)
    return result


def _deployment(
    builder: _Builder,
    plan: MigrationPlan,
    frozen: dict[Path, FrozenFile],
) -> DeploymentPlan:
    inventory = plan.inventory
    assert inventory.scope is not None
    scope = DeploymentScope(
        inventory.scope,
        inventory.root.resolve()
        if inventory.scope == "project"
        else inventory.source_root.resolve(),
        inventory.source_root.resolve(),
        None,
        (),
    )
    before_receipt = _validate_state(scope)
    if before_receipt is not None:
        raise LoadoutError(f"migration requires an unowned destination scope: {scope.receipt_path}")
    layout = _layout(plan)
    routes = tuple((route, layout.normalize(path)) for route, path in plan.artifact_routes)
    entries = []
    changes = []
    for output in plan.generated_writes:
        path = layout.normalize(output.path)
        if path not in frozen:
            continue
        builder.parents(path, "topology")
        image = builder.image(path)
        if image.kind not in {"file", "absent"}:
            raise LoadoutError(f"destination is not installable: {path}")
        before = FrozenFile(image.content, image.mode) if image.kind == "file" else None
        after = frozen[path]
        changes.append(FileChange(path, before, after))
        route, base = max(
            (r for r in routes if path.is_relative_to(r[1])), key=lambda r: len(r[1].parts)
        )
        entries.append(
            DeploymentEntry(
                anchor=str(scope.anchor),
                route=route,
                root=str(scope.anchor if scope.name == "project" else base),
                relative=path.relative_to(base).as_posix(),
                format=output.format if output.owned is not None else "whole",
                mode=after.mode,
                digest=""
                if output.owned is not None
                else hashlib.sha256(after.content).hexdigest(),
                owned=tuple(sorted(output.owned or ())),
                values=key_fingerprints(
                    after.content.decode(), output.format, frozenset(output.owned)
                )
                if output.owned is not None
                else (),
            )
        )
    return DeploymentPlan(
        (ScopePlan(scope, before_receipt, Receipt(), tuple(entries), tuple(changes)),),
        frozenset(frozen),
        (),
        (),
        (),
    )


def _deployment_operations(builder: _Builder, deployment: DeploymentPlan) -> None:
    scope = deployment.scopes[0]
    directory = scope.scope.receipt_path.parent
    builder.parents(directory / ".gitignore", "source", private=(directory,))
    builder.put(directory / ".gitignore", Image("file", b"*\n", 0o600), "deploy")
    pending = _receipt_bytes(scope.scope, Receipt((), scope.entries))
    builder.put(scope.scope.receipt_path, Image("file", pending.content, pending.mode), "deploy")
    for change in scope.changes:
        assert change.after is not None
        builder.put(change.path, Image("file", change.after.content, change.after.mode), "deploy")
    receipt = _receipt_bytes(scope.scope, Receipt(scope.entries))
    builder.put(scope.scope.receipt_path, Image("file", receipt.content, receipt.mode), "deploy")


def _ignore_lines(plan: MigrationPlan) -> tuple[str, ...]:
    patterns = ["/.loadout-state/", *plan.ignores]
    result = []
    for value in patterns:
        if any(character in value for character in "\n\r\0"):
            raise LoadoutError(
                "migration paths containing newlines cannot be represented in .gitignore"
            )
        escaped = "".join("\\" + c if c in "\\*?[] " else c for c in value)
        if escaped not in result:
            result.append(escaped)
    return tuple(result)


def _append_ignores(content: bytes, patterns: tuple[str, ...]) -> bytes:
    text = content.decode()
    missing = [p for p in patterns if p not in text.splitlines()]
    if not missing:
        return content
    return (
        text + ("\n" if text and not text.endswith("\n") else "") + "\n".join(missing) + "\n"
    ).encode()


def _staging(
    plan: MigrationPlan, prepared: migration_git.GitPreparation, patterns: tuple[str, ...]
) -> tuple[tuple[str, ...], tuple[str, ...], tuple[str, bytes]]:
    root = prepared.root
    additions = tuple(
        sorted(
            value
            for write in plan.source_writes
            if not write.private
            and not migration_git.excluded(write.path, plan.private_paths)
            and (value := migration_git.relative(root, write.path)) is not None
        )
    )
    paths = (*plan.obsolete, *(w.path for w in plan.generated_writes), *plan.private_paths)
    removals = {
        value
        for path in paths
        if (value := migration_git.relative(root, migration_git._entry(root, path))) is not None
    }
    if prepared.existing:
        for path in plan.private_paths:
            value = migration_git.relative(root, path)
            if value is not None:
                removals.update(
                    os.fsdecode(p)
                    for p in migration_git.git(
                        root, "ls-files", "-z", "--", migration_git.literal(value)
                    ).stdout.split(b"\0")
                    if p
                )
    ignore = migration_git.relative(root, plan.inventory.root / ".gitignore")
    assert ignore is not None
    indexed = migration_git.indexed_content(root, ignore) if prepared.existing else None
    if ignore in prepared.baseline:
        indexed = (root / ignore).read_bytes()
    if prepared.existing:
        migration_git._partial_adopted(root, (*additions, *removals))
        if migration_git._ignored(root, additions):
            raise LoadoutError(
                "public migration source is ignored by existing Git policy; resolve source privacy first"
            )
    return additions, tuple(sorted(removals)), (ignore, _append_ignores(indexed or b"", patterns))


def _filesystem_plan(
    plan: MigrationPlan, machine_write: SourceWrite | None
) -> tuple[_Builder, DeploymentPlan | None]:
    builder = _Builder()
    if machine_write is not None:
        if not machine_write.path.is_absolute() or machine_write.path.is_symlink():
            raise LoadoutError(
                "planned machine registration requires an absolute regular destination"
            )
        if machine_write.path.is_relative_to(plan.inventory.source_root):
            raise LoadoutError("machine registration must not overlap authored source")
    frozen = _frozen_outputs(plan)
    _topology(builder, plan, tuple(frozen))
    layout = _layout(plan)
    private = tuple(layout.normalize(p) for p in plan.private_paths)
    for write in plan.source_writes:
        path = layout.normalize(write.path)
        builder.parents(path, "source", private=private)
        builder.put(path, Image("file", write.content, write.mode), "source")
    deployment = _deployment(builder, plan, frozen) if not plan.already_initialized else None
    if deployment is not None:
        _deployment_operations(builder, deployment)
    for path in plan.obsolete:
        target = entry_path(path)
        if target in frozen or target in builder.images:
            continue
        if not target.is_relative_to(plan.inventory.root.resolve()) or target.is_dir():
            raise LoadoutError(f"retirement must name an individual adopted source: {path}")
        builder.put(target, Image(), "retire")
    if machine_write is not None:
        builder.parents(machine_write.path, "registration")
        builder.put(
            machine_write.path,
            Image("file", machine_write.content, machine_write.mode),
            "registration",
        )
    return builder, deployment


def prepare_migration(
    plan: MigrationPlan, *, machine_write: SourceWrite | None = None, hooks: HookPlan | None = None
) -> MigrationPreparation:
    if not plan.complete:
        raise LoadoutError("migration requires a fully resolved, validated plan")
    _verify_environment(plan)
    _verify(plan.preconditions)
    if plan.already_initialized and machine_write is None and not hooks:
        return MigrationPreparation(plan, None, (), plan.preconditions, ())
    if not plan.already_initialized:
        validated = validate_plan(plan)
        if not validated.complete or validated.generated_writes != plan.generated_writes:
            raise LoadoutError("migration source reconstruction differs from the approved plan")
    builder, deployment = _filesystem_plan(plan, machine_write)
    git_prepared = None if plan.already_initialized else migration_git.prepare_git(plan)
    additions: tuple[str, ...] = ()
    removals: tuple[str, ...] = ()
    staged_ignore = None
    if git_prepared is not None:
        patterns = _ignore_lines(plan)
        ignore_path = plan.inventory.root.resolve() / ".gitignore"
        current = builder.image(ignore_path)
        if current.kind not in {"file", "absent"}:
            raise LoadoutError(f".gitignore must be a regular file: {ignore_path}")
        builder.put(
            ignore_path,
            Image(
                "file",
                _append_ignores(current.content, patterns),
                current.mode if current.kind == "file" else 0o644,
            ),
            "ignore",
        )
        additions, removals, staged_ignore = _staging(plan, git_prepared, patterns)
    if hooks is not None:
        for hook in hooks.hooks:
            if hook.status == "install":
                builder.put(hook.path, Image("file", hook.content, 0o755), "git-hook")
    states = _operation_preconditions(plan, git_prepared, builder.operations)
    protected(plan.inventory.root.resolve() / ".loadout-state")
    ignored = plan.inventory.root.resolve() / ".loadout-state/.gitignore"
    if ignored.exists() and snapshot(ignored) != Image("file", b"*\n", 0o600):
        raise LoadoutError(
            f"private migration ignore file must contain '*' with mode 0600: {ignored}"
        )
    names = tuple(
        (state.path, tuple(sorted(os.listdir(state.path))))
        for state in states.values()
        if state.kind == "directory" and state.digest is not None
    )
    return MigrationPreparation(
        plan,
        git_prepared,
        tuple(builder.operations),
        tuple(states.values()),
        names,
        additions,
        removals,
        staged_ignore,
        deployment,
        hooks,
    )


def _operation_preconditions(
    plan: MigrationPlan, prepared: migration_git.GitPreparation | None, operations: list[Operation]
) -> dict[Path, EntryState]:
    states = {state.path: state for state in plan.preconditions}
    extra = [operation.path for operation in operations]
    extra += [plan.inventory.root.resolve() / ".loadout-state"]
    if prepared:
        extra += [prepared.root / path for path in prepared.baseline]
    for path in extra:
        for state in path_preconditions(path):
            if (
                prepared is not None
                and not prepared.existing
                and state.kind == "absent"
                and state.path in {prepared.root / ".git", prepared.root / ".git/hooks"}
            ):
                continue
            states.setdefault(state.path, state)
    return states


def _bytes(value: bytes | None) -> str | None:
    return base64.b64encode(value).decode() if value is not None else None


def _decode(value: str | None) -> bytes | None:
    return base64.b64decode(value) if value is not None else None


def _git_document(prepared: migration_git.GitPreparation | None) -> dict[str, Any] | None:
    if prepared is None:
        return None
    return {
        **asdict(prepared),
        "root": str(prepared.root),
        "index_path": str(prepared.index_path) if prepared.index_path else None,
        "original_index": _bytes(prepared.original_index),
        "private": [str(p) for p in prepared.private],
        "authored_privacy": [[str(p), ignored] for p, ignored in prepared.authored_privacy],
        "policy_paths": [str(p) for p in prepared.policy_paths],
    }


def _git_parse(value: dict[str, Any]) -> migration_git.GitPreparation:
    return migration_git.GitPreparation(
        Path(value["root"]),
        value["existing"],
        value["head"],
        value["symbolic"],
        Path(value["index_path"]) if value["index_path"] else None,
        _decode(value["original_index"]),
        tuple(value["baseline"]),
        tuple(value["privacy_paths"]),
        tuple(value["ignored"]),
        tuple(Path(p) for p in value["private"]),
        tuple((Path(p), ignored) for p, ignored in value["authored_privacy"]),
        tuple(Path(p) for p in value["policy_paths"]),
        tuple((key, expected) for key, expected in value["privacy_policy"]),
    )


def _create_journal(prepared: MigrationPreparation) -> Journal:
    root = prepared.plan.inventory.root.resolve()
    state = root / ".loadout-state"
    for path in (state, state / "migrations"):
        protected(path)
        path.mkdir(mode=0o700, exist_ok=True)
    ignored = state / ".gitignore"
    if ignored.exists() and snapshot(ignored) != Image("file", b"*\n", 0o600):
        raise LoadoutError(
            f"private migration ignore file must contain '*' with mode 0600: {ignored}"
        )
    atomic_install(ignored, FrozenFile(b"*\n", 0o600))
    directory = state / "migrations" / uuid.uuid4().hex
    directory.mkdir(mode=0o700)
    journal = Journal(
        directory / "journal.json",
        prepared.operations,
        {
            "root": str(root),
            "git": _git_document(prepared.git),
            "git_hooks": prepared.hooks.preview() if prepared.hooks else None,
            "privacy_policy": prepared.git.privacy_policy if prepared.git else (),
            "baseline": prepared.git.head if prepared.git else None,
            "expected_index": _bytes(prepared.git.original_index) if prepared.git else None,
            "additions": prepared.additions,
            "removals": prepared.removals,
            "ignore": (prepared.staged_ignore[0], _bytes(prepared.staged_ignore[1]))
            if prepared.staged_ignore
            else None,
            "absences": [
                str(_layout(prepared.plan).normalize(p)) for p in prepared.plan.required_absences
            ],
            "owned_absences": [
                {
                    "path": str(_layout(prepared.plan).normalize(a.path)),
                    "format": a.format,
                    "keys": a.keys,
                }
                for a in prepared.plan.required_owned_absences
            ],
            "retirement_outputs": _retirement_outputs(prepared.plan),
            "outputs": {
                str(change.path): Image("file", change.after.content, change.after.mode).document()
                for scope in prepared.deployment.scopes
                for change in scope.changes
                if change.after is not None
            }
            if prepared.deployment is not None
            else {},
            "already_initialized": prepared.plan.already_initialized,
            "source_root": str(prepared.plan.inventory.source_root.resolve()),
            "destinations": [
                str(_layout(prepared.plan).normalize(p)) for _, p in prepared.plan.artifact_routes
            ],
            "registration": [
                str(o.path)
                for o in prepared.operations
                if o.phase == "registration" and o.after.kind == "file"
            ],
            "parents": {
                str(entry_path(s.path)): [s.device, s.inode]
                for s in prepared.preconditions
                if s.kind == "directory"
            },
            "preconditions": [{**asdict(s), "path": str(s.path)} for s in prepared.preconditions],
            "directory_names": [[str(p), list(names)] for p, names in prepared.directory_names],
        },
    )
    journal.save()
    return journal


def _checkpoint(journal: Journal) -> None:
    _guard_privacy(journal)
    _verify_checkpoint_inputs(
        tuple(
            EntryState(**{**s, "path": Path(s["path"])}) for s in journal.metadata["preconditions"]
        ),
        {Path(p): tuple(names) for p, names in journal.metadata["directory_names"]},
        Path(journal.metadata["root"]),
    )
    raw = journal.metadata["git"]
    if raw is not None:
        prepared = _git_parse(raw)
        _verify_original_privacy(prepared)

        def committed(head: str, index: Path) -> None:
            journal.metadata.update(
                baseline=head,
                index_path=str(index),
                symbolic=migration_git.identity(prepared.root)[1],
            )
            journal.status = "checkpoint-committed"
            journal.save()

        def started(head: str | None, symbolic: str | None, index: Path) -> None:
            expected = journal.metadata.get("initialized_ref")
            if expected is not None and expected != [head, symbolic]:
                raise LoadoutError("Git identity changed after repository initialization")
            if expected is None and head != prepared.head:
                raise LoadoutError("Git HEAD changed before checkpoint")
            if expected is None and not prepared.existing:
                _verify_original_privacy(prepared)
                if migration_git._ignored(prepared.root, prepared.baseline):
                    raise LoadoutError("Git checkpoint privacy changed during initialization")
                journal.metadata["privacy_policy"] = migration_git.privacy_policy(
                    prepared.root, prepared.policy_paths
                )
            journal.metadata.update(initialized_ref=[head, symbolic], index_path=str(index))
            journal.save()

        baseline, refreshed, index = migration_git.checkpoint(
            prepared, journal.path.parent, committed, started
        )
        journal.metadata.update(
            baseline=baseline,
            refreshed_index=_bytes(refreshed),
            index_path=str(index),
            symbolic=migration_git.identity(prepared.root)[1],
        )
        journal.status = "checkpoint-index"
        journal.save()
        if refreshed is not None and refreshed != prepared.original_index:
            migration_git.replace_index(index, prepared.original_index, refreshed)
        journal.metadata["expected_index"] = _bytes(refreshed)
    journal.status = "apply"
    journal.save()


def _verify_after_checkpoint(prepared: MigrationPreparation) -> None:
    _verify_checkpoint_inputs(
        prepared.preconditions,
        dict(prepared.directory_names),
        prepared.plan.inventory.root.resolve(),
    )


def _verify_checkpoint_inputs(
    states: tuple[EntryState, ...], names: dict[Path, tuple[str, ...]], root: Path
) -> None:
    for state in states:
        if state.path == root / ".loadout-state":
            continue
        if state.path in names:
            current = set(os.listdir(state.path))
            allowed = {
                p.name
                for p in (root / ".loadout-state", root / ".git")
                if p.parent == state.path.resolve() and p.name not in names[state.path]
            }
            if current - allowed != set(names[state.path]):
                raise LoadoutError(f"migration directory changed during checkpoint: {state.path}")
            current_state = entry_state(state.path, read=False)
            if (current_state.mode, current_state.device, current_state.inode) != (
                state.mode,
                state.device,
                state.inode,
            ):
                raise LoadoutError(f"migration parent changed during checkpoint: {state.path}")
        elif entry_state(state.path, read=state.digest is not None) != state:
            raise LoadoutError(f"migration input changed during checkpoint: {state.path}")


def _deploy(journal: Journal, deployment: DeploymentPlan) -> None:
    def write(path: Path, frozen: FrozenFile | None) -> None:
        wanted = Image("file", frozen.content, frozen.mode) if frozen is not None else Image()
        if snapshot(path) == wanted:
            return
        if journal.next >= len(journal.operations):
            raise LoadoutError("deployment escaped the migration journal")
        operation = journal.operations[journal.next]
        if operation.path != path or operation.after != wanted or operation.phase != "deploy":
            raise LoadoutError(f"deployment differs from the frozen migration: {path}")
        journal.step()

    apply_deployment(deployment, write=write)


def _guard_git(journal: Journal, executor: Executor | None = None) -> None:
    if executor is None:
        _guard_privacy(journal)
        _guard_git_state(journal)
    else:
        privacy = executor.submit(_guard_privacy, journal, executor)
        try:
            _guard_git_state(journal)
        finally:
            privacy.result()
    if journal.metadata["git"] is not None and migration_git.index_bytes(
        Path(journal.metadata["index_path"])
    ) != _decode(journal.metadata["expected_index"]):
        raise LoadoutError("Git index changed during migration")


def _guard_git_state(journal: Journal) -> None:
    _guard_hooks(journal)
    raw = journal.metadata["git"]
    if raw is None:
        return
    root = Path(raw["root"])
    expected = (journal.metadata["baseline"], journal.metadata["symbolic"])
    if migration_git.identity(root) != expected:
        raise LoadoutError("Git HEAD or symbolic ref changed during migration")


def _verify_original_privacy(prepared: migration_git.GitPreparation) -> None:
    migration_git.verify_authored_privacy(prepared)
    if (
        prepared.existing
        and migration_git._ignored(prepared.root, prepared.privacy_paths) != prepared.ignored
    ):
        raise LoadoutError("Git-derived source privacy changed after migration preview")


def _guard_privacy(journal: Journal, executor: Executor | None = None) -> None:
    raw = journal.metadata["git"]
    if raw is None:
        return
    prepared = _git_parse(raw)
    expected = dict(journal.metadata["privacy_policy"])
    for operation in journal.operations[: journal.next + int(journal.pending)]:
        key = "file:" + str(operation.path)
        if key in expected and snapshot(operation.path) == operation.after:
            expected[key] = migration_git.privacy_fingerprint(operation.path)
    if (
        dict(migration_git.privacy_policy(prepared.root, prepared.policy_paths, executor=executor))
        != expected
    ):
        raise LoadoutError(
            "Git privacy policy changed during migration; rediscover before publishing"
        )
    if migration_git.git(
        prepared.root, "rev-parse", "--show-toplevel", check=False
    ).returncode == 0 and migration_git._ignored(
        prepared.root, tuple(journal.metadata["additions"])
    ):
        raise LoadoutError("public migration source privacy changed during migration")


def _guard_completed(journal: Journal) -> None:
    completed = {
        operation.path: operation.after for operation in journal.operations[: journal.next]
    }
    remaining = {operation.path for operation in journal.operations[journal.next :]}
    completed.update(
        (Path(path), Image.parse(image))
        for path, image in journal.metadata["outputs"].items()
        if Path(path) not in remaining
    )
    for path, expected in completed.items():
        if snapshot(path) != expected:
            raise LoadoutError(f"completed migration entry changed: {path}")
    for raw, expected_identity in journal.metadata["parents"].items():
        path = Path(raw)
        if path in completed:
            continue
        state = entry_state(path, read=False)
        if state.kind != "directory" or [state.device, state.inode] != expected_identity:
            raise LoadoutError(f"migration parent changed: {path}")


def _guard_outputs(journal: Journal) -> None:
    for raw, expected in journal.metadata["outputs"].items():
        if snapshot(Path(raw)) != Image.parse(expected):
            raise LoadoutError(f"migration output changed: {raw}")
    for raw in journal.metadata["absences"]:
        if snapshot(Path(raw)).kind != "absent":
            raise LoadoutError(f"dormant output unexpectedly exists: {raw}")
    for absence in journal.metadata.get("owned_absences", ()):
        current = snapshot(Path(absence["path"]))
        if current.kind == "absent":
            continue
        if (
            current.kind != "file"
            or set(absence["keys"])
            & parse_document(current.content.decode(), absence["format"]).keys()
        ):
            raise LoadoutError(f"dormant owned fields unexpectedly exist: {absence['path']}")


def _finish(journal: Journal) -> MigrationResult:
    _guard_git(journal)
    _guard_completed(journal)
    _guard_outputs(journal)
    raw = journal.metadata["git"]
    if raw is not None:
        root = Path(raw["root"])
        ignore = journal.metadata["ignore"]
        final = migration_git.staged_index(
            root,
            journal.path.parent,
            _decode(journal.metadata["expected_index"]),
            migration_git.Staging(
                tuple(journal.metadata["additions"]),
                tuple(journal.metadata["removals"]),
                (ignore[0], _decode(ignore[1]) or b"") if ignore else None,
            ),
        )
        journal.metadata["final_index"] = _bytes(final)
        journal.status = "stage-index"
        journal.save()
        _guard_git(journal)
        _guard_completed(journal)
        _guard_outputs(journal)
        migration_git.replace_index(
            Path(journal.metadata["index_path"]), _decode(journal.metadata["expected_index"]), final
        )
        journal.metadata["expected_index"] = _bytes(final)
    _guard_completed(journal)
    _guard_outputs(journal)
    journal.status = "complete"
    journal.save()
    return _result(journal)


def _result(journal: Journal) -> MigrationResult:
    return MigrationResult(
        journal.path,
        journal.metadata["baseline"],
        tuple(dict.fromkeys(o.path for o in journal.operations if o.after.kind == "file")),
        tuple(journal.metadata["additions"]),
        journal.metadata["already_initialized"],
    )


def apply_migration(prepared: MigrationPreparation) -> MigrationResult:
    _verify_environment(prepared.plan)
    _verify(prepared.preconditions)
    if prepared.hooks is not None:
        existing = prepared.git is None or prepared.git.existing
        if hooks_directory(
            prepared.hooks.repository, existing=existing
        ) != prepared.hooks.directory or (
            any(hook.status == "install" for hook in prepared.hooks.hooks)
            and not local_directory(
                prepared.hooks.repository, prepared.hooks.directory, existing=existing
            )
        ):
            raise LoadoutError("effective Git hook directory changed after preview")
    if prepared.git is not None:
        migration_git.verify_git(prepared.git)
        if (
            not prepared.git.existing
            and migration_git.prepare_git(prepared.plan).baseline != prepared.git.baseline
        ):
            raise LoadoutError("new repository checkpoint paths changed after preview")
    if not prepared.operations and prepared.git is None:
        return MigrationResult(None, None, (), (), True)
    journal = _create_journal(prepared)
    try:
        _checkpoint(journal)
        _verify_after_checkpoint(prepared)
        _guard_git(journal)
        while journal.next < len(journal.operations) and journal.operations[
            journal.next
        ].phase not in {"deploy", "git-hook"}:
            journal.step()
        if prepared.deployment is not None:
            _guard_completed(journal)
            _deploy(journal, prepared.deployment)
        _run_remaining(journal)
        return _finish(journal)
    except (Exception, KeyboardInterrupt) as error:
        raise MigrationFailure(str(error), journal.path) from error


def resume_migration(path: Path) -> MigrationResult:
    journal = Journal.load(path)
    if journal.status == "complete":
        return _result(journal)
    if journal.status in {"recovering", "recovered", "recovery-conflicts"}:
        raise LoadoutError(f"a recovered migration must be planned again: {path}")
    try:
        _guard_privacy(journal)
        _resume_checkpoint(journal)
        if journal.status == "checkpoint":
            _checkpoint(journal)
        if journal.status == "checkpoint-index":
            raw = journal.metadata["git"]
            assert raw is not None
            index = Path(journal.metadata["index_path"])
            after = _decode(journal.metadata["refreshed_index"])
            if migration_git.index_bytes(index) != after and after is not None:
                migration_git.replace_index(index, _decode(raw["original_index"]), after)
            journal.metadata["expected_index"] = _bytes(after)
            journal.status = "apply"
            journal.save()
        if journal.status == "stage-index":
            final = _decode(journal.metadata["final_index"])
            index = Path(journal.metadata["index_path"])
            if migration_git.index_bytes(index) == final:
                journal.metadata["expected_index"] = _bytes(final)
                _guard_git(journal)
                _guard_completed(journal)
                _guard_outputs(journal)
                journal.status = "complete"
                journal.save()
                return _result(journal)
        _guard_git(journal)
        if not journal.pending:
            _guard_completed(journal)
        _run_remaining(journal)
        return _finish(journal)
    except (Exception, KeyboardInterrupt) as error:
        raise MigrationFailure(str(error), journal.path) from error


def recover_migration(path: Path) -> RecoveryResult:
    journal = Journal.load(path)
    if journal.status == "recovered":
        return RecoveryResult(path, (), journal.metadata["baseline"])
    conflicts: list[Path] = []
    try:
        _resume_checkpoint(journal)
    except LoadoutError:
        raw_git = journal.metadata["git"]
        if raw_git is not None:
            conflicts.append(
                Path(journal.metadata.get("index_path") or raw_git["index_path"] or raw_git["root"])
            )
            return RecoveryResult(path, tuple(conflicts), journal.metadata["baseline"])
    guard = _recovery_git_conflicts(journal)
    if guard:
        return RecoveryResult(path, guard, journal.metadata["baseline"])
    journal.status = "recovering"
    journal.save()
    raw = journal.metadata["git"]
    if raw is not None and journal.metadata.get("final_index") is not None:
        index = Path(journal.metadata["index_path"])
        current = migration_git.index_bytes(index)
        baseline = _decode(journal.metadata.get("refreshed_index"))
        if migration_git.identity(Path(raw["root"])) != (
            journal.metadata["baseline"],
            journal.metadata["symbolic"],
        ):
            conflicts.append(index)
        elif current == _decode(journal.metadata["final_index"]):
            migration_git.replace_index(index, current, baseline)
        elif current != baseline:
            conflicts.append(index)
    conflicts.extend(journal.recover())
    return RecoveryResult(path, tuple(conflicts), journal.metadata["baseline"])


def _recovery_git_conflicts(journal: Journal) -> tuple[Path, ...]:
    raw = journal.metadata["git"]
    if raw is None:
        return ()
    index_name = journal.metadata.get("index_path") or raw["index_path"]
    if index_name is None:
        return ()
    index = Path(index_name)
    expected_ref = (
        [journal.metadata["baseline"], journal.metadata["symbolic"]]
        if "symbolic" in journal.metadata
        else journal.metadata.get("initialized_ref", [raw["head"], raw["symbolic"]])
    )
    if migration_git.identity(Path(raw["root"])) != tuple(expected_ref):
        return (index,)
    accepted = {_decode(journal.metadata["expected_index"])}
    if "final_index" in journal.metadata:
        accepted.add(_decode(journal.metadata["final_index"]))
        if journal.status in {"recovering", "recovery-conflicts"}:
            accepted.add(_decode(journal.metadata.get("refreshed_index")))
    return () if migration_git.index_bytes(index) in accepted else (index,)


def _resume_checkpoint(journal: Journal) -> None:
    if journal.status not in {"checkpoint", "checkpoint-committed", "checkpoint-index"}:
        return
    raw = journal.metadata["git"]
    if raw is None:
        return
    prepared = _git_parse(raw)
    if (
        not prepared.existing
        and migration_git.git(prepared.root, "rev-parse", "--show-toplevel", check=False).returncode
        != 0
    ):
        return
    baseline = migration_git.committed_checkpoint(prepared, journal.path.parent)
    initial_ref = journal.metadata.get("initialized_ref")
    if initial_ref is not None and migration_git.identity(prepared.root)[1] != initial_ref[1]:
        raise LoadoutError("Git symbolic ref changed after repository initialization")
    if baseline is None:
        return
    index = Path(
        os.fsdecode(
            migration_git.git(
                prepared.root, "rev-parse", "--path-format=absolute", "--git-path", "index"
            ).stdout
        ).strip()
    )
    journal.metadata.update(
        baseline=baseline, index_path=str(index), symbolic=migration_git.identity(prepared.root)[1]
    )
    journal.status = "checkpoint-committed"
    journal.save()
    refreshed = _decode(journal.metadata.get("refreshed_index"))
    if refreshed is None:
        refreshed = migration_git.refresh_index(prepared, journal.path.parent, baseline)
        journal.metadata["refreshed_index"] = _bytes(refreshed)
        journal.save()
    current = migration_git.index_bytes(index)
    if current != refreshed:
        migration_git.replace_index(index, prepared.original_index, refreshed)
    journal.metadata["expected_index"] = _bytes(refreshed)
    journal.status = "apply"
    journal.save()


def _retirement_outputs(plan: MigrationPlan) -> dict[str, list[str]]:
    layout = _layout(plan)
    replacements: dict[str, set[str]] = {}
    for original in plan.originals:
        if original.action == "retire" and original.destination is not None:
            replacements.setdefault(str(entry_path(original.path)), set()).add(
                str(layout.normalize(original.destination))
            )
    return {path: sorted(outputs) for path, outputs in replacements.items()}


def _entry_fingerprint(path: Path) -> tuple[int, ...] | None:
    try:
        entry = path.lstat()
    except FileNotFoundError:
        return None
    identity = (entry.st_dev, entry.st_ino, entry.st_mode)
    return (
        identity
        if stat.S_ISDIR(entry.st_mode)
        else (*identity, entry.st_size, entry.st_mtime_ns, entry.st_ctime_ns)
    )


class _RetirementGuard:
    def __init__(self, journal: Journal) -> None:
        self.journal = journal
        self.paths = {
            *(o.path for o in journal.operations[: journal.next]),
            *(Path(p) for p in journal.metadata["outputs"]),
            *(Path(p) for p in journal.metadata["parents"]),
            *(Path(p) for p in journal.metadata["absences"]),
            *(Path(a["path"]) for a in journal.metadata.get("owned_absences", ())),
        }
        self.refresh()

    def refresh(self) -> None:
        self.fingerprints = {path: _entry_fingerprint(path) for path in self.paths}

    def check(self, operation: Operation) -> None:
        journal = self.journal
        replacements = journal.metadata.get("retirement_outputs", {}).get(str(operation.path))
        if not replacements or any(p not in journal.metadata["outputs"] for p in replacements):
            _guard_completed(journal)
            _guard_outputs(journal)
        else:
            if any(
                _entry_fingerprint(path) != before for path, before in self.fingerprints.items()
            ):
                _guard_completed(journal)
                _guard_outputs(journal)
                self.refresh()
            for path in replacements:
                if snapshot(Path(path)) != Image.parse(journal.metadata["outputs"][path]):
                    raise LoadoutError(f"migration output changed: {path}")

    def completed(self, operation: Operation) -> None:
        if snapshot(operation.path) != operation.after:
            raise LoadoutError(f"completed migration entry changed: {operation.path}")
        self.paths.add(operation.path)
        self.fingerprints[operation.path] = _entry_fingerprint(operation.path)


def _run_remaining(journal: Journal) -> None:
    retirement_guard = None
    with ThreadPoolExecutor(max_workers=4) as executor:
        while journal.next < len(journal.operations):
            operation = journal.operations[journal.next]
            if operation.phase in {"retire", "git-hook"}:
                _guard_git(journal, executor)
                if retirement_guard is None or operation.phase == "git-hook":
                    _guard_completed(journal)
                    _guard_outputs(journal)
                    retirement_guard = _RetirementGuard(journal)
                retirement_guard.check(operation)
            if operation.phase == "git-hook":
                operation.path.parent.mkdir(parents=True, exist_ok=True)
            journal.step()
            if retirement_guard is not None and operation.phase == "retire":
                retirement_guard.completed(operation)


def _guard_hooks(journal: Journal) -> None:
    hooks = journal.metadata.get("git_hooks")
    if hooks is None or not any(h["status"] == "install" for h in hooks["hooks"]):
        return
    repository, directory = Path(hooks["repository"]), Path(hooks["directory"])
    if hooks_directory(repository) != directory or not local_directory(repository, directory):
        raise LoadoutError("effective Git hook directory changed; preserve hooks and rediscover")


def _verify_environment(plan: MigrationPlan) -> None:
    if plan.already_initialized:
        return
    captured = dict(plan.inventory.destination_environment)
    for key in (
        "HOME",
        "CLAUDE_CONFIG_DIR",
        "CODEX_HOME",
        "XDG_CONFIG_HOME",
        "PI_CODING_AGENT_DIR",
        "OPENCODE_CONFIG",
        "OPENCODE_CONFIG_DIR",
    ):
        if (os.environ.get(key) or None) != captured.get(key):
            raise LoadoutError(
                f"destination environment changed; rediscover before applying migration: {key}"
            )
