from __future__ import annotations

import difflib
import io
import os
import subprocess
import sys
import tarfile
import tempfile
from collections.abc import Iterable, Mapping
from pathlib import Path

from .bundled_skill import bundled_skill_path
from .emit import (
    Copied,
    Merged,
    Output,
    check_all,
    collect_notices,
    declared_profiles,
    describe_file,
    render_all,
    write_outputs,
)
from .errors import LoadoutError, UsageError
from .machine import machine_config_path
from .manifest import MANIFEST_NAME, InstructionTarget, load_manifest, manifest_path
from .project import (
    PROJECT_CONFIG_NAME,
    PROJECT_DIR,
    load_project_config,
    project_config_path,
)
from .resolve import resolve_fragment, resolve_item
from .scaffold import add_harness, init_global, init_project
from .skill_installation import (
    SkillSourceLocation,
    SourceSkillState,
    configured_skill_agents,
    inspect_skill_source,
    install_skill_source,
    uninstall_skill_source,
)
from .templates import (
    TEMPLATES,
    VENDORED,
    copy_tree,
    declare,
    declared_sources,
    record_hash,
    resolve_template,
    template_divergence,
    template_files,
    tree_hash,
    unverifiable_templates,
    vendored_path,
)
from .written import (
    WrittenEntry,
    accepts_bytes,
    accepts_text,
    copied_entry,
    merged_entry,
    normalise,
    read_written,
    record_written,
    text_entry,
)

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


def _print_skill_location(
    root: Path, profile: str, source_name: str | None
) -> tuple[Path, SkillSourceLocation, tuple[str, ...]]:
    bundle = bundled_skill_path()
    location = inspect_skill_source(root, profile, bundle, source_name)
    agents = configured_skill_agents(root, profile)
    print(f"source {location.source}: {location.path} ({location.state.value})")
    print(f"configured agents: {', '.join(agents) if agents else 'none'}")
    return bundle, location, agents


def _confirm_skill_change(action: str, path: Path, yes: bool) -> bool:
    if yes:
        return True
    try:
        response = input(f"{action} the loadout skill at {path} and sync global config? [y/N] ")
    except (EOFError, OSError) as error:
        raise UsageError(f"skill {action} requires --yes when input is not interactive") from error
    return response.strip().lower() in {"y", "yes"}


def cmd_skill_status(root: Path, profile: str, source_name: str | None = None) -> int:
    _print_skill_location(root, profile, source_name)
    return 0


def cmd_skill_install(
    root: Path,
    profile: str,
    source_name: str | None = None,
    *,
    yes: bool = False,
) -> int:
    agents = configured_skill_agents(root, profile)
    if not agents:
        print("configured agents: none")
        print("no configured agents receive global skills; no changes made")
        return 0
    bundle, location, _ = _print_skill_location(root, profile, source_name)
    if location.state in {SourceSkillState.CONFLICTING, SourceSkillState.MODIFIED}:
        detail = (
            "exists and is not owned by loadout"
            if location.state is SourceSkillState.CONFLICTING
            else "was modified after installation"
        )
        print(f"loadout: {location.path} {detail}; no changes made", file=sys.stderr)
        return 1
    if location.state is not SourceSkillState.INSTALLED and not _confirm_skill_change(
        "install", location.path, yes
    ):
        print("declined; no changes made")
        return 0
    try:
        changed = install_skill_source(location, bundle)
    except LoadoutError as error:
        print(f"loadout: {error}", file=sys.stderr)
        return 1
    print(
        f"{'installed' if changed else 'already installed'} loadout skill in "
        f"source {location.source}: {location.path}"
    )
    return cmd_sync(root, profile=profile)


def cmd_skill_uninstall(
    root: Path,
    profile: str,
    source_name: str | None = None,
    *,
    yes: bool = False,
) -> int:
    _, location, _ = _print_skill_location(root, profile, source_name)
    if location.state is SourceSkillState.MISSING:
        print("loadout skill is not installed; no changes made")
        return 0
    if location.state in {SourceSkillState.CONFLICTING, SourceSkillState.MODIFIED}:
        detail = (
            "exists and is not owned by loadout"
            if location.state is SourceSkillState.CONFLICTING
            else "was modified after installation"
        )
        print(f"loadout: {location.path} {detail}; no changes made", file=sys.stderr)
        return 1
    if not _confirm_skill_change("uninstall", location.path, yes):
        print("declined; no changes made")
        return 0
    try:
        removed = uninstall_skill_source(location, root=root, profile=profile)
    except LoadoutError as error:
        print(f"loadout: {error}", file=sys.stderr)
        return 1
    print(
        f"uninstalled loadout skill from source {location.source}: {location.path} "
        f"({len(removed)} generated files removed)"
    )
    return cmd_sync(root, profile=profile)


