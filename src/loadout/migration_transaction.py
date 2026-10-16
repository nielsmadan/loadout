from __future__ import annotations

import os
import stat
from dataclasses import dataclass, field
from functools import cached_property
from pathlib import Path
from typing import Any

from . import migration_git
from .discovery import DESTINATION_ENVIRONMENT, entry_state
from .errors import LoadoutError
from .gitignore import escape, require_plain_gitignore, with_lines
from .migration_images import Image, Operation, Phase, install, snapshot
from .migration_models import EntryState, MigrationPlan, SourceWrite
from .migration_paths import DestinationLayout, inventory_layout
from .migration_validation import validate_plan
from .output import entry_path
from .surgery import apply_json, apply_toml


@dataclass(frozen=True)
class Delinked:
    """A deployment link the migration replaces with a real directory or file."""

    link: Path
    target: Path
    directory: bool
    superseded: tuple[Path, ...]
    written_into: tuple[str, ...] = ()

    def document(self) -> dict[str, Any]:
        return {
            "link": str(self.link),
            "target": str(self.target),
            "directory": self.directory,
            "superseded": [str(p) for p in self.superseded],
            "written_into": list(self.written_into),
        }


@dataclass(frozen=True)
class MigrationPreparation:
    plan: MigrationPlan = field(repr=False)
    operations: tuple[Operation, ...] = field(repr=False)
    preconditions: tuple[EntryState, ...] = field(repr=False)
    repository: Path | None = None
    untracked: tuple[str, ...] = ()
    new_repository: Path | None = None

    @cached_property
    def layout(self) -> DestinationLayout:
        return _layout(self.plan)

    def preview(self) -> dict[str, Any]:
        return {
            **self.plan.preview(),
            "untrack_paths": list(self.untracked),
            "source_repository": str(self.new_repository) if self.new_repository else None,
            "operations": [
                {"path": str(o.path), "phase": o.phase, "action": o.after.kind}
                for o in self.operations
            ],
            "delinked": [d.document() for d in self.delinked()],
        }

    def touched(self) -> tuple[Path, ...]:
        """Every path applying this can change, for the backup taken first."""
        index = (
            (migration_git.index_path(self.repository),)
            if self.repository is not None and self.untracked
            else ()
        )
        repository = (self.new_repository / ".git",) if self.new_repository else ()
        return tuple(dict.fromkeys((*(o.path for o in self.operations), *index, *repository)))

    def canonical(self, path: Path) -> Path:
        """The one spelling of `path` a backup records. Beneath a link init replaces it
        stays at the harness path; anywhere else its parent is resolved, so a link above
        a harness root (written through, ADR 0023) is never an ancestor of a backup path."""
        normalized = self.layout.normalize(path)
        replaced = {
            o.path for o in self.operations if o.phase == "topology" and o.before.kind == "symlink"
        }
        if normalized in replaced or replaced.intersection(normalized.parents):
            return normalized
        return entry_path(normalized)

    def delinked(self) -> tuple[Delinked, ...]:
        """Read before applying: afterwards the links are gone."""
        outputs = tuple(self.layout.normalize(w.path) for w in self.plan.generated_writes)
        result = []
        for operation in self.operations:
            if operation.phase != "topology" or operation.before.kind != "symlink":
                continue
            link = operation.path
            target = (link.parent / os.fsdecode(operation.before.content)).resolve()
            copies = (
                target / path.relative_to(link) for path in outputs if path.is_relative_to(link)
            )
            superseded = tuple(sorted(copy for copy in copies if copy.is_file()))
            nested = (path.relative_to(link).parts for path in outputs if path.is_relative_to(link))
            written_into = tuple(sorted({parts[0] for parts in nested if len(parts) > 1}))
            result.append(Delinked(link, target, target.is_dir(), superseded, written_into))
        return tuple(result)


