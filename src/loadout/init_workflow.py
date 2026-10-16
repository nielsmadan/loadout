from __future__ import annotations

import json
import shlex
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, TextIO

import tomlkit

from .commands import diff_lines, report_diff
from .discovery import discover, initialized_project_path
from .emit import render_all
from .errors import LoadoutError, UsageError
from .harnesses import KNOWN_HARNESSES
from .init_options import InitOptions
from .machine import load_machine_config, machine_config_path, resolve_profile
from .migration import plan_migration
from .migration_backup import (
    Backup,
    Declared,
    Restored,
    complete,
    create_backup,
    interrupted_backup,
    latest_backup,
    record,
    restore,
)
from .migration_git import index_path
from .migration_models import Issue, MigrationPlan, SourceWrite, Unadopted
from .migration_transaction import (
    Delinked,
    MigrationPreparation,
    MigrationResult,
    apply_migration,
    prepare_migration,
)
from .output import Merged, Output
from .project import gitignore_entries
from .sync import RenderedChange, compare_with_disk, deploy
from .written import written_state_path


class _Cancelled(Exception):
    pass


def _answer(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, OSError, KeyboardInterrupt) as error:
        raise _Cancelled from error


def _plan(options: InitOptions) -> MigrationPlan:
    if not options.root.is_dir():
        raise LoadoutError(
            f"selected directory must exist: {options.root}; create a dedicated project/source directory first"
        )
    return plan_migration(discover(options.root, scope=options.scope, harnesses=options.harnesses))


def _project_harness_defaults(options: InitOptions) -> InitOptions:
    if options.scope != "project" or options.harnesses:
        return options
    if initialized_project_path(options.root) is not None:
        return options
    machine = load_machine_config(machine_config_path(), require_source=False)
    if machine is None or not machine.harnesses:
        return options
    return replace(options, harnesses=machine.harnesses)


def _resolve(options: InitOptions) -> tuple[InitOptions, MigrationPlan]:
    options = _project_harness_defaults(options)
    plan = _plan(options)
    if options.dry_run or options.json or not sys.stdin.isatty():
        return options, plan
    if any(i.code == "harnesses-required" for i in plan.issues):
        answer = _answer(
            "Configured harnesses (comma-separated: "
            f"{','.join(sorted(KNOWN_HARNESSES))}; empty cancels): "
        )
        if not answer:
            return options, plan
        options = replace(options, harnesses=tuple(a.strip() for a in answer.split(",")))
        plan = _plan(options)
    return options, plan


def _updated_registration(path: Path, text: str, agents: tuple[str, ...]) -> SourceWrite | None:
    if not agents:
        return None
    document = tomlkit.parse(text)
    document["harnesses"] = list(agents)
    content = tomlkit.dumps(document).encode()
    return None if content == text.encode() else SourceWrite(path, content, 0o600, True)


def _registration(
    options: InitOptions, plan: MigrationPlan
) -> tuple[SourceWrite | None, Issue | None]:
    if plan.inventory.scope != "global" or options.registration == "keep":
        return None, None
    path = machine_config_path().absolute()
    source = plan.inventory.source_root.resolve()
    existing_text: str | None = None
    if path.exists() or path.is_symlink():
        same = False
        if not path.is_symlink() and path.is_file():
            try:
                existing_text = path.read_text(encoding="utf-8")
                data = tomllib.loads(existing_text)
                raw = data.get("source")
                same = isinstance(raw, str) and Path(raw).expanduser().resolve() == source
            except (ValueError, OSError):
                pass
        if same:
            return _updated_registration(path, existing_text or "", plan.inventory.harnesses), None
        choice: str | None = options.registration
        if choice is None and sys.stdin.isatty() and not (options.dry_run or options.json):
            choice = _answer(
                f"Machine registration {path} differs. Replace or keep? [replace/keep] "
            )
        if choice == "keep":
            return None, None
        if choice != "replace":
            return None, Issue(
                "registration-conflict",
                (path,),
                "Choose --registration replace or --registration keep; --yes does not choose ownership.",
            )
    registration: dict[str, object] = {"source": str(source)}
    if plan.inventory.harnesses:
        registration["harnesses"] = list(plan.inventory.harnesses)
    return SourceWrite(path, tomlkit.dumps(registration).encode(), 0o600, True), None


