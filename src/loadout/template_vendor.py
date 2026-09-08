from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from .artifacts import FrozenFile
from .deployment import atomic_install, read_file
from .errors import LoadoutError
from .native_templates import validate_template_change
from .project import ProjectConfig, load_project_config, project_config_path
from .resolve import ResolvedItem
from .template_catalog import load_catalog
from .templates import VENDORED, declare, record_hash, tree_hash, vendored_path


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
                f"shared template part conflicts with an existing file: {path}; sync its owning template first"
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
                    f"shared update also affects undeclared manifest {manifest}; declare it before syncing"
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
        digest = tree_hash(manifest)
        recorded = config.vendored_hash(other)
        if digest != recorded and not (
            other == name and recorded is None and digest == tree_hash(upstream)
        ):
            raise LoadoutError(
                f"{other}: vendored copy is modified or has no recorded provenance; sync refused"
            )


def _preview(
    root: Path, local: Path, name: str, affected: set[str], desired: dict[Path, FrozenFile | None]
) -> FrozenFile | None:
    with tempfile.TemporaryDirectory(prefix="loadout-template-") as temporary:
        preview = Path(temporary).resolve()
        shutil.copytree(project_config_path(root).parent, preview / "loadout", symlinks=True)
        for path, content in desired.items():
            _install(preview / path.relative_to(root), content)
        preview_local = preview / local.relative_to(root)
        validate_template_change(preview, name, ResolvedItem(name, VENDORED, preview_local))
        declare(preview, name)
        for other in sorted(affected):
            record_hash(preview, other, tree_hash(vendored_path(preview, other)))
        return read_file(project_config_path(preview))


def _confirm(root: Path, changes: dict[Path, FrozenFile | None], affected: set[str]) -> bool:
    print("Template changes:")
    for path, content in changes.items():
        print(f"    {'remove' if content is None else 'write'} {path.relative_to(root)}")
    if len(affected) <= 1:
        return True
    print("Affected templates: " + ", ".join(sorted(affected)))
    try:
        answer = input("Update these shared template parts? [y/N] ")
    except (EOFError, OSError):
        answer = ""
    return answer.strip().lower() in {"y", "yes"}


def _apply(changes: dict[Path, FrozenFile | None], before: dict[Path, FrozenFile | None]) -> None:
    applied: list[Path] = []
    try:
        for path, content in changes.items():
            applied.append(path)
            _install(path, content)
    except BaseException as error:
        for path in reversed(applied):
            _install(path, before[path])
        if isinstance(error, OSError):
            raise LoadoutError(
                f"template update failed; previous files restored: {error}"
            ) from error
        raise


def vendor_catalog(root: Path, name: str, upstream: ResolvedItem, *, update: bool = False) -> int:
    config_path = project_config_path(root)
    config = load_project_config(config_path)
    local = vendored_path(root, name)
    if not update:
        if local.exists():
            raise LoadoutError(f"{name} is already vendored; run `loadout template sync {name}`")
        local = local.with_name(local.name + ".toml")
    if not upstream.path.is_file() or (update and not local.is_file()):
        raise LoadoutError("template sync cannot change between a directory and a catalog manifest")
    selected = {n: p for n in config.templates if (p := vendored_path(root, n)).is_file()}
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
    desired[config_path] = _preview(root, local, name, affected, desired)
    changes = {path: content for path, content in desired.items() if content != before[path]}
    if not changes:
        print(f"{name} is up to date")
        return 0
    if not _confirm(root, changes, affected):
        print("Cancelled; nothing changed.")
        return 1
    if any(read_file(path) != content for path, content in before.items()) or any(
        tree_hash(path) != digest for path, digest in hashes.items()
    ):
        raise LoadoutError("template source changed during confirmation; nothing written, retry")
    _apply(changes, before)
    print(
        f"{'updated' if update else 'vendored'} {name}; run `loadout sync` to regenerate project outputs"
    )
    return 0
