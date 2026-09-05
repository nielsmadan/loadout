from __future__ import annotations

import json
import shlex
import sys
import tomllib
from dataclasses import replace
from pathlib import Path
from typing import Any

import tomlkit

from .discovery import discover
from .errors import LoadoutError, UsageError
from .git_hooks import HookPlan, plan_hooks, show_hooks
from .init_options import InitOptions, parse_mapping, parse_selection
from .machine import load_machine_config, machine_config_path
from .migration import plan_migration
from .migration_git import git
from .migration_models import Issue, MigrationPlan, SourceWrite
from .migration_transaction import (
    MigrationFailure,
    MigrationResult,
    apply_migration,
    prepare_migration,
    recover_migration,
    resume_migration,
)


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
    return plan_migration(
        discover(
            selected,
            scope=options.scope,
            agents=options.agents,
            mappings=options.mappings,
        ),
        selections=options.selections,
        starter=options.starter,
    )


def _resolve(options: InitOptions) -> tuple[InitOptions, MigrationPlan]:
    plan = _plan(options)
    if options.dry_run or options.json or not sys.stdin.isatty():
        return options, plan
    if any(i.code == "scope-required" for i in plan.issues):
        scope = _answer(
            "Initialize project or global configuration? [project/global, empty cancels] "
        )
        if scope not in {"project", "global"}:
            return options, plan
        options = replace(options, scope="project" if scope == "project" else "global")
    if options.scope == "global" and options.source is None:
        answer = _answer(f"Directory to hold the global source [{options.root}]: ")
        options = replace(
            options, source=Path(answer).expanduser().absolute() if answer else options.root
        )
    plan = _plan(options)
    if any(i.code == "agents-required" for i in plan.issues):
        answer = _answer(
            "Configured harnesses (comma-separated: claude,codex,opencode,pi; empty cancels): "
        )
        if not answer:
            return options, plan
        options = replace(options, agents=tuple(a.strip() for a in answer.split(",")))
        plan = _plan(options)
    while plan.issues:
        for issue in plan.issues:
            print(f"{issue.code}: {issue.message}", file=sys.stderr)
            for path in issue.paths:
                print(f"  {path}", file=sys.stderr)
        answer = _answer("Resolution: mapping JSON or selection JSON (empty leaves unresolved): ")
        if not answer:
            break
        options = _resolution(options, answer)
        plan = _plan(options)
    if (
        plan.complete
        and not plan.already_initialized
        and plan.inventory.scope == "project"
        and options.starter is None
        and not options.yes
    ):
        answer = _answer("Optional project starter [none/frontend/backend; default none]: ")
        options = replace(options, starter=answer or "none")
        plan = _plan(options)
    return options, plan


def _resolution(options: InitOptions, answer: str) -> InitOptions:
    try:
        resolution = json.loads(answer)
    except ValueError as error:
        raise UsageError("resolution must be a JSON object") from error
    if isinstance(resolution, dict) and "agents" in resolution:
        return replace(options, mappings=(*options.mappings, parse_mapping(answer)))
    return replace(options, selections=(*options.selections, parse_selection(answer)))


def _registration(
    options: InitOptions, plan: MigrationPlan
) -> tuple[SourceWrite | None, Issue | None]:
    if plan.inventory.scope != "global" or options.registration == "keep":
        return None, None
    path = machine_config_path().absolute()
    source = plan.inventory.source_root.resolve()
    if path.exists() or path.is_symlink():
        same = False
        if not path.is_symlink() and path.is_file():
            try:
                data = tomllib.loads(path.read_text(encoding="utf-8"))
                raw = data.get("source")
                same = isinstance(raw, str) and Path(raw).expanduser().resolve() == source
            except (ValueError, OSError):
                pass
        if same:
            return None, None
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
    return SourceWrite(path, tomlkit.dumps({"source": str(source)}).encode(), 0o600, True), None


def _show(preview: dict[str, Any], *, as_json: bool) -> None:
    if as_json:
        print(json.dumps(preview, indent=2))
        return
    print(
        f"Scope: {preview['scope'] or 'unresolved'}; agents: {', '.join(preview['agents']) or 'unresolved'}"
    )
    print(f"Source: {preview['source_root']}")
    print(f"Starter: {preview['starter']}")
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
    if preview.get("registration"):
        print(f"Register global source: {preview['registration']}")
    if preview.get("git_hooks"):
        show_hooks(preview["git_hooks"])
    for issue in preview["issues"]:
        print(f"{issue['code']}: {issue['message']}", file=sys.stderr)
        for path in issue["paths"]:
            print(f"  {path}", file=sys.stderr)