@dataclass(frozen=True)
class MigrationResult:
    written: tuple[Path, ...]
    untracked: tuple[str, ...]
    already_initialized: bool = False
    # The generated files the first render wrote, filled in once it has run.
    rendered: tuple[Path, ...] = ()


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

    def put(self, path: Path, image: Image, phase: Phase) -> None:
        before = self.image(path)
        if before == image:
            return
        self.operations.append(Operation(path, before, image, phase))
        self.images[path] = image

    def parents(self, path: Path, phase: Phase, *, private: tuple[Path, ...] = ()) -> None:
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
    return inventory_layout(plan.inventory)


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
    """Rebuild a replaced link's directory: entries loadout does not write link back
    into the target, and each output it does starts as the file it was, so the first
    render keeps the keys loadout does not own exactly as the preview showed."""
    originals = {entry_path(o.path): o for o in plan.originals}
    retired = tuple(entry_path(p) for p in plan.obsolete)
    for child in sorted(path.iterdir()):
        adopted = tuple(p for p in outputs if p == child or p.is_relative_to(child))
        if adopted:
            if child in adopted:
                builder.put(child, _as_found(child), "topology")
                continue
            if not child.is_dir():
                raise LoadoutError(f"destination topology conflicts with a file: {child}")
            builder.put(child, Image("directory", mode=0o755), "topology")
            _mirror(builder, child, outputs, plan)
            continue
        original = originals.get(child) or originals.get(entry_path(child))
        if original is None or original.action == "retain":
            target = entry_path(child)
            if any(target.is_relative_to(p) for p in retired):
                raise LoadoutError(f"retained entry depends on a retired source: {child}")
            builder.put(child, Image("symlink", os.fsencode(target), 0o777), "topology")


def _as_found(path: Path) -> Image:
    resolved = path.resolve()
    return Image("file", resolved.read_bytes(), stat.S_IMODE(resolved.stat().st_mode))


def _topology(builder: _Builder, plan: MigrationPlan, outputs: tuple[Path, ...]) -> None:
    for link in _links(plan, outputs):
        directory = link.is_dir()
        builder.put(link, Image(), "topology")
        if directory:
            builder.put(link, Image("directory", mode=0o755), "topology")
            _mirror(builder, link, outputs, plan)
        elif link in outputs:
            # A linked output file starts as the file it was, as `_mirror` does for a
            # linked directory's children, so its keys loadout does not own survive.
            builder.put(link, _as_found(link), "topology")


def _rendered_paths(plan: MigrationPlan) -> tuple[Path, ...]:
    """Every output the first render will leave on disk."""
    layout = _layout(plan)
    result: list[Path] = []
    for output in plan.generated_writes:
        path = layout.normalize(output.path)
        if output.owned is not None:
            actual = snapshot(output.path.resolve())
            if actual.kind not in {"file", "absent"}:
                raise LoadoutError(f"partial destination must be a document: {path}")
            apply = apply_json if output.format == "json" else apply_toml
            if actual.kind == "absent" and not apply(
                "", frozenset(output.owned), output.content.decode()
            ):
                continue
        result.append(path)
    return tuple(result)


def _ignore_lines(plan: MigrationPlan) -> tuple[str, ...]:
    return tuple(dict.fromkeys(escape(value) for value in plan.ignores))


def _ignored_outputs(plan: MigrationPlan) -> tuple[Path, ...]:
    """Project outputs init gitignores: rendered from committed source from now on, so a
    tracked copy would hand every clone a stale file the first sync refuses over."""
    if plan.inventory.scope != "project":
        return ()
    return tuple(
        plan.inventory.root / pattern.strip("/")
        for pattern in plan.ignores
        if not pattern.endswith("/")
    )