def written_through(plan: MigrationPlan, machine: SourceWrite | None) -> tuple[str, ...]:
    """A link above a harness root stays a link, so what loadout writes beneath it lands in
    the link's target."""
    home = dict(plan.inventory.destination_environment).get("HOME")
    boundary = Path(home) if plan.inventory.scope == "global" and home else plan.inventory.root
    destinations = (
        *(m.source for m in plan.inventory.mappings),
        *((machine.path,) if machine else ()),
    )
    links = dict.fromkeys(
        link for path in destinations if (link := _link_above(path, boundary)) is not None
    )
    return tuple(
        f"{link} is a symlink to {link.resolve()}; loadout writes through it, so the files it "
        f"writes under {link} land in {link.resolve()}."
        for link in links
    )


def _link_above(path: Path, boundary: Path) -> Path | None:
    return next(
        (
            ancestor
            for ancestor in reversed(path.parents)
            if ancestor != boundary and ancestor.is_relative_to(boundary) and ancestor.is_symlink()
        ),
        None,
    )


ACTION_LABELS = {
    "retire": "Retire original",
    "retain": "Keep in place",
    "generated": "Becomes generated",
}


@dataclass(frozen=True)
class Preview:
    """What init shows before it changes anything: the plan, and once it is resolved,
    the operations, the registration and the first render."""

    plan: MigrationPlan
    prepared: MigrationPreparation | None = None
    registration: Path | None = None
    render: tuple[RenderedChange, ...] = ()
    notes: tuple[str, ...] = ()

    def document(self) -> dict[str, Any]:
        if self.prepared is None:
            return self.plan.preview()
        preview = self.prepared.preview()
        return {
            **preview,
            "registration": str(self.registration) if self.registration else None,
            "notes": [*preview["notes"], *self.notes],
            "outputs": [{"path": str(c.path), "change": c.kind} for c in self.render],
        }


def _show(preview: Preview, *, as_json: bool, status: str = "preview", exit_code: int = 0) -> None:
    if as_json:
        print(
            json.dumps({"status": status, "exit_code": exit_code, **preview.document()}, indent=2)
        )
        return
    plan, prepared = preview.plan, preview.prepared
    inventory = plan.inventory
    print(
        f"Scope: {inventory.scope or 'unresolved'}; harnesses: {', '.join(inventory.harnesses) or 'unresolved'}"
    )
    print(f"Source: {inventory.source_root}")
    for note in (*plan.notes, *preview.notes):
        print(note)
    _show_changes(plan, prepared)
    if preview.registration:
        print(f"Register global source: {preview.registration}")
    _show_render(preview.render)
    for item in plan.unadopted:
        print(f"Not adopted, conflicting copies of {item.kind} {item.name!r}:")
        for path, harnesses in item.originals:
            print(f"  {path} ({', '.join(harnesses)})")
        print("  Each copy stays in place; merge them into the source with /loadout.")
    for issue in plan.issues:
        print(f"{issue.code}: {issue.message}", file=sys.stderr)
        for issue_path in issue.paths:
            print(f"  {issue_path}", file=sys.stderr)


def _show_changes(plan: MigrationPlan, prepared: MigrationPreparation | None) -> None:
    if prepared is not None and prepared.new_repository:
        print(f"Create the source's Git repository: {prepared.new_repository}")
    for name in prepared.untracked if prepared is not None else ():
        print(f"Untrack (generated, now ignored): {name}")
    for write in plan.source_writes:
        print(f"Write source: {write.path}")
    for generated in plan.generated_writes:
        print(f"Generate: {generated.path}")
    for candidate in plan.inventory.candidates:
        if candidate.disposition == "migrated" and candidate.private:
            print(f"Private (stays on this machine): {candidate.path}: {candidate.private_reason}")
    for original in plan.originals:
        print(f"{ACTION_LABELS[original.action]}: {original.path} ({original.reason})")
    for delinked in prepared.delinked() if prepared is not None else ():
        print(f"Replace symlink: {delinked.link} -> {delinked.target}")


def _show_render(changes: tuple[RenderedChange, ...]) -> None:
    if not changes:
        return
    updates = [c for c in changes if c.kind == "update"]
    if updates:
        print(f"Render changes {len(updates)} existing file(s); applying overwrites them:")
        for change in updates:
            lines = diff_lines(str(change.path), change.actual, change.wanted, context=1)
            report_diff(lines, sys.stdout)
    else:
        print("Render: loadout reproduces your setup exactly.")
    for change in changes:
        if change.kind == "create":
            print(f"Create: {change.path}")


def _render_profile(plan: MigrationPlan) -> str:
    return resolve_profile(_render_root(plan))


