from __future__ import annotations

import difflib
import io
import json
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable
from pathlib import Path

from .emit import check_all, declared_profiles, render_all, write_all
from .errors import LoadoutError
from .machine import machine_config_path
from .manifest import MANIFEST_NAME, InstructionTarget, load_manifest, manifest_path
from .project import PROJECT_CONFIG_NAME, PROJECT_DIR, project_config_path
from .resolve import resolve_fragment
from .scaffold import add_harness, init_global, init_project

_DIFF_LIMIT = 40


def cmd_init(root: Path, harnesses: tuple[str, ...]) -> int:
    for action in init_project(root, harnesses, machine_config_path=machine_config_path()):
        print(action)
    print("\nEdit loadout/permissions.toml, then run `loadout sync`.")
    return 0


def cmd_init_global(source: Path | None, force: bool = False) -> int:
    if source is None:
        if not sys.stdin.isatty():
            raise LoadoutError(
                "--source is required when not attached to a terminal "
                "(loadout init --global --source <path>)"
            )
        default = Path.home() / "loadout"
        response = input(f"Directory to hold the global loadout source [{default}]: ").strip()
        source = Path(response) if response else default
    for action in init_global(source.expanduser(), machine_config_path(), force=force):
        print(action)
    return 0


def cmd_harness_add(root: Path, harness: str) -> int:
    for action in add_harness(root, harness):
        print(action)
    return 0