def _filesystem_plan(plan: MigrationPlan, machine_write: SourceWrite | None) -> _Builder:
    builder = _Builder()
    if machine_write is not None:
        if not machine_write.path.is_absolute() or machine_write.path.is_symlink():
            raise LoadoutError(
                "planned machine registration requires an absolute regular destination"
            )
        if machine_write.path.is_relative_to(plan.inventory.source_root):
            raise LoadoutError("machine registration must not overlap authored source")
    rendered = _rendered_paths(plan)
    _topology(builder, plan, rendered)
    layout = _layout(plan)
    private = tuple(layout.normalize(p) for p in plan.private_paths)
    for write in plan.source_writes:
        path = layout.normalize(write.path)
        builder.parents(path, "source", private=private)
        builder.put(path, Image("file", write.content, write.mode), "source")
    # The first render writes these; checked here so a bad destination stops init first.
    for path in rendered if not plan.already_initialized else ():
        builder.parents(path, "topology")
        if builder.image(path).kind not in {"file", "absent"}:
            raise LoadoutError(f"destination is not installable: {path}")
    for path in plan.obsolete:
        target = entry_path(path)
        if target in rendered or target in builder.images:
            continue
        if not _retirable(plan, target) or target.is_dir():
            raise LoadoutError(f"retirement must name an individual adopted source: {path}")
        builder.put(target, Image(), "retire")
    if machine_write is not None:
        registration = entry_path(machine_write.path)
        builder.parents(registration, "registration")
        builder.put(
            registration,
            Image("file", machine_write.content, machine_write.mode),
            "registration",
        )
    return builder


def prepare_migration(
    plan: MigrationPlan,
    *,
    machine_write: SourceWrite | None = None,
    ignores: tuple[str, ...] = (),
) -> MigrationPreparation:
    """`ignores` are the lines the first render will add to `.gitignore`, written now so
    the render finds them there."""
    if not plan.complete:
        raise LoadoutError("migration requires a fully resolved, validated plan")
    _verify_environment(plan)
    _verify(plan.preconditions)
    if plan.already_initialized and machine_write is None:
        return MigrationPreparation(plan, (), plan.preconditions)
    if not plan.already_initialized:
        validated = validate_plan(plan)
        if not validated.complete or validated.generated_writes != plan.generated_writes:
            raise LoadoutError("migration source reconstruction differs from the approved plan")
    builder = _filesystem_plan(plan, machine_write)
    repository: Path | None = None
    untracked: tuple[str, ...] = ()
    new_repository: Path | None = None
    if not plan.already_initialized:
        require_plain_gitignore(plan.inventory.root.resolve())
        ignore_path = plan.inventory.root.resolve() / ".gitignore"
        current = builder.image(ignore_path)
        builder.put(
            ignore_path,
            Image(
                "file",
                with_lines(current.content.decode(), (*_ignore_lines(plan), *ignores)).encode(),
                current.mode if current.kind == "file" else 0o644,
            ),
            "ignore",
        )
        repository = migration_git.repository(plan.inventory.root.resolve())
        if repository is not None and plan.inventory.scope == "project":
            untracked = migration_git.tracked(repository, _ignored_outputs(plan))
        if repository is None and plan.inventory.scope == "global":
            new_repository = migration_git.new_source_repository(plan.inventory.root.resolve())
    return MigrationPreparation(
        plan,
        tuple(builder.operations),
        plan.preconditions,
        repository,
        untracked,
        new_repository,
    )


def apply_migration(prepared: MigrationPreparation) -> MigrationResult:
    """Apply every operation in order; the caller holds the backup that undoes it."""
    _verify_environment(prepared.plan)
    _verify(prepared.preconditions)
    if not prepared.operations:
        return MigrationResult((), (), True)
    for operation in prepared.operations:
        install(operation.path, operation.after)
    if prepared.new_repository is not None:
        migration_git.git(prepared.new_repository, "init", "-q")
    if prepared.repository is not None and prepared.untracked:
        migration_git.untrack(prepared.repository, prepared.untracked)
    return MigrationResult(
        tuple(dict.fromkeys(o.path for o in prepared.operations if o.after.kind == "file")),
        prepared.untracked,
        prepared.plan.already_initialized,
    )


def _retirable(plan: MigrationPlan, target: Path) -> bool:
    """Inside the root, or inside a harness root init read: never anywhere else."""
    bases = (
        plan.inventory.root,
        *(m.destination for m in plan.inventory.mappings if m.kind == "harness"),
    )
    return any(target.is_relative_to(base.resolve()) for base in bases)


def _verify_environment(plan: MigrationPlan) -> None:
    if plan.already_initialized:
        return
    captured = dict(plan.inventory.destination_environment)
    for key in DESTINATION_ENVIRONMENT:
        if (os.environ.get(key) or None) != captured.get(key):
            raise LoadoutError(
                f"destination environment changed; rediscover before applying migration: {key}"
            )