def _render_root(plan: MigrationPlan) -> Path:
    return plan.inventory.root if plan.inventory.scope == "project" else plan.inventory.source_root


def _rebased(outputs: Mapping[Path, Output], scratch: Path, real: Path) -> dict[Path, Output]:
    def move(path: Path) -> Path:
        return real / path.relative_to(scratch) if path.is_relative_to(scratch) else path

    def records_moved(content: Output) -> Output:
        if not isinstance(content, Merged):
            return content
        return replace(content, records=tuple((move(r), t) for r, t in content.records))

    return {move(path): records_moved(content) for path, content in outputs.items()}


def planned_render(plan: MigrationPlan, profile: str) -> tuple[RenderedChange, ...]:
    """The first sync, judged before the migration writes anything.

    The planned source is materialised in scratch and rendered with the real
    environment, so global destinations are the real ones. A project output that
    preserves foreign keys reads its existing file, so the real one is copied in
    beside the source before the render that counts.
    """
    real = _render_root(plan).absolute()
    with tempfile.TemporaryDirectory(prefix="loadout-init-render-") as name:
        scratch = Path(name).resolve() / "root"
        source = scratch / "loadout" if plan.inventory.scope == "project" else scratch
        for write in plan.source_writes:
            path = source / write.path.relative_to(plan.inventory.source_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(write.content)
            path.chmod(write.mode)
        for path in render_all(scratch, profile):
            original = real / path.relative_to(scratch) if path.is_relative_to(scratch) else None
            if original is None or not original.is_file() or path.exists() or path.is_symlink():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            # An in-repo link decides what is rendered there, so scratch keeps it a link.
            target = original.resolve()
            if original.is_symlink() and target.is_relative_to(real.resolve()):
                path.symlink_to(scratch / target.relative_to(real.resolve()))
            else:
                path.write_bytes(original.read_bytes())
        outputs = _rebased(render_all(scratch, profile), scratch, real)
        return tuple(compare_with_disk(outputs))


def _first_sync(
    plan: MigrationPlan, profile: str, approved: tuple[RenderedChange, ...], *, as_json: bool
) -> int:
    root = _render_root(plan).absolute()
    outputs = render_all(root, profile)
    wanted = {change.path: change.wanted for change in compare_with_disk(outputs)}
    if wanted != {change.path: change.wanted for change in approved}:
        print(
            "loadout: rendered output changed after the preview, so nothing was written; "
            "review it with `loadout check`, then run `loadout sync`",
            file=sys.stderr,
        )
        return 1
    written, _ = deploy(root, outputs)
    if not as_json:
        print(f"Rendered {len(written)} generated file(s).")
    return 0


def delinked_notice(delinked: Delinked) -> str:
    if not delinked.directory:
        return (
            f"{delinked.link} was a symlink to {delinked.target}. It is now a real file "
            f"written by loadout; {delinked.target} is no longer used."
        )
    notice = (
        f"{delinked.link} was a symlink into {delinked.target}. loadout's files are now real "
        f"files here; everything else still links into {delinked.target}."
    )
    if delinked.superseded:
        copies = ", ".join(p.relative_to(delinked.target).as_posix() for p in delinked.superseded)
        notice = f"{notice} {delinked.target}'s copies of {copies} are no longer used."
    directories = ", ".join(f"{name}/" for name in delinked.written_into)
    inside = f", or inside {directories}," if directories else ""
    return (
        f"{notice} Entries added later to {delinked.target}'s top level{inside} "
        "will not appear here."
    )


def _result(
    result: MigrationResult,
    delinked: tuple[Delinked, ...],
    unadopted: tuple[Unadopted, ...],
    backup: Backup | None,
    *,
    as_json: bool,
) -> None:
    data = {
        "status": "complete",
        "exit_code": 0,
        "backup": str(backup.directory) if backup else None,
        "written": [str(p) for p in result.written],
        "untracked": list(result.untracked),
        "already_initialized": result.already_initialized,
        "delinked": [d.document() for d in delinked],
        "unadopted": [u.preview() for u in unadopted],
    }
    if as_json:
        print(json.dumps(data, indent=2))
    elif result.already_initialized:
        print(
            "Existing source adopted; no migration needed. Use loadout check/sync for managed outputs."
        )
    else:
        print("Migration complete; nothing was committed or staged.")
        if backup is not None:
            print(f"Backup of everything init changed: {backup.directory}")
        for each in delinked:
            print(delinked_notice(each))


def run_init(options: InitOptions) -> int:
    try:
        return _run_init(options)
    except _Cancelled:
        print("cancelled; no changes made")
        return 0


def _interrupted(options: InitOptions) -> int | None:
    """An init killed part way leaves its backup marked in progress; offer it first."""
    backup = interrupted_backup()
    if backup is None:
        return None
    if options.json or options.dry_run or not sys.stdin.isatty():
        message = {
            "status": "interrupted",
            "exit_code": 2,
            "backup": str(backup.directory),
            "message": "an earlier init did not finish; restore it with `loadout init restore`",
        }
        if options.json:
            print(json.dumps(message, indent=2))
        else:
            print(f"loadout: {message['message']} (backup {backup.directory})", file=sys.stderr)
        return 2
    answer = _answer(
        f"An earlier init did not finish. Restore everything it changed from {backup.directory}? "
        "[y/N] "
    )
    if answer.lower() not in {"y", "yes"}:
        print("declined; the backup stays. Restore it later with `loadout init restore`.")
        return 2
    return _restored(backup, as_json=False)


def _run_init(options: InitOptions) -> int:
    if (pending := _interrupted(options)) is not None:
        return pending
    options, plan = _resolve(options)
    machine, issue = _registration(options, plan)
    if issue:
        plan = replace(plan, issues=(*plan.issues, issue))
    if not plan.complete:
        _show(Preview(plan), as_json=options.json, status="incomplete", exit_code=2)
        return 2
    profile = _render_profile(plan)
    render = () if plan.already_initialized else planned_render(plan, profile)
    ignores = (
        gitignore_entries(plan.inventory.root, (change.path for change in render))
        if plan.inventory.scope == "project"
        else ()
    )
    prepared = prepare_migration(plan, machine_write=machine, ignores=ignores)
    if (stopped := _preview(options, prepared, machine, render)) is not None:
        return stopped
    if not prepared.operations:
        _result(
            MigrationResult((), (), True),
            prepared.delinked(),
            plan.unadopted,
            None,
            as_json=options.json,
        )
        return _after_migration(plan, already=True, as_json=options.json)
    return _apply(options, prepared, profile, render)


def _preview(
    options: InitOptions,
    prepared: MigrationPreparation,
    machine: SourceWrite | None,
    render: tuple[RenderedChange, ...],
) -> int | None:
    """Show the plan and get approval; an exit code when init stops here."""
    preview = Preview(
        prepared.plan,
        prepared,
        machine.path if machine else None,
        render,
        tuple(written_through(prepared.plan, machine)),
    )
    if options.dry_run or not options.json:
        _show(preview, as_json=options.json)
    if options.dry_run:
        return 0
    if not prepared.operations or options.yes:
        return None
    if options.json or not sys.stdin.isatty():
        raise UsageError("init requires --yes after reviewing a --dry-run preview; no changes made")
    if _answer("Apply this migration? [y/N] ").lower() not in {"y", "yes"}:
        print("declined; no changes made")
        return 0
    return None


def _apply(
    options: InitOptions,
    prepared: MigrationPreparation,
    profile: str,
    render: tuple[RenderedChange, ...],
) -> int:
    """Back up every path init can touch, apply, render, and restore on any failure."""
    plan = prepared.plan
    delinked = prepared.delinked()
    label = {"scope": plan.inventory.scope, "root": str(plan.inventory.root)}
    root = _render_root(plan).absolute()
    untracked = (
        (
            prepared.canonical(index_path(prepared.repository)),
            prepared.repository,
            prepared.untracked,
        )
        if prepared.repository is not None and prepared.untracked
        else None
    )
    declared = Declared(
        _planned(prepared, render),
        records=(prepared.canonical(written_state_path(root)),),
        untracked=untracked,
        repository=prepared.new_repository,
    )
    backup = create_backup(_touched(prepared, render), declared, now=datetime.now(UTC), label=label)
    try:
        result = apply_migration(prepared)
        record(backup, (prepared.canonical(path) for path in prepared.touched()))
        if prepared.new_repository is not None:
            created = prepared.canonical(prepared.new_repository / ".git")
            record(backup, (created, *sorted(created.rglob("*"))), created=True)
        code = (
            0
            if plan.already_initialized
            else _first_sync(plan, profile, render, as_json=options.json)
        )
    except BaseException as error:
        # Whatever stopped it, the paths go back before anything else happens.
        code = _failed(backup, str(error) or type(error).__name__, as_json=options.json)
        if not isinstance(error, Exception):
            raise
        return code
    if code:
        return _failed(backup, "the first render was not written", as_json=options.json)
    complete(backup)
    _result(result, delinked, plan.unadopted, backup, as_json=options.json)
    return _after_migration(plan, already=plan.already_initialized, as_json=options.json)


def _touched(prepared: MigrationPreparation, render: tuple[RenderedChange, ...]) -> list[Path]:
    """What applying and the first render can change: the operations, the rendered outputs
    and the render root's write record, each in its one spelling."""
    root = _render_root(prepared.plan).absolute()
    paths = (
        *prepared.touched(),
        *(change.path for change in render),
        written_state_path(root),
    )
    return list(dict.fromkeys(prepared.canonical(path) for path in paths))


def _planned(
    prepared: MigrationPreparation, render: tuple[RenderedChange, ...]
) -> dict[Path, list[str]]:
    """Every shape init may leave at each path it writes: what each operation writes,
    then what the first render writes, which can differ for the same file."""
    planned: dict[Path, list[str]] = {}
    for operation in prepared.operations:
        planned.setdefault(prepared.canonical(operation.path), []).append(operation.after.shape())
    for change in render:
        planned.setdefault(prepared.canonical(change.path), []).append(f"file:{change.sha256}")
    return planned


def _failed(backup: Backup, reason: str, *, as_json: bool) -> int:
    restored = restore(backup)
    message = f"init failed ({reason}); what it changed was restored from {backup.directory}"
    if as_json:
        print(
            json.dumps(
                {
                    "status": "restored",
                    "exit_code": 1,
                    "error": reason,
                    **_restore_report(restored, backup),
                }
            )
        )
    print(f"loadout: {message}", file=sys.stderr)
    _print_kept(restored, sys.stderr)
    return 1


def _restore_report(restored: Restored, backup: Backup) -> dict[str, object]:
    return {
        "backup": str(backup.directory),
        "kept": [str(path) for path in restored.kept],
        "links": {str(path): target for path, target in restored.links.items()},
    }


def _print_kept(restored: Restored, stream: TextIO) -> None:
    for path in restored.kept:
        print(f"Kept, not init's to undo: {path}", file=stream)
    for path, target in restored.links.items():
        print(f"{path} used to link to {target}; it stays a directory for now.", file=stream)
    if restored.kept:
        print(
            "The backup now holds only what was kept; run `loadout init restore` again "
            "once those paths are dealt with.",
            file=stream,
        )


def _after_migration(plan: MigrationPlan, *, already: bool, as_json: bool) -> int:
    if not already and not as_json:
        print(_hook_hint(plan))
        if plan.inventory.scope == "global":
            print(_commit_hint(plan))
    if plan.inventory.scope == "project" and not as_json and not machine_config_path().is_file():
        print("Global scope is not configured; use loadout init global when needed.")
    return 0


def _hook_hint(plan: MigrationPlan) -> str:
    root = plan.inventory.source_root if plan.inventory.scope == "global" else plan.inventory.root
    return (
        "To run loadout from that repository's Git hooks: "
        f"loadout integrate git-hooks install --root {shlex.quote(str(root))}"
    )


def _commit_hint(plan: MigrationPlan) -> str:
    root = shlex.quote(str(plan.inventory.root))
    return (
        "Nothing was committed. To commit the new source: "
        f"git -C {root} add -A && git -C {root} commit -m 'chore: adopt agent configuration'"
    )


def _restored(backup: Backup, *, as_json: bool) -> int:
    restored = restore(backup)
    if as_json:
        code = 1 if restored.kept else 0
        report = {"status": "restored", "exit_code": code, **_restore_report(restored, backup)}
        print(json.dumps(report, indent=2))
    else:
        print(f"Restored what init changed, from {backup.directory}.")
        _print_kept(restored, sys.stdout)
    return 1 if restored.kept else 0


def run_restore(*, yes: bool, as_json: bool) -> int:
    """Undo the latest adoption, or finish undoing one that was interrupted."""
    backup = interrupted_backup() or latest_backup()
    if backup is None:
        raise LoadoutError("no init backup to restore; a backup is spent once it is restored")
    if not yes:
        if as_json or not sys.stdin.isatty():
            raise UsageError("init restore requires --yes")
        try:
            answer = _answer(f"Restore everything init changed, from {backup.directory}? [y/N] ")
        except _Cancelled:
            answer = ""
        if answer.lower() not in {"y", "yes"}:
            print("declined; no changes made")
            return 0
    return _restored(backup, as_json=as_json)