def _display(path: Path, root: Path) -> str:
    """A destination may live outside root (e.g. under ~); relative_to() would raise."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


# Case-insensitive because the banner has been both `GENERATED` and `Generated`;
# a normaliser that only knows the current spelling fails exactly when it is
# needed, which is the sync that changes the spelling.
# `//` is here for the generated hook adapters, which are JavaScript and
# TypeScript. Every other generated file comments with `#` or `<!--`, so a
# comment marker this did not know would leave the banner in the compared text
# and reintroduce the whole-tree false positive for exactly those two files.
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
            if isinstance(content, Copied):
                continue  # verbatim by definition, so it has no per-profile form to vary
            if isinstance(content, Merged):
                continue  # its legitimate content includes foreign material, so the
                # set of acceptable forms is not finite and cannot be enumerated here
            try:
                key = rebase_to / path.relative_to(source_root)
            except ValueError:
                key = path  # a destination outside the root renders to the same path
            variants.setdefault(key, set()).add(normalise(key, content))
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


def _modified_outside_loadout(
    root: Path, outputs: Mapping[Path, Output]
) -> list[tuple[Path, str, str]] | None:
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
    # The third variant, and the only one that knows about a source state which was
    # never committed and has since moved on. See written.py and ADR 0019.
    written = read_written(root)

    modified: list[tuple[Path, str, str]] = []
    for path, expected in outputs.items():
        if isinstance(expected, Copied):
            # A copied file has no per-profile form, so it is compared against its
            # source directly. check_all only *reports* drift; this is the guard
            # that stops sync overwriting a hand edit, and it has to cover a
            # supporting file just as much as a SKILL.md.
            if (
                path.is_file()
                and path.read_bytes() != expected.source.read_bytes()
                and not accepts_bytes(written.get(path), path.read_bytes())
            ):
                modified.append((path, describe_file(path), describe_file(expected.source)))
            continue
        if isinstance(expected, Merged):
            # Applying replaces owned keys and passes everything else through, so
            # sync cannot destroy a foreign edit here and there is nothing for this
            # guard to protect. An edit to an *owned* key is still overwritten
            # silently; `check` reports it, and covering it properly needs a
            # baseline this guard does not have for a file loadout does not own.
            continue
        forms = acceptable.get(path, set())
        if not path.is_file() or not forms:
            continue
        actual = path.read_text(encoding="utf-8")
        if normalise(path, actual) not in forms and not accepts_text(
            written.get(path), path, actual
        ):
            modified.append((path, actual, expected))
    return modified


def _write_full_diff(lines: list[str]) -> Path:
    """The untruncated diff, so an abort the terminal shortened stays reviewable."""
    descriptor, name = tempfile.mkstemp(suffix=".diff", prefix="loadout-")
    with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
        handle.writelines(lines)
    return Path(name)


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
    # Rendered once and reused for the guard, the write and the record, so what is
    # recorded is provably what was written rather than a third render of it.
    outputs = render_all(root, profile)
    if not force:
        modified = _modified_outside_loadout(root, outputs)
        if modified is None:
            print("note: no committed baseline — skipping the modified-file check", file=sys.stderr)
        elif modified:
            full: list[str] = []
            for path, actual, expected in modified:
                rel = _display(path, root)
                print(f"WARNING: {rel} was modified outside loadout", file=sys.stderr)
                # Tight context: on a 16k settings.json the one runtime-added entry
                # should be readable without scrolling past the whole document.
                lines = _diff(rel, actual, expected, context=1)
                full.extend(lines)
                sys.stderr.writelines(lines[:_DIFF_LIMIT])
                if len(lines) > _DIFF_LIMIT:
                    print(f"    ... {len(lines) - _DIFF_LIMIT} more line(s)", file=sys.stderr)
            print(
                "\nSync aborted — the '-' lines would be lost. Move them into the source, "
                "or run `loadout sync --force` to discard them.",
                file=sys.stderr,
            )
            # A blocking prompt the reader cannot see all of is one they learn to
            # skip, and skipping this one means reaching for --force, which is the
            # thing it exists to prevent. Whatever the terminal truncates, the file
            # holds in full — the decision stays reviewable.
            if any(
                len(_diff(_display(p, root), a, e, context=1)) > _DIFF_LIMIT for p, a, e in modified
            ):
                print(f"\nThe complete diff is at {_write_full_diff(full)}", file=sys.stderr)
            return 1

    for path in write_outputs(outputs):
        print(f"wrote {_display(path, root)}")
    # After the write, so a run that aborts part-way records nothing and the next
    # sync falls back to the two renders — losing the record fails closed.
    record_written(root, profile, _entries_for(outputs))
    _report_notices(root, profile)
    return 0


def _entries_for(outputs: Mapping[Path, Output]) -> dict[Path, WrittenEntry]:
    entries: dict[Path, WrittenEntry] = {}
    for path, content in outputs.items():
        if isinstance(content, Copied):
            entries[path] = copied_entry(content.source)
        elif isinstance(content, Merged):
            entries[path] = merged_entry(content.owned)
        else:
            entries[path] = text_entry(path, content)
    return entries


def _report_notices(root: Path, profile: str) -> None:
    """Advisory findings, on the same terms as a diverged vendored template.

    A notice describes a source that rendered successfully while doing less than
    it says — a plugin left switched off, a hook that cannot fire. The output is
    correct, so this never moves an exit code; treating it as drift would fail a
    render that did the right thing with what it was given.

    Until this existed the four reports behind it reached nobody, and one was
    visible only by opening the generated JavaScript.
    """
    for notice in collect_notices(root, profile):
        print(f"note: {notice.render()}")


def cmd_check(root: Path, profile: str = "default") -> int:
    drift = check_all(root, profile)
    _report_notices(root, profile)
    # Reported, never failed: check's contract is that generated output matches
    # its source, and a vendored template *is* source — so it falls outside that
    # jurisdiction by definition rather than by exemption (ADR 0014). Saying
    # nothing at all would let a project drift from its template indefinitely,
    # which is the failure vendoring exists to avoid.
    for name in template_divergence(root):
        print(
            f"note: the vendored template {name!r} was modified after it was vendored — "
            f"`loadout template sync {name}` will refuse until those edits go upstream"
        )
    for name in unverifiable_templates(root):
        print(
            f"note: the vendored template {name!r} has no recorded provenance, so nothing "
            f"can tell your edits from its source — `loadout template vendor {name}` takes "
            f"the source wholesale and records one"
        )
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
    # `check` used to end every drift with "run `loadout sync`" even where sync was
    # about to refuse, sending the reader in a circle and teaching them --force. Ask
    # the guard the same question sync will, and say which answer applies.
    hand_edited = _modified_outside_loadout(root, render_all(root, profile))
    blocked = {path for path, _, _ in hand_edited or ()}
    if blocked:
        for path in sorted(blocked):
            print(f"MODIFIED OUTSIDE LOADOUT: {_display(path, root)}", file=sys.stderr)
        print(
            f"\n{len(drift)} generated file(s) out of sync. {len(blocked)} of them hold edits "
            f"loadout did not write, and `loadout sync` will refuse rather than discard them — "
            f"move those into the source, or run `loadout sync --force`.",
            file=sys.stderr,
        )
        return 1
    print(
        f"\n{len(drift)} generated file(s) out of sync — run `loadout sync`.",
        file=sys.stderr,
    )
    return 1


def _vendored_state(root: Path, name: str, recorded: str | None) -> str:
    """How a vendored copy stands against the hash recorded when it was vendored."""
    if recorded is None:
        return "vendored, no recorded hash"
    if tree_hash(vendored_path(root, name)) == recorded:
        return "vendored, clean"
    return "vendored, modified"


def cmd_template_list(root: Path) -> int:
    config = load_project_config(project_config_path(root))
    if not config.templates:
        print(
            f"no templates declared — add one with `loadout template add <name>`, "
            f"or list it in {PROJECT_DIR}/{PROJECT_CONFIG_NAME}"
        )
        return 0
    for name in config.templates:
        try:
            found = resolve_template(name, root)
        except LoadoutError as error:
            # Reported rather than raised: `list` is the command you reach for
            # *because* something is wrong, so one broken name must not hide the rest.
            print(f"{name}: unresolved")
            print(f"    {error}")
            continue
        if found.source == VENDORED:
            print(f"{name}: {_vendored_state(root, name, config.vendored_hash(name))}")
        else:
            print(f"{name}: declared, from source {found.source}")
        print(f"    {_display(found.path, root)}")
    return 0


def cmd_template_add(root: Path, name: str) -> int:
    """Declare a template without copying it in — the other first-class mode.

    Resolved before it is written so a name that cannot be found never reaches the
    committed config, where it would fail every later render.
    """
    found = resolve_template(name, root)
    if declare(root, name):
        print(f"declared {name} in {PROJECT_DIR}/{PROJECT_CONFIG_NAME}")
    else:
        print(f"{name} is already declared")
    origin = "the vendored copy" if found.source == VENDORED else f"source {found.source}"
    print(f"resolves from {origin}: {_display(found.path, root)}")
    return 0


def cmd_template_vendor(root: Path, name: str) -> int:
    local = vendored_path(root, name)
    if local.is_dir():
        raise LoadoutError(
            f"{name} is already vendored at {_display(local, root)}; run "
            f"`loadout template sync {name}` to update it"
        )
    found = resolve_template(name, root)
    copy_tree(found.path, local)
    declare(root, name)
    record_hash(root, name, tree_hash(local))
    print(f"vendored {name} from source {found.source} into {_display(local, root)}")
    for relative in template_files(local):
        print(f"    {relative}")
    return 0


def _tree_diff(local: Path, upstream: Path, label: str) -> list[str]:
    """A unified diff per file across both trees, added and removed files included."""
    lines: list[str] = []
    for relative in sorted({*template_files(local), *template_files(upstream)}):
        here, there = local / relative, upstream / relative
        try:
            actual = here.read_text(encoding="utf-8") if here.is_file() else ""
            expected = there.read_text(encoding="utf-8") if there.is_file() else ""
        except UnicodeDecodeError:
            # A template carries whatever a skill carries, binaries included, and
            # a byte diff of one is noise rather than a review.
            if not (here.is_file() and there.is_file()) or here.read_bytes() != there.read_bytes():
                lines.append(f"{label}/{relative}: differs (binary)\n")
            continue
        if actual != expected:
            lines.extend(_diff(f"{label}/{relative}", actual, expected, context=3))
    return lines


def _report_diff(lines: list[str]) -> None:
    sys.stderr.writelines(lines[:_DIFF_LIMIT])
    if len(lines) > _DIFF_LIMIT:
        print(f"    ... {len(lines) - _DIFF_LIMIT} more diff line(s)", file=sys.stderr)


def cmd_template_sync(root: Path, name: str) -> int:
    """Update a vendored copy from its source, refusing rather than merging.

    Refuse-and-diff is never wrong and never silently mangles anything. The
    alternative — a three-way merge against the version you vendored — is the
    largest single piece of work in this design, and the recorded hash is exactly
    the base it would need, so deferring it costs no state migration.
    """
    config = load_project_config(project_config_path(root))
    local = vendored_path(root, name)
    if not local.is_dir():
        raise LoadoutError(
            f"{name} is not vendored, so there is nothing to sync — it resolves from a "
            f"source on every render. Run `loadout template vendor {name}` to copy it in."
        )

    recorded = config.vendored_hash(name)
    current = tree_hash(local)
    # A vendored copy resolves ahead of every source, so the upstream has to be
    # reached past it deliberately.
    upstream = resolve_item(declared_sources(), name, TEMPLATES).path

    # Refuse unless the copy can be *proved* unmodified, rather than refusing only
    # when it can be proved modified. Those differ exactly when there is no
    # provenance to compare against — which is the state `harness add` produced —
    # and fail-open there would let a command whose contract is refuse-rather-than-
    # merge silently overwrite local edits. Matching upstream is proof enough, so a
    # clean copy still falls through to the self-heal below and is never refused.
    if recorded is None and tree_hash(upstream) != current:
        print(
            f"WARNING: the vendored copy of {name} has no recorded provenance",
            file=sys.stderr,
        )
        _report_diff(_tree_diff(local, upstream, name))
        print(
            f"\nSync refused — with no recorded hash, nothing can tell your edits from "
            f"the source's, so the '-' lines above may be either. Commit your copy first "
            f"if you want to keep them, or delete {_display(local, root)} and run "
            f"`loadout template vendor {name}` to take the source wholesale.",
            file=sys.stderr,
        )
        return 1

    if recorded is not None and current != recorded:
        print(
            f"WARNING: the vendored copy of {name} was modified after it was vendored",
            file=sys.stderr,
        )
        _report_diff(_tree_diff(local, upstream, name))
        print(
            f"\nSync refused — the '-' lines above exist only in your copy and would be "
            f"lost. Move them upstream, or delete {_display(local, root)} and run "
            f"`loadout template vendor {name}` to take the upstream wholesale.",
            file=sys.stderr,
        )
        return 1

    # **Position is deliberate: this must stay below both refusals.** It reads
    # like a cheap early-out that belongs at the top, and hoisting it changes a
    # real case — a copy whose recorded hash says modified but whose content now
    # equals the upstream currently refuses, and would start reporting "up to
    # date" instead. Nothing asked for that. The no-provenance gate above already
    # falls through to here when the copy matches, which is the self-heal.
    if tree_hash(upstream) == current:
        print(f"{name} is up to date")
        if recorded is None:
            record_hash(root, name, current)
        return 0

    changed = _tree_diff(local, upstream, name)
    copy_tree(upstream, local)
    record_hash(root, name, tree_hash(local))
    print(
        f"updated {name} from source, {sum(1 for c in changed if c.startswith('---'))} file(s) changed"
    )
    _report_diff(changed)
    return 0


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
