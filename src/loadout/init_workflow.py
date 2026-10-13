from __future__ import annotations

import json
import shlex
import sys
import tempfile
import tomllib
from collections.abc import Mapping
from dataclasses import replace
from pathlib import Path
from typing import Any

import tomlkit

from .commands import DIFF_LIMIT, RenderedChange, compare_with_disk, deploy, diff_lines
from .deployment import DeploymentConflict
from .discovery import discover, initialized_project_path
from .emit import render_all
from .errors import LoadoutError, UsageError
from .harnesses import KNOWN_HARNESSES
from .init_options import InitOptions
from .machine import load_machine_config, machine_config_path
from .migration import plan_migration
from .migration_models import Issue, MigrationPlan, SourceWrite, Unadopted
from .migration_transaction import (
    Delinked,
    MigrationFailure,
    MigrationResult,
    apply_migration,
    prepare_migration,
    recover_migration,
    resume_migration,
)
from .output import Merged, Output


class _Cancelled(Exception):
    pass


def _answer(prompt: str) -> str:
    try:
        return input(prompt).strip()
    except (EOFError, OSError, KeyboardInterrupt) as error:
        raise _Cancelled from error


def _plan(options: InitOptions) -> MigrationPlan:
    selected = options.source or options.root
    if not selected.is_dir():
        raise LoadoutError(
            f"selected directory must exist: {selected}; create a dedicated project/source directory first"
        )
    return plan_migration(discover(selected, scope=options.scope, agents=options.agents))


def _project_harness_defaults(options: InitOptions) -> InitOptions:
    if options.scope != "project" or options.agents:
        return options
    if initialized_project_path(options.root) is not None:
        return options
    machine = load_machine_config(machine_config_path(), require_source=False)
    if machine is None or not machine.harnesses:
        return options
    return replace(options, agents=machine.harnesses)


def _resolve(options: InitOptions) -> tuple[InitOptions, MigrationPlan]:
    options = _project_harness_defaults(options)
    plan = _plan(options)
    if options.dry_run or options.json or not sys.stdin.isatty():
        return options, plan
    if any(i.code == "agents-required" for i in plan.issues):
        answer = _answer(
            "Configured harnesses (comma-separated: "
            f"{','.join(sorted(KNOWN_HARNESSES))}; empty cancels): "
        )
        if not answer:
            return options, plan
        options = replace(options, agents=tuple(a.strip() for a in answer.split(",")))
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
            return _updated_registration(path, existing_text or "", plan.inventory.agents), None
        choice = options.registration
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
    if plan.inventory.agents:
        registration["harnesses"] = list(plan.inventory.agents)
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


def _show(preview: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(preview, indent=2))
        return
    print(
        f"Scope: {preview['scope'] or 'unresolved'}; agents: {', '.join(preview['agents']) or 'unresolved'}"
    )
    print(f"Source: {preview['source_root']}")
    for note in preview["notes"]:
        print(note)
    git = preview.get("git")
    if git:
        print(
            f"Git repository: {git['root']} ({'initialize' if git['initialize'] else 'existing'})"
        )
        print("Baseline checkpoint paths (review for private information):")
        for path in git["baseline_paths"]:
            print(f"  {path}")
        print(
            "Checkpoint detection cannot prove arbitrary content secret-free. Existing Git hooks may run."
        )
        print(
            "Migration source and ignore changes will be staged; the final migration is not committed."
        )
    for label, key in (("Write source", "source_writes"), ("Generate", "generated_writes")):
        for entry in preview[key]:
            print(f"{label}: {entry['path']}")
    for original in preview["originals"]:
        print(f"{original['action']}: {original['path']} ({original['reason']})")
    for delinked in preview.get("delinked", ()):
        print(f"Replace symlink: {delinked['link']} -> {delinked['target']}")
    if preview.get("registration"):
        print(f"Register global source: {preview['registration']}")
    _show_render(preview.get("render", ()))
    _show_unadopted(preview["unadopted"])
    for issue in preview["issues"]:
        print(f"{issue['code']}: {issue['message']}", file=sys.stderr)
        for path in issue["paths"]:
            print(f"  {path}", file=sys.stderr)


def _show_unadopted(items: list[dict[str, Any]]) -> None:
    for item in items:
        print(f"Not adopted, conflicting copies of {item['kind']} {item['name']!r}:")
        for original in item["originals"]:
            print(f"  {original['path']} ({', '.join(original['harnesses'])})")
        print("  Each copy stays in place; merge them into the source with /loadout.")


def _show_render(changes: tuple[RenderedChange, ...]) -> None:
    if not changes:
        return
    updates = [c for c in changes if c.kind == "update"]
    if updates:
        print(f"Render changes {len(updates)} existing file(s); applying overwrites them:")
        for change in updates:
            sys.stdout.writelines(
                diff_lines(str(change.path), change.actual, change.wanted, context=1)[:DIFF_LIMIT]
            )
    else:
        print("Render: loadout reproduces your setup exactly.")
    for change in changes:
        if change.kind == "create":
            print(f"Create: {change.path}")


