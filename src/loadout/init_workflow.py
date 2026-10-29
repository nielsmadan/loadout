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
from typing import Any, Literal

import tomlkit

from .containment import first_link, refuse_escaping
from .diffs import diff_lines, report_diff
from .discovery import discover, initialized_project_path, project_root
from .emit import RenderContext, refuse_unregistered_global, render_all, render_project
from .errors import LoadoutError, UsageError
from .harnesses import KNOWN_HARNESSES
from .init_options import InitOptions
from .machine import (
    is_registered,
    load_machine_config,
    machine_config_path,
    registered_at,
    registration,
    resolve_profile,
)
from .migration import plan_migration
from .migration_backup import Backup, create_backup, discard, interrupted_backup, restore
from .migration_models import Issue, MigrationPlan, SourceWrite, secrets_document
from .migration_transaction import (
    Delinked,
    MigrationPreparation,
    MigrationResult,
    apply_migration,
    prepare_migration,
)
from .output import Merged, Output
from .project import PROJECT_DIR, gitignore_entries
from .skill_copy import cmd_skill_copy, copy_command
from .state import sync_state
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
        # Keeping another registration leaves a source init is creating nothing it may
        # render, so it is offered only for one already initialized (review 5, 4.2).
        keepable = plan.already_initialized
        choices = "replace/keep" if keepable else "replace"
        choice: str | None = options.registration
        if choice is None and sys.stdin.isatty() and not (options.dry_run or options.json):
            choice = _answer(f"Machine registration {path} differs. Replace it? [{choices}] ")
        if choice == "keep":
            return None, None
        if choice != "replace":
            advice = (
                "Choose --registration replace or --registration keep"
                if keepable
                else "Run init with --registration replace to register this source"
            )
            return None, Issue(
                "registration-conflict", (path,), f"{advice}; --yes does not choose ownership."
            )
    registration: dict[str, object] = {"source": str(source)}
    if plan.inventory.harnesses:
        registration["harnesses"] = list(plan.inventory.harnesses)
    return SourceWrite(path, tomlkit.dumps(registration).encode(), 0o600, True), None


