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
import re
import shutil
from pathlib import Path

from .errors import LoadoutError
from .machine import load_machine_config, machine_config_path
from .manifest import load_manifest, manifest_path
from .project import PROJECT_DIR, load_project_config, project_config_path
from .resolve import ResolvedItem, Slice, resolve_item
from .skills import EXCLUDED_DIRECTORIES, EXCLUDED_NAMES, EXCLUDED_SUFFIXES
from .sources import Source

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
    return vendored_root(root) / name


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
    return tuple(s for s in manifest.sources if TEMPLATES.use in s.use)


def resolve_template(name: str, root: Path, config_path: Path | None = None) -> ResolvedItem:
    """A template name, resolved the way a fragment name is — one level up.

    A vendored copy stops resolution before the machine config is even read. That
    is what lets a clone build without the template repo, and it is why switching
    between declared and vendored is not a migration: same source, same list,
    a different place it resolves from.
    """
    local = vendored_path(root, name)
    if local.is_dir():
        return ResolvedItem(name=name, source=VENDORED, path=local)

    sources = declared_sources(config_path)
    try:
        return resolve_item(sources, name, TEMPLATES)
    except LoadoutError as error:
        searched = ", ".join(str(s.path / TEMPLATES_SUBDIR / name) for s in sources)
        where = searched or "(no source offers templates)"
        raise LoadoutError(f"{error} Searched {local} and {where}.") from error


# `[^\S\n]*` and not `\s*`: `\s` matches the newline, so a greedy trailing `\s*$`
# swallows it whenever the key is the file's last line, and the rewrite silently
# drops the final newline.
_TEMPLATES_KEY = re.compile(r"^templates[^\S\n]*=[^\S\n]*\[[^\]]*\][^\S\n]*$", re.MULTILINE)


def declare(root: Path, name: str) -> bool:
    """Add a name to `templates` in the project config. False if already there.

    Rewritten line-wise rather than re-serialised: the file is hand-maintained
    source, and round-tripping it through a writer would reformat the comments and
    key order its author chose.
    """
    path = project_config_path(root)
    config = load_project_config(path)
    if name in config.templates:
        return False
    rendered = "templates = [" + ", ".join(f'"{n}"' for n in [*config.templates, name]) + "]"
    text = path.read_text(encoding="utf-8")
    if _TEMPLATES_KEY.search(text):
        text = _TEMPLATES_KEY.sub(rendered, text, count=1)
    else:
        # Ahead of the first table header, so it stays a top-level key rather than
        # landing inside whichever table happens to come last.
        head, marker, tail = text.partition("\n[")
        text = head.rstrip("\n") + "\n" + rendered + "\n" + marker + tail
    path.write_text(text, encoding="utf-8")
    return True


def record_hash(root: Path, name: str, digest: str) -> None:
    """Record the content hash of a vendored copy, replacing any earlier one."""
    path = project_config_path(root)
    block = re.compile(rf"^\[template\.{re.escape(name)}\]\n(?:(?!\[).*\n?)*", re.MULTILINE)
    text = block.sub("", path.read_text(encoding="utf-8"))
    path.write_text(
        text.rstrip("\n") + f'\n\n[template.{name}]\nvendored = "{digest}"\n', encoding="utf-8"
    )


def copy_tree(source: Path, destination: Path) -> None:
    """Replace `destination` with `source`, byte for byte, mode included.

    Replace rather than overlay: a file the upstream dropped has to disappear from
    the copy too, or the recorded hash would describe a tree that is neither the
    upstream nor anything anyone wrote.

    Mode is copied for the reason `Copied` names a source path rather than decoded
    text — a skill's `scripts/` files are executable, and a template carries skills.
    """
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
        if recorded is not None and local.is_dir() and tree_hash(local) != recorded:
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
        if config.vendored_hash(name) is None and vendored_path(root, name).is_dir()
    ]


def _excluded(relative: Path) -> bool:
    if any(part in EXCLUDED_DIRECTORIES for part in relative.parts):
        return True
    return relative.name in EXCLUDED_NAMES or relative.suffix in EXCLUDED_SUFFIXES


def template_files(tree: Path) -> tuple[Path, ...]:
    """Every content file in a template, relative to its root, sorted.

    Build output is skipped for the reason a skill skips it: a template that once
    had a `__pycache__` in it would otherwise never compare equal to the same
    template checked out fresh.
    """
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
        path = tree / relative
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
