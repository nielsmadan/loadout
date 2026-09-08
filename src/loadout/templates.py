"""Templates — shared configuration for a kind of project.

A template is a source (spec 3): a named bundle of the portable slices that a
project opts into, merged beneath everything the project itself declares. It
resolves by **name**, never by path, because a path in a committed file means
nothing on a colleague's machine and less in CI.

Declared and vendored are the same source resolved from two places, not a primary
path and an escape hatch. What makes vendoring safe is the recorded content hash:
it answers the one question `sync` has to ask before it overwrites anything.
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import tomlkit

from .bundled_templates import BUNDLED, bundled_template
from .errors import LoadoutError
from .machine import load_machine_config, machine_config_path
from .manifest import load_manifest, manifest_path
from .project import PROJECT_DIR, load_project_config, project_config_path
from .resolve import ResolvedItem, Slice
from .skills import EXCLUDED_DIRECTORIES, EXCLUDED_NAMES, EXCLUDED_SUFFIXES
from .sources import Source
from .template_catalog import load_catalog, template_name
from .toml_paths import apply_paths, key_name

HASH_PREFIX = "sha256:"

TEMPLATES_SUBDIR = "templates"
TEMPLATES = Slice(use="templates", subdir=TEMPLATES_SUBDIR, suffix="", directory=True)

# The `source` a vendored template reports. Parenthesised so it cannot collide
# with a real source name, which is a bare identifier.
VENDORED = "(vendored)"


def vendored_root(root: Path) -> Path:
    """Vendored templates get a directory of their own, never merged into the
    project's own fragments — otherwise nothing could later tell template-owned
    content from content you wrote, and sync would be impossible."""
    return root / PROJECT_DIR / TEMPLATES_SUBDIR


def vendored_path(root: Path, name: str) -> Path:
    template_name(name)
    directory = vendored_root(root) / name
    manifest = directory.with_name(directory.name + ".toml")
    if manifest.exists() and directory.exists():
        raise LoadoutError(f"ambiguous template {name!r}: both {directory} and {manifest} exist")
    return manifest if manifest.exists() else directory


def declared_sources(config_path: Path | None = None) -> tuple[Source, ...]:
    """Every source the machine's global manifest declares that offers templates.

    Project scope carries no `[[source]]` list of its own, and must not: a path in
    a committed file is wrong for everyone who is not its author. So a declared
    name resolves through the machine config, which is where this machine's paths
    already live (ADR 0010).
    """
    path = machine_config_path() if config_path is None else config_path
    machine = load_machine_config(path)
    if machine is None:
        raise LoadoutError(
            f"no machine config at {path}, so a declared template has nowhere to "
            f"resolve from; run `loadout init --global`, or vendor the template"
        )
    manifest = load_manifest(manifest_path(machine.source))
    sources = tuple(s for s in manifest.sources if TEMPLATES.use in s.use)
    if not manifest.sources and manifest.artifacts is not None:
        return (Source(machine.source.name, machine.source / "loadout", frozenset({"templates"})),)
    return sources


def resolve_template(name: str, root: Path, config_path: Path | None = None) -> ResolvedItem:
    """A template name, resolved the way a fragment name is — one level up.

    A vendored copy stops resolution before the machine config is even read. That
    is what lets a clone build without the template repo, and it is why switching
    between declared and vendored is not a migration: same source, same list,
    a different place it resolves from.
    """
    local = vendored_path(root, name)
    if local.exists():
        return ResolvedItem(name=name, source=VENDORED, path=local)

    try:
        return resolve_upstream_template(name, config_path)
    except LoadoutError as error:
        raise LoadoutError(f"{error} Vendored path: {local}.") from error


def resolve_upstream_template(name: str, config_path: Path | None = None) -> ResolvedItem:
    template_name(name)
    path = machine_config_path() if config_path is None else config_path
    bundled = bundled_template(name)
    if bundled is not None and not path.exists():
        return _bundled_template(name, bundled)
    sources = declared_sources(config_path)
    bare = name
    if "/" in name:
        qualifier, bare = name.split("/")
        sources = tuple(s for s in sources if s.name == qualifier)
        if not sources:
            raise LoadoutError(f"unknown source {qualifier!r} in {name!r}")
    hits: list[ResolvedItem] = []
    searched: list[str] = []
    for source in sources:
        for suffix in ("", ".toml"):
            candidate = source.path / TEMPLATES_SUBDIR / (bare + suffix)
            searched.append(str(candidate))
            if candidate.exists() or candidate.is_symlink():
                if not candidate.resolve().is_relative_to(
                    (source.path / TEMPLATES_SUBDIR).resolve()
                ):
                    raise LoadoutError(f"template name escapes its source: {name!r}")
                if suffix and not candidate.is_file():
                    raise LoadoutError(f"template manifest is not a file: {candidate}")
                if not suffix and not candidate.is_dir():
                    raise LoadoutError(f"template is not a directory: {candidate}")
                hits.append(ResolvedItem(bare, source.name, candidate))
    if len(hits) > 1:
        raise LoadoutError(
            f"{name!r} is ambiguous across sources or formats: "
            + ", ".join(str(h.path) for h in hits)
        )
    if hits:
        return hits[0]
    if bundled is not None:
        return _bundled_template(name, bundled)
    raise LoadoutError(
        f"templates not found: {name!r}. Searched {', '.join(searched) or '(no source offers templates)'}."
    )


def _bundled_template(name: str, path: Path) -> ResolvedItem:
    if not (path / "instructions.md").is_file():
        raise LoadoutError(f"bundled starter missing at {path}; reinstall the loadout package")
    return ResolvedItem(name=name, source=BUNDLED, path=path)


def declare(root: Path, name: str) -> bool:
    """Add a name to the project config, preserving its TOML syntax tree."""
    path = project_config_path(root)
    config = load_project_config(path)
    if name in config.templates:
        return False
    template_name(name)
    document = tomlkit.parse(path.read_text(encoding="utf-8"))
    document["templates"] = [*config.templates, name]
    path.write_text(tomlkit.dumps(document), encoding="utf-8")
    return True


def record_hash(root: Path, name: str, digest: str) -> None:
    """Record the content hash of a vendored copy, replacing any earlier one."""
    path = project_config_path(root)
    key = key_name(("template", name, "vendored"))
    document = f"[{key_name(('template', name))}]\nvendored = {json.dumps(digest)}\n"
    path.write_text(
        apply_paths(path.read_text(encoding="utf-8"), frozenset({key}), document), encoding="utf-8"
    )


def copy_tree(source: Path, destination: Path) -> None:
    """Copy bytes and modes; replace directories, but refuse conflicting catalog files."""
    if source.is_file():
        files = load_catalog(source).files()
        for path in files:
            target = (
                destination
                if path == source
                else destination.parent / path.relative_to(source.parent)
            )
            if target.exists() and target.read_bytes() != path.read_bytes():
                raise LoadoutError(f"catalog copy would replace existing content: {target}")
        for path in files:
            target = (
                destination
                if path == source
                else destination.parent / path.relative_to(source.parent)
            )
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            shutil.copymode(path, target)
        return
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    for relative in template_files(source):
        target = destination / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source / relative, target)
        shutil.copymode(source / relative, target)


def template_divergence(root: Path) -> list[str]:
    """Vendored templates whose content no longer matches the recorded hash.

    Reported rather than failed: a vendored copy is source, and a user editing
    their own source is not drift. See ADR 0014.
    """
    path = project_config_path(root)
    if not path.is_file():
        return []
    config = load_project_config(path)
    diverged: list[str] = []
    for name in config.templates:
        recorded = config.vendored_hash(name)
        local = vendored_path(root, name)
        if recorded is not None and local.exists() and tree_hash(local) != recorded:
            diverged.append(name)
    return diverged


def unverifiable_templates(root: Path) -> list[str]:
    """Vendored copies with no recorded hash, so nothing can vouch for them.

    A distinct state from divergence, and it has to be reported separately
    because it is the *absence* of the evidence divergence is measured against:
    `template_divergence` can only speak about copies it has a base for, so a
    missing hash reads there as "no divergence" rather than "cannot say".

    `loadout harness add` produced exactly this state until it stopped rewriting
    the config from scratch, so repos are in it today with nothing to tell them.
    """
    path = project_config_path(root)
    if not path.is_file():
        return []
    config = load_project_config(path)
    return [
        name
        for name in config.templates
        if config.vendored_hash(name) is None and vendored_path(root, name).exists()
    ]


def _excluded(relative: Path) -> bool:
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
        return True
    return relative.name in EXCLUDED_NAMES or relative.suffix in EXCLUDED_SUFFIXES


def template_files(tree: Path) -> tuple[Path, ...]:
    """Sorted content paths relative to the directory or manifest parent, excluding build output."""
    if tree.is_file():
        return tuple(p.relative_to(tree.parent) for p in load_catalog(tree).files())
    if not tree.is_dir():
        return ()
    return tuple(
        sorted(
            item.relative_to(tree)
            for item in tree.rglob("*")
            if item.is_file() and not _excluded(item.relative_to(tree))
        )
    )


def tree_hash(tree: Path) -> str:
    """A content hash of a template, independent of where the tree sits.

    Path-independent by construction — only paths *relative* to the template root
    are hashed — so vendoring does not change the hash, which is what lets one
    recorded value compare a copy against its upstream.

    A git SHA would not do: a template may come from a plain directory with no
    repository behind it.
    """
    digest = hashlib.sha256()
    for relative in template_files(tree):
        path = (tree.parent if tree.is_file() else tree) / relative
        payload = path.read_bytes()
        digest.update(relative.as_posix().encode("utf-8"))
        digest.update(b"\0")
        digest.update(b"x" if path.stat().st_mode & 0o111 else b"-")
        digest.update(b"\0")
        # The length pins the boundary, so no arrangement of bytes across two
        # files can collide with a different arrangement across two others.
        digest.update(str(len(payload)).encode("ascii"))
        digest.update(b"\0")
        digest.update(payload)
        digest.update(b"\0")
    return HASH_PREFIX + digest.hexdigest()