def _result(result: MigrationResult, *, as_json: bool) -> None:
    data = {
        "status": "complete",
        "journal": str(result.journal) if result.journal else None,
        "baseline": result.baseline,
        "written": [str(p) for p in result.written],
        "staged": list(result.staged),
        "already_initialized": result.already_initialized,
    }
    if as_json:
        print(json.dumps(data, indent=2))
    elif result.already_initialized:
        print(
            "Existing source adopted; no migration needed. Use loadout check/sync for managed outputs."
        )
    else:
        print(
            f"Migration complete; {len(result.staged)} source paths staged. Recovery journal: {result.journal}"
        )


def run_init(options: InitOptions) -> int:
    try:
        return _run_init(options)
    except _Cancelled:
        print("cancelled; no changes made")
        return 0


def _run_init(options: InitOptions) -> int:
    if options.scope == "project" and options.source is not None:
        raise UsageError("--source selects a global directory; use --root for project scope")
    options, plan = _resolve(options)
    machine, issue = _registration(options, plan)
    if issue:
        plan = replace(plan, issues=(*plan.issues, issue))
    if not plan.complete:
        _show(plan.preview(), as_json=options.json)
        return 2
    hooks = _hooks(options, plan)
    prepared = prepare_migration(plan, machine_write=machine, hooks=hooks)
    preview = {**prepared.preview(), "registration": str(machine.path) if machine else None}
    if options.dry_run or not options.json:
        _show(preview, as_json=options.json)
    if options.dry_run:
        return 0
    if (prepared.operations or prepared.git) and not options.yes:
        if options.json or not sys.stdin.isatty():
            raise UsageError("init requires --yes after reviewing init --dry-run; no changes made")
        if _answer(
            "Apply this migration, including its Git checkpoint and final staging? [y/N] "
        ).lower() not in {"y", "yes"}:
            print("declined; no changes made")
            return 0
    try:
        _result(apply_migration(prepared), as_json=options.json)
    except MigrationFailure as error:
        return report_failure(error, as_json=options.json)
    if (
        plan.inventory.scope == "project"
        and not options.json
        and not machine_config_path().is_file()
    ):
        print("Global scope is not configured; use loadout init --global when needed.")
    return 0


def _hooks(options: InitOptions, plan: MigrationPlan) -> HookPlan | None:
    if options.git_hooks not in {None, "none", "check", "regenerate"}:
        raise UsageError("--git-hooks must be none, check or regenerate")
    if options.git_hooks not in {"check", "regenerate"}:
        return None
    root = plan.inventory.source_root if plan.inventory.scope == "global" else plan.inventory.root
    initialize = (
        git(plan.inventory.root, "rev-parse", "--show-toplevel", check=False).returncode != 0
    )
    profile = "default"
    if plan.inventory.scope == "global" and plan.already_initialized:
        machine = load_machine_config(machine_config_path())
        if machine is not None and machine.source == root.resolve():
            profile = machine.profile or "default"
    return plan_hooks(
        root,
        regenerate=options.git_hooks == "regenerate",
        profile=profile,
        initialize=plan.inventory.root if initialize else None,
    )


def report_failure(error: MigrationFailure, *, as_json: bool) -> int:
    if as_json:
        print(json.dumps({"status": "interrupted", "journal": str(error.journal)}))
    print(f"loadout: {error}", file=sys.stderr)
    print(
        f"Resume: loadout init --resume {shlex.quote(str(error.journal))} --yes\nRecover: loadout init --recover {shlex.quote(str(error.journal))} --yes",
        file=sys.stderr,
    )
    return 1


def run_recovery(path: Path, *, recover: bool, yes: bool, as_json: bool) -> int:
    action = "recover" if recover else "resume"
    if not yes:
        if as_json or not sys.stdin.isatty():
            raise UsageError(f"init --{action} requires --yes")
        try:
            answer = _answer(f"{action.capitalize()} migration journal {path}? [y/N] ")
        except _Cancelled:
            answer = ""
        if answer.lower() not in {"y", "yes"}:
            print("declined; no changes made")
            return 0
    try:
        if not recover:
            _result(resume_migration(path), as_json=as_json)
            return 0
        result = recover_migration(path)
    except MigrationFailure as error:
        return report_failure(error, as_json=as_json)
    data = {
        "status": "recovery-conflicts" if result.conflicts else "recovered",
        "journal": str(result.journal),
        "conflicts": [str(p) for p in result.conflicts],
        "baseline": result.baseline,
    }
    print(
        json.dumps(data, indent=2)
        if as_json
        else f"{data['status']}: {path}; successful baseline retained"
    )
    for conflict in result.conflicts:
        print(f"Recovery conflict: {conflict}", file=sys.stderr)
    return 1 if result.conflicts else 0