def _refuse_unregistered_render(plan: MigrationPlan, machine: SourceWrite | None) -> None:
    """The first sync renders global scope, which only the registered source may do, so
    keeping another registration leaves init nothing it can render (ADR 0031)."""
    source = plan.inventory.source_root
    if plan.inventory.scope != "global" or plan.already_initialized or machine is not None:
        return
    found = registration()
    # A machine config that does not load is the answer, not an unregistered source.
    if found.error is not None:
        raise found.error
    if not is_registered(source, found.source):
        raise LoadoutError(
            f"{source} would not be this machine's registered source, and only that source "
            "renders global scope; nothing was changed. Run init with --registration replace "
            "to register it"
        )


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
        link
        for path in destinations
        if path.parent.is_relative_to(boundary)
        and (link := first_link(boundary, path.parent)) is not None
    )
    return tuple(
        f"{link} is a symlink to {link.resolve()}; loadout writes through it, so the files it "
        f"writes under {link} land in {link.resolve()}."
        for link in links
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


InitStatus = Literal["preview", "incomplete", "complete", "interrupted", "restored"]


def _envelope(status: InitStatus, code: int, **fields: object) -> int:
    """Print init's JSON document and return its exit code."""
    print(json.dumps({"status": status, "exit_code": code, **fields}, indent=2))
    return code


def _show(
    preview: Preview,
    *,
    as_json: bool,
    status: InitStatus = "preview",
    exit_code: int = 0,
    extra: Mapping[str, object] | None = None,
) -> None:
    if as_json:
        _envelope(status, exit_code, **preview.document(), **(extra or {}))
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
    for path, reason in plan.secrets:
        print(f"Contains a secret: {path}: {reason}")
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
            lines = diff_lines(str(change.path), change.actual, change.expected, context=1)
            report_diff(lines, sys.stdout)
    elif not any(c.kind == "normalised" for c in changes):
        print("Render: loadout reproduces your setup exactly.")
    for change in changes:
        if change.kind == "normalised":
            print(f"Normalise: {change.path} (only loadout's banner or formatting changes)")
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
        try:
            return _planned_render(plan, profile, scratch, real)
        except LoadoutError as error:
            # Nothing outside init knows the scratch folder, so a refusal names the project.
            raise LoadoutError(str(error).replace(str(scratch), str(real))) from error


def _planned_render(
    plan: MigrationPlan, profile: str, scratch: Path, real: Path
) -> tuple[RenderedChange, ...]:
    source = scratch / PROJECT_DIR if plan.inventory.scope == "project" else scratch
    for write in plan.source_writes:
        path = source / write.path.relative_to(plan.inventory.source_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(write.content)
        path.chmod(write.mode)
    # init registers the source it plans, so its scratch copy may render global scope.
    context = RenderContext(registration=registered_at(scratch))
    rendered = list(_render(plan, scratch, profile, context))
    if plan.inventory.scope == "project":
        # Before an original is copied in: a link in the project would be read through.
        # The skills folder links apply replaces with real folders are the exception.
        replaced = {p for w in plan.generated_writes for p in w.path.parents if p.is_symlink()}
        written = [real / path.relative_to(scratch) for path in rendered]
        refuse_escaping(real, [p for p in written if replaced.isdisjoint(p.parents)], written)
    for path in rendered:
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
    outputs = _rebased(_render(plan, scratch, profile, context), scratch, real)
    return tuple(compare_with_disk(outputs))


def _render(
    plan: MigrationPlan, root: Path, profile: str, context: RenderContext
) -> dict[Path, Output]:
    """What init renders: a project init renders its project alone, though the root may
    also be the registered global source (review 3, batch 2.3)."""
    if plan.inventory.scope == "project":
        return render_project(root, context)
    return render_all(root, profile, context)


def _first_sync(
    plan: MigrationPlan, profile: str, approved: tuple[RenderedChange, ...], *, as_json: bool
) -> list[Path]:
    """The paths the first render wrote."""
    root = _render_root(plan).absolute()
    outputs = _render(plan, root, profile, RenderContext())
    rendered = {change.path: change.expected for change in compare_with_disk(outputs)}
    if rendered != {change.path: change.expected for change in approved}:
        raise LoadoutError("the first render differs from the preview, so it was not written")
    written, _ = deploy(sync_state(root), outputs)
    if not as_json:
        print(f"Rendered {len(written)} generated file(s).")
    return written


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


def closing_message(untracked: tuple[str, ...]) -> str:
    if not untracked:
        return "Migration complete; nothing was committed or staged."
    heading = (
        f"Migration complete; nothing was committed. {len(untracked)} adopted file(s) "
        "loadout now generates were removed from the index, so `git status` shows them "
        "as staged deletions:"
    )
    return "\n".join([heading, *(f"  {name}" for name in untracked)])


def _result(
    result: MigrationResult,
    delinked: tuple[Delinked, ...],
    plan: MigrationPlan,
    *,
    as_json: bool,
) -> None:
    fields = {
        "written": [str(p) for p in result.written],
        "rendered": [str(p) for p in result.rendered],
        "untracked": list(result.untracked),
        "already_initialized": result.already_initialized,
        "delinked": [d.document() for d in delinked],
        "unadopted": [u.preview() for u in plan.unadopted],
        "secrets": secrets_document(plan.secrets),
    }
    if as_json:
        _envelope("complete", 0, **fields)
    elif result.already_initialized:
        print(
            "Existing source adopted; no migration needed. Use loadout check/sync for managed outputs."
        )
    else:
        print(closing_message(result.untracked))
        for each in delinked:
            print(delinked_notice(each))
        if plan.secrets:
            print(
                "Note: the source now contains secrets, so keep its remote private. "
                "The /loadout skill can move each one into an environment variable."
            )


def run_init(options: InitOptions) -> int:
    try:
        return _run_init(options)
    except _Cancelled:
        print("cancelled; no changes made")
        return 0


def interrupted_document(backup: Backup) -> dict[str, object]:
    error = (
        "an earlier init was stopped part way and left what it changed in place; "
        "run init again at a terminal to put that back first"
    )
    return {"backup": str(backup.directory), "error": error}


def _interrupted(options: InitOptions) -> int | None:
    """A run killed part way leaves its backup marked in progress, and nothing else
    happens until it is put back."""
    backup = interrupted_backup()
    if backup is None:
        return None
    if options.json or options.dry_run or not sys.stdin.isatty():
        document = interrupted_document(backup)
        if options.json:
            return _envelope("interrupted", 2, **document)
        print(f"loadout: {document['error']} (backup {backup.directory})", file=sys.stderr)
        return 2
    root = backup.manifest()["root"]
    answer = _answer(
        f"An earlier init of {root} was stopped part way. Put back everything it changed? "
        "Edits made since to those files are lost. [y/N] "
    )
    if answer.lower() not in {"y", "yes"}:
        print("declined; nothing was changed, and init stops here until that is put back.")
        return 2
    put_back = restore(backup)
    print(f"Put back {len(put_back)} path(s) as they were before that init; run init again.")
    return 0


def _run_init(options: InitOptions) -> int:
    if (pending := _interrupted(options)) is not None:
        return pending
    if options.scope == "project":
        # The folder init adopts, which a subdirectory resolves up to before planning.
        refuse_unregistered_global(project_root(options.root))
    options, plan = _resolve(options)
    machine, issue = _registration(options, plan)
    if issue:
        plan = replace(plan, issues=(*plan.issues, issue))
    if not plan.complete:
        harnesses = plan.inventory.harnesses
        converts = any(issue.code not in CHOICES for issue in plan.issues)
        _show(
            Preview(plan),
            as_json=options.json,
            status="incomplete",
            exit_code=2,
            extra=_skill_fields(harnesses) if converts else None,
        )
        if converts and not options.json:
            _offer_skill(harnesses, options)
        return 2
    _refuse_unregistered_render(plan, machine)
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
        _result(MigrationResult((), (), True), prepared.delinked(), plan, as_json=options.json)
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
    """Back up every path init can touch, apply and render; on any failure put every
    path back, and on success drop the backup."""
    plan = prepared.plan
    delinked = prepared.delinked()
    backup = create_backup(
        _touched(prepared, render), now=datetime.now(UTC), root=plan.inventory.root
    )
    try:
        result = apply_migration(prepared)
        rendered = (
            []
            if plan.already_initialized
            else _first_sync(plan, profile, render, as_json=options.json)
        )
    except BaseException as error:
        # Whatever stopped it, the paths go back before anything else happens.
        interrupted = not isinstance(error, Exception)
        code = _failed(backup, plan, options, str(error) or type(error).__name__, interrupted)
        if interrupted and not isinstance(error, KeyboardInterrupt):
            raise
        return code
    discard(backup)
    _result(replace(result, rendered=tuple(rendered)), delinked, plan, as_json=options.json)
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


def _failed(
    backup: Backup, plan: MigrationPlan, options: InitOptions, reason: str, interrupted: bool
) -> int:
    code = INTERRUPTED if interrupted else 1
    try:
        put_back = restore(backup)
    except (OSError, LoadoutError) as error:
        message = (
            f"init failed ({reason}), and putting back what it changed failed too ({error}); "
            "the next init puts it back first"
        )
        if options.json:
            return _envelope("interrupted", code, error=message, backup=str(backup.directory))
        print(f"loadout: {message} (backup {backup.directory})", file=sys.stderr)
        return code
    harnesses = plan.inventory.harnesses
    if options.json:
        restored = [str(path) for path in put_back]
        return _envelope(
            "restored", code, error=reason, restored=restored, **_skill_fields(harnesses)
        )
    print(f"loadout: init failed ({reason}); everything it changed was put back", file=sys.stderr)
    # Stopped by the user: advise, but ask nothing more of them.
    _offer_skill(harnesses, options, ask=not interrupted)
    return code


# The shell's exit status for a process stopped by SIGINT.
INTERRUPTED = 130

# Issues a flag answers: the choice is the user's, and the skill cannot make it for them.
CHOICES = frozenset({"harnesses-required", "scope-required", "registration-conflict"})

SKILL_ADVICE = (
    "loadout could not convert this setup on its own. The /loadout skill can do the "
    "conversion with you: install it into your current setup and start it there."
)


def _skill_fields(harnesses: tuple[str, ...]) -> dict[str, object]:
    return {
        "recommendation": SKILL_ADVICE,
        "install_command": copy_command(harnesses or ("<harness>",)),
    }


def _offer_skill(harnesses: tuple[str, ...], options: InitOptions, *, ask: bool = True) -> None:
    """Recommend the skill, and at a terminal offer to copy it into the harnesses' user
    skills folders; otherwise print the command that does."""
    print(SKILL_ADVICE, file=sys.stderr)
    if ask and harnesses and not options.dry_run and sys.stdin.isatty():
        try:
            answer = _answer(
                "Install the /loadout skill into your current setup so you can run the "
                "conversion with it? [y/N] "
            )
        except _Cancelled:
            answer = ""
        if answer.lower() not in {"y", "yes"}:
            return
        try:
            cmd_skill_copy(harnesses)
        except (OSError, LoadoutError) as error:
            print(f"loadout: {error}", file=sys.stderr)
        return
    print(f"To install it: {copy_command(harnesses or ('<harness>',))}", file=sys.stderr)


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