def _display(path: Path, root: Path) -> str:
    """A destination may live outside root (e.g. under ~); relative_to() would raise."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _normalise(path: Path, content: str) -> str:
    """Key order is load-bearing in output, but not in the question 'who wrote this?'.

    Claude Code rewrites its own settings.json and reorders keys loadout owns; comparing
    the parsed document keeps that from reading as a hand edit.
    """
    if path.suffix != ".json":
        return content
    try:
        return json.dumps(json.loads(content), sort_keys=True)
    except json.JSONDecodeError:
        return content


def _render_variants(
    source_root: Path, profiles: Iterable[str], rebase_to: Path
) -> dict[Path, set[str]]:
    variants: dict[Path, set[str]] = {}
    for profile in profiles:
        try:
            rendered = render_all(source_root, profile)
        except LoadoutError:
            continue
        for path, content in rendered.items():
            try:
                key = rebase_to / path.relative_to(source_root)
            except ValueError:
                key = path  # a destination outside the root renders to the same path
            variants.setdefault(key, set()).add(_normalise(key, content))
    return variants


def _committed_variants(root: Path, profiles: Iterable[str]) -> dict[Path, set[str]]:
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), "archive", "HEAD"], capture_output=True, check=False
        )
    except OSError:
        return {}
    if proc.returncode != 0:
        return {}
    with tempfile.TemporaryDirectory() as tmp_dir:
        try:
            with tarfile.open(fileobj=io.BytesIO(proc.stdout), mode="r:") as tar:
                tar.extractall(path=tmp_dir, filter="data")
        except (tarfile.TarError, OSError):
            return {}
        return _render_variants(Path(tmp_dir), profiles, rebase_to=root)


def _modified_outside_loadout(root: Path, profile: str) -> list[tuple[Path, str, str]] | None:
    """Files matching no output loadout itself could have written — None if unknowable.

    The accept set spans the committed source, the working source, and every declared
    profile, so an unsynced source edit, a synced one and a profile switch all match
    something. Without a committed baseline an unsynced edit is indistinguishable from
    a hand edit, so the caller must not block.
    """
    profiles = declared_profiles(root)
    acceptable = _committed_variants(root, profiles)
    if not acceptable:
        return None
    for path, forms in _render_variants(root, profiles, rebase_to=root).items():
        acceptable.setdefault(path, set()).update(forms)

    modified: list[tuple[Path, str, str]] = []
    for path, expected in render_all(root, profile).items():
        forms = acceptable.get(path, set())
        if not path.is_file() or not forms:
            continue
        actual = path.read_text(encoding="utf-8")
        if _normalise(path, actual) not in forms:
            modified.append((path, actual, expected))
    return modified


def _diff(rel: str, actual: str, expected: str, context: int) -> list[str]:
    return list(
        difflib.unified_diff(
            actual.splitlines(keepends=True),
            expected.splitlines(keepends=True),
            fromfile=f"{rel} (on disk)",
            tofile=f"{rel} (expected)",
            n=context,
        )
    )


def cmd_sync(root: Path, profile: str = "default", force: bool = False) -> int:
    if not force:
        modified = _modified_outside_loadout(root, profile)
        if modified is None:
            print("note: no committed baseline — skipping the modified-file check", file=sys.stderr)
        elif modified:
            for path, actual, expected in modified:
                rel = _display(path, root)
                print(f"WARNING: {rel} was modified outside loadout", file=sys.stderr)
                # Tight context: on a 16k settings.json the one runtime-added entry
                # should be readable without scrolling past the whole document.
                lines = _diff(rel, actual, expected, context=1)
                sys.stderr.writelines(lines[:_DIFF_LIMIT])
                if len(lines) > _DIFF_LIMIT:
                    print(f"    ... {len(lines) - _DIFF_LIMIT} more diff line(s)", file=sys.stderr)
            print(
                "\nSync aborted — the '-' lines above exist only on disk and would be lost. "
                "Move them into the source, or run `loadout sync --force` to discard them.",
                file=sys.stderr,
            )
            return 1

    for path in write_all(root, profile):
        print(f"wrote {_display(path, root)}")
    return 0


def cmd_check(root: Path, profile: str = "default") -> int:
    drift = check_all(root, profile)
    if not drift:
        print("generated files are up to date")
        return 0
    for path, actual, expected in drift:
        rel = _display(path, root)
        print(f"DRIFT: {rel}", file=sys.stderr)
        lines = list(_diff(rel, actual, expected, context=3))
        sys.stderr.writelines(lines[:_DIFF_LIMIT])
        if len(lines) > _DIFF_LIMIT:
            print(f"    ... {len(lines) - _DIFF_LIMIT} more diff line(s)", file=sys.stderr)
    print(
        f"\n{len(drift)} generated file(s) out of sync — run `loadout sync`.",
        file=sys.stderr,
    )
    return 1


def cmd_explain(root: Path, name: str) -> int:
    root_manifest = manifest_path(root)
    if not root_manifest.is_file() and project_config_path(root).is_file():
        raise LoadoutError(
            f"explain covers instruction fragments, which are global scope only "
            f"({MANIFEST_NAME}); this repo has project scope only "
            f"({PROJECT_DIR}/{PROJECT_CONFIG_NAME})."
        )
    manifest = load_manifest(root_manifest)
    item = resolve_fragment(manifest.sources, name)

    def label(target: InstructionTarget) -> str:
        if target.path is not None:
            return str(target.path)
        return f"destinations={', '.join(str(d) for d in target.destinations)}"

    users: list[str] = []
    unresolved: list[str] = []
    for target in manifest.targets:
        matched = False
        for fragment in target.fragments:
            try:
                if resolve_fragment(manifest.sources, fragment).path == item.path:
                    matched = True
            except LoadoutError:
                unresolved.append(f"{label(target)}: {fragment}")
        if matched:
            users.append(label(target))

    print(f"{item.name}")
    print(f"  source: {item.source}")
    print(f"  file:   {item.path}")
    if users:
        print("  used by:")
        for target_path in users:
            print(f"    {target_path}")
    else:
        print("  used by: (no target lists it)")
    if unresolved:
        print("  warning: other fragments in this manifest do not resolve:", file=sys.stderr)
        for entry in unresolved:
            print(f"    {entry}", file=sys.stderr)
    return 0