def _render_profile(plan: MigrationPlan) -> str:
    if plan.inventory.scope == "project":
        return "default"
    machine = load_machine_config(machine_config_path(), require_source=False)
    if machine is None or machine.source.resolve() != plan.inventory.source_root.resolve():
        return "default"
    return machine.profile or "default"


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
    try:
        written, _ = deploy(root, profile, outputs)
    except DeploymentConflict as error:
        print(f"loadout: the first render was not written: {error}", file=sys.stderr)
        return 1
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
        f"{delinked.link} was a symlink into {delinked.target}. Loadout's files are now real "
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
    *,
    as_json: bool,
) -> None:
    data = {
        "status": "complete",
        "journal": str(result.journal) if result.journal else None,
        "baseline": result.baseline,
        "written": [str(p) for p in result.written],
        "staged": list(result.staged),
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
        print(f"Migration complete; {len(result.staged)} source paths staged.")
        for each in delinked:
            print(delinked_notice(each))


def run_init(options: InitOptions) -> int:
    try:
        return _run_init(options)
    except _Cancelled:
        print("cancelled; no changes made")
        return 0


def _run_init(options: InitOptions) -> int:
    options, plan = _resolve(options)
    machine, issue = _registration(options, plan)
    if issue:
        plan = replace(plan, issues=(*plan.issues, issue))
    if not plan.complete:
        _show(plan.preview(), as_json=options.json)
        return 2
    prepared = prepare_migration(plan, machine_write=machine)
    delinked = prepared.delinked()
    profile = _render_profile(plan)
    render = () if plan.already_initialized else planned_render(plan, profile)
    preview = {**prepared.preview(), "registration": str(machine.path) if machine else None}
    preview["notes"] = [*preview["notes"], *written_through(plan, machine)]
    if options.json:
        preview["outputs"] = [{"path": str(c.path), "change": c.kind} for c in render]
    else:
        preview["render"] = render
    if options.dry_run or not options.json:
        _show(preview, as_json=options.json)
    if options.dry_run:
        return 0
    if (prepared.operations or prepared.git) and not options.yes:
        if options.json or not sys.stdin.isatty():
            raise UsageError(
                "init requires --yes after reviewing a --dry-run preview; no changes made"
            )
        if _answer(
            "Apply this migration, including its Git checkpoint and final staging? [y/N] "
        ).lower() not in {"y", "yes"}:
            print("declined; no changes made")
            return 0
    try:
        result = apply_migration(prepared)
    except MigrationFailure as error:
        return report_failure(error, as_json=options.json)
    _result(result, delinked, plan.unadopted, as_json=options.json)
    return _after_migration(plan, result, profile, render, as_json=options.json)


def _after_migration(
    plan: MigrationPlan,
    result: MigrationResult,
    profile: str,
    render: tuple[RenderedChange, ...],
    *,
    as_json: bool,
) -> int:
    if not result.already_initialized:
        rendered = _first_sync(plan, profile, render, as_json=as_json)
        if rendered:
            return rendered
        if not as_json:
            print(_hook_hint(plan))
    if plan.inventory.scope == "project" and not as_json and not machine_config_path().is_file():
        print("Global scope is not configured; use loadout init global when needed.")
    return 0


def _hook_hint(plan: MigrationPlan) -> str:
    root = plan.inventory.source_root if plan.inventory.scope == "global" else plan.inventory.root
    return (
        "To run loadout from that repository's Git hooks: "
        f"loadout integrate git-hooks install --root {shlex.quote(str(root))}"
    )


def report_failure(error: MigrationFailure, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"status": "interrupted", "journal": str(error.journal)}))
    print(f"loadout: {error}", file=sys.stderr)
    print(
        f"Resume: loadout init resume {shlex.quote(str(error.journal))} --yes\nRecover: loadout init recover {shlex.quote(str(error.journal))} --yes",
        file=sys.stderr,
    )
    return 1


def run_recovery(path: Path, *, recover: bool, yes: bool, as_json: bool) -> int:
    action = "recover" if recover else "resume"
    if not yes:
        if as_json or not sys.stdin.isatty():
            raise UsageError(f"init {action} requires --yes")
        try:
            answer = _answer(f"{action.capitalize()} migration journal {path}? [y/N] ")
        except _Cancelled:
            answer = ""
        if answer.lower() not in {"y", "yes"}:
            print("declined; no changes made")
            return 0
    try:
        if not recover:
            _result(resume_migration(path), (), (), as_json=as_json)
            return 0
        result = recover_migration(path)
    except MigrationFailure as error:
        return report_failure(error, as_json=as_json)
    data = {
        "status": "recovery-conflicts" if result.conflicts else "recovered",
        "journal": str(result.journal) if result.journal else None,
        "conflicts": [str(p) for p in result.conflicts],
        "baseline": result.baseline,
    }
    print(
        json.dumps(data, indent=2) if as_json else f"{data['status']}; successful baseline retained"
    )
    for conflict in result.conflicts:
        print(f"Recovery conflict: {conflict}", file=sys.stderr)
    return 1 if result.conflicts else 0
