from __future__ import annotations

import shutil
import sys
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from .atomic import atomic_install, read_file
from .diffs import diff_lines
from .errors import LoadoutError, UsageError
from .kit_catalog import load_catalog
from .kits import (
    copy_tree,
    declare,
    has_local_edits,
    kit_base,
    kit_files,
    record_hash,
    resolve_upstream_kit,
    tree_hash,
    vendored_path,
)
from .output import FrozenFile
from .project import ProjectConfig, load_project_config, project_config_path
from .resolve import ResolvedItem

VendoredState = Literal["current", "level", "stale", "edited", "unverifiable"]


def vendored_state(copy: str, recorded: str | None, source: str) -> VendoredState:
    """A vendored copy against its source, from the three hashes. `level` is a copy equal
    to its source whose recorded hash says otherwise; `stale` is an unedited copy whose
    source moved."""
    if copy == source:
        return "current" if copy == recorded else "level"
    if recorded is None:
        return "unverifiable"
    return "edited" if copy != recorded else "stale"


def _install(path: Path, content: FrozenFile | None) -> None:
    if content is None:
        path.unlink(missing_ok=True)
    else:
        atomic_install(path, content)


def _desired(
    local: Path, upstream: Path, selected: dict[str, Path]
) -> dict[Path, FrozenFile | None]:
    incoming = load_catalog(upstream)
    desired = {
        local.parent / p.relative_to(upstream.parent): read_file(p) for p in incoming.files()
    }
    manifest = local.parent / upstream.name
    if manifest != local:
        desired[local] = desired.pop(manifest)
    retained: set[Path] = set()
    for other in selected.values():
        if other != local:
            retained.update(load_catalog(other).files())
    updated_parts = tuple(local.parent / p.relative_to(upstream.parent) for _, p in incoming.parts)
    current = load_catalog(local).files() if local.exists() else ()
    for path in current:
        replaced = any(path == p or path.is_relative_to(p) for p in updated_parts)
        if path not in desired and (path not in retained or replaced):
            desired[path] = None
    for path, content in desired.items():
        before = read_file(path)
        if (
            path not in current
            and before is not None
            and content != before
            and not (local.exists() and path in retained)
        ):
            raise LoadoutError(
                f"shared kit part conflicts with an existing file: {path}; sync its owning kit first"
            )
    return desired


def _affected(
    local: Path, name: str, desired: dict[Path, FrozenFile | None], selected: dict[str, Path]
) -> set[str]:
    changed = {path for path, content in desired.items() if read_file(path) != content}
    affected = {name}
    candidates = dict(selected)
    if local.parent.is_dir():
        for path in local.parent.glob("*.toml"):
            if path != local and path not in selected.values():
                candidates[str(path)] = path
    for other, manifest in candidates.items():
        if manifest.parent != local.parent:
            continue
        catalog = load_catalog(manifest)
        if any(
            file == part or file.is_relative_to(part)
            for _, part in catalog.parts
            for file in changed
        ):
            if other not in selected:
                raise LoadoutError(
                    f"shared update also affects {manifest}, which `kits` does not list; add it before syncing"
                )
            affected.add(other)
    return affected


def _clean(
    config: ProjectConfig,
    name: str,
    upstream: Path,
    affected: set[str],
    selected: dict[str, Path],
) -> None:
    for other in affected:
        manifest = selected.get(other)
        if manifest is None:
            continue
        if has_local_edits(
            manifest, config.vendored_hash(other), upstream if other == name else None
        ):
            raise LoadoutError(
                f"{other}: vendored copy is modified or has no recorded provenance; sync refused"
            )


def _preview(
    root: Path, name: str, affected: set[str], desired: dict[Path, FrozenFile | None]
) -> FrozenFile | None:
    with tempfile.TemporaryDirectory(prefix="loadout-kit-") as temporary:
        preview = Path(temporary).resolve()
        shutil.copytree(project_config_path(root).parent, preview / "loadout", symlinks=True)
        for path, content in desired.items():
            _install(preview / path.relative_to(root), content)
        declare(preview, name)
        for other in sorted(affected):
            record_hash(preview, other, tree_hash(vendored_path(preview, other)))
        return read_file(project_config_path(preview))


def _confirm(
    root: Path, changes: dict[Path, FrozenFile | None], affected: set[str], *, yes: bool
) -> bool:
    print("Kit changes:")
    for path, content in changes.items():
        print(f"    {'remove' if content is None else 'write'} {path.relative_to(root)}")
    if len(affected) <= 1 or yes:
        return True
    print("Affected kits: " + ", ".join(sorted(affected)))
    if not sys.stdin.isatty():
        raise UsageError(
            "updating parts shared with other kits needs --yes after reviewing the changes; "
            "no changes made"
        )
    try:
        answer = input("Update these shared kit parts? [y/N] ")
    except (EOFError, OSError, KeyboardInterrupt):
        answer = ""
    return answer.strip().lower() in {"y", "yes"}


def apply_changes(
    changes: Mapping[Path, FrozenFile | None],
    before: Mapping[Path, FrozenFile | None],
    failure: str,
) -> None:
    """Write each change, None deleting; a failure restores what was replaced and is
    raised as `failure` with the cause."""
    applied: list[Path] = []
    try:
        for path, content in changes.items():
            applied.append(path)
            _install(path, content)
    except BaseException as error:
        for path in reversed(applied):
            _install(path, before[path])
        if isinstance(error, OSError):
            raise LoadoutError(f"{failure}: {error}") from error
        raise


def vendor_catalog(
    root: Path, name: str, upstream: ResolvedItem, *, update: bool = False, yes: bool = False
) -> bool:
    """Vendor or update a catalog kit, and whether anything was written."""
    config_path = project_config_path(root)
    config = load_project_config(config_path)
    local = vendored_path(root, name)
    if not update:
        if local.exists():
            raise LoadoutError(f"{name} is already vendored; run `loadout kit sync {name}`")
        local = local.with_name(local.name + ".toml")
    if not upstream.path.is_file() or (update and not local.is_file()):
        raise LoadoutError("kit sync cannot change between a directory and a catalog manifest")
    selected = {n: p for n in config.kits if (p := vendored_path(root, n)).is_file()}
    if update:
        selected[name] = local
    before = {config_path: read_file(config_path)}
    desired = _desired(local, upstream.path, selected)
    affected = _affected(local, name, desired, selected)
    _clean(config, name, upstream.path, affected, selected)
    for path in desired:
        before[path] = read_file(path)
    hashes = {manifest: tree_hash(manifest) for manifest in selected.values()}
    for manifest in selected.values():
        before.update({p: read_file(p) for p in load_catalog(manifest).files()})
    desired[config_path] = _preview(root, name, affected, desired)
    changes = {path: content for path, content in desired.items() if content != before[path]}
    if not changes:
        print(f"{name} is up to date")
        return False
    if not _confirm(root, changes, affected, yes=yes):
        print("declined; no changes made")
        return False
    if any(read_file(path) != content for path, content in before.items()) or any(
        tree_hash(path) != digest for path, digest in hashes.items()
    ):
        raise LoadoutError("kit source changed during confirmation; nothing written, retry")
    apply_changes(changes, before, "kit update failed; previous files restored")
    return True


@dataclass(frozen=True)
class VendoredUpdate:
    """What updating a vendored directory kit from its source did, or why it did not."""

    state: Literal["updated", "current", "edited", "unverifiable"]
    upstream: Path
    changed: tuple[tuple[str, ...], ...] = ()


def update_vendored_directory(root: Path, name: str) -> VendoredUpdate:
    """Bring a vendored directory kit up to its source, refusing rather than merging.

    Refuse-and-diff is never wrong and never silently mangles anything. The
    alternative — a three-way merge against the version you vendored — is the
    largest single piece of work in this design, and the recorded hash is exactly
    the base it would need, so deferring it costs no state migration.
    """
    local = vendored_path(root, name)
    recorded = load_project_config(project_config_path(root)).vendored_hash(name)
    # A vendored copy resolves ahead of every source, so the upstream has to be
    # reached past it deliberately.
    upstream = resolve_upstream_kit(name).path
    if upstream.is_file():
        raise LoadoutError("kit sync cannot change between a directory and a catalog manifest")
    state = vendored_state(tree_hash(local), recorded, tree_hash(upstream))
    # Refuse unless the copy can be *proved* unmodified. A copy with no recorded hash
    # that matches its source is that proof, so it is recorded; one whose recorded hash
    # says modified stays refused here even when it now equals the source, which only
    # `loadout sync` in a terminal records (kit_push).
    if state == "level" and recorded is None:
        record_hash(root, name, tree_hash(local))
        return VendoredUpdate("current", upstream)
    if state in {"level", "edited"}:
        return VendoredUpdate("edited", upstream)
    if state == "unverifiable":
        return VendoredUpdate("unverifiable", upstream)
    if state == "current":
        return VendoredUpdate("current", upstream)
    return VendoredUpdate("updated", upstream, refresh_directory(root, name, upstream))


def refresh_directory(root: Path, name: str, upstream: Path) -> tuple[tuple[str, ...], ...]:
    """Replace a vendored directory copy with its source and record it; the diff of each
    changed file."""
    local = vendored_path(root, name)
    changed = tuple(tuple(diff) for diff in _file_diffs(local, upstream, name))
    copy_tree(upstream, local)
    record_hash(root, name, tree_hash(local))
    return changed


def kit_diff(local: Path, upstream: Path, label: str) -> list[str]:
    """A unified diff per file across both kits, added and removed files included."""
    return [line for diff in _file_diffs(local, upstream, label) for line in diff]


def _file_diffs(local: Path, upstream: Path, label: str) -> list[list[str]]:
    """Each changed file's diff lines, so a file whose diff is withheld still counts."""
    diffs: list[list[str]] = []
    for relative in sorted({*kit_files(local), *kit_files(upstream)}):
        here, there = kit_base(local) / relative, kit_base(upstream) / relative
        try:
            actual = here.read_text(encoding="utf-8") if here.is_file() else ""
            expected = there.read_text(encoding="utf-8") if there.is_file() else ""
        except UnicodeDecodeError:
            # A kit carries whatever a skill carries, binaries included, and
            # a byte diff of one is noise rather than a review.
            if not (here.is_file() and there.is_file()) or here.read_bytes() != there.read_bytes():
                diffs.append([f"{label}/{relative}: differs (binary)\n"])
            continue
        if actual != expected:
            diffs.append(diff_lines(f"{label}/{relative}", actual, expected, context=3))
    return diffs
