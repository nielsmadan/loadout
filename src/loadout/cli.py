from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path

from .commands import (
    cmd_check,
    cmd_explain,
    cmd_harness_add,
    cmd_skill_install,
    cmd_skill_status,
    cmd_skill_uninstall,
    cmd_sync,
    cmd_template_add,
    cmd_template_list,
    cmd_template_sync,
    cmd_template_vendor,
)
from .discovery import project_root
from .errors import LoadoutError, UsageError
from .git_hook_commands import run_hook
from .git_hooks import EVENTS, install_hooks
from .init_options import InitOptions, parse_mapping, parse_selection
from .init_workflow import run_init, run_recovery
from .machine import load_machine_config, machine_config_path
from .staged import check_staged


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loadout")
    subparsers = parser.add_subparsers(dest="command")

    def add_root(sub: argparse.ArgumentParser, *, allow_global: bool = False) -> None:
        target = sub.add_mutually_exclusive_group() if allow_global else sub
        target.add_argument(
            "--root",
            type=Path,
            default=Path.cwd(),
            help="repository root holding loadout.toml (default: cwd)",
        )
        if allow_global:
            target.add_argument(
                "--global",
                dest="use_global",
                action="store_true",
                help="use this machine's configured global source instead of --root",
            )

    for name, help_text in (
        ("sync", "regenerate every generated file under the repo root"),
        ("check", "exit 1 if any generated file has drifted"),
    ):
        sub = subparsers.add_parser(name, help=help_text)
        add_root(sub, allow_global=True)
        sub.add_argument(
            "--profile",
            default=None,
            help="active profile to render (default: 'default', or the machine "
            "config's profile under --global)",
        )
        if name == "sync":
            sub.add_argument(
                "--force",
                action="store_true",
                help="overwrite generated files that were modified outside loadout",
            )
        else:
            sub.add_argument(
                "--staged", action="store_true", help="validate the isolated Git index"
            )

    explain = subparsers.add_parser("explain", help="show where a fragment comes from")
    explain.add_argument("name", help="fragment name, optionally qualified as source/name")
    add_root(explain)

    init = subparsers.add_parser(
        "init", help="adopt existing agent configuration into editable loadout source"
    )
    init.add_argument(
        "--harness",
        dest="harnesses",
        action="append",
        default=None,
        help="configured harness (repeatable); otherwise detect configured roots",
    )
    scope = init.add_mutually_exclusive_group()
    scope.add_argument("--project", action="store_true", help="adopt project configuration")
    scope.add_argument(
        "--global",
        dest="use_global",
        action="store_true",
        help="adopt global configuration and register its source on this machine",
    )
    init.add_argument(
        "--source",
        type=Path,
        default=None,
        help=(
            "[--global] existing global source root, or directory to hold a new source; "
            "default: cwd"
        ),
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="alias for --registration replace; never resolves source conflicts",
    )
    init.add_argument(
        "--mapping",
        action="append",
        default=[],
        help="root mapping JSON: source, destination, agents; optional kind/category/destination_template",
    )
    init.add_argument(
        "--select-source",
        action="append",
        default=[],
        help="conflicting-copy selection JSON: destination, source",
    )
    init.add_argument(
        "--dry-run",
        action="store_true",
        help="preview without changing source, Git or destinations",
    )
    init.add_argument("--json", action="store_true", help="emit machine-readable preview or result")
    init.add_argument(
        "--yes",
        action="store_true",
        help="approve a fully resolved migration and its Git operations",
    )
    _add_init_choices(init)
    add_root(init)

    _add_git_hooks(
        subparsers.add_parser("git-hooks", help="optional repository-local Git integration")
    )

    harness = subparsers.add_parser("harness", help="manage this project's enabled harnesses")
    harness_subparsers = harness.add_subparsers(dest="harness_command")
    harness_add = harness_subparsers.add_parser(
        "add", help="enable an additional harness for this project"
    )
    harness_add.add_argument("name", help="harness to enable")
    add_root(harness_add)

    template = subparsers.add_parser("template", help="manage this project's templates")
    template_subparsers = template.add_subparsers(dest="template_command")
    for sub_name, sub_help in (
        ("list", "show each declared template and how it resolves"),
        ("add", "declare a template, leaving it to resolve from a source"),
        ("vendor", "copy a template into this project and record its content hash"),
        ("sync", "update the vendored copy from its source"),
    ):
        template_sub = template_subparsers.add_parser(sub_name, help=sub_help)
        if sub_name != "list":
            template_sub.add_argument("name", help="template name")
        add_root(template_sub)

    skill = subparsers.add_parser("skill", help="manage the bundled loadout skill")
    skill_subparsers = skill.add_subparsers(dest="skill_command")
    for sub_name, sub_help in (
        ("install", "vendor the bundled skill into a global loadout source and sync"),
        ("status", "show the bundled skill's global source state"),
        ("uninstall", "remove the owned source copy and its generated outputs"),
    ):
        skill_sub = skill_subparsers.add_parser(sub_name, help=sub_help)
        skill_sub.add_argument(
            "--profile",
            default=None,
            help="global profile whose sources and configured agents to use",
        )
        skill_sub.add_argument(
            "--source",
            default=None,
            help="global source to hold the skill when more than one offers skills",
        )
        if sub_name != "status":
            skill_sub.add_argument(
                "--yes",
                action="store_true",
                help="apply the displayed source change without confirmation",
            )

    return parser


def _resolve_root_and_profile(args: argparse.Namespace) -> tuple[Path, str]:
    """Precedence for profile: explicit --profile > machine config's profile > 'default'."""
    if getattr(args, "use_global", False):
        config_path = machine_config_path()
        config = load_machine_config(config_path)
        if config is None:
            raise LoadoutError(
                f"no machine config at {config_path}; run `loadout init --global` first"
            )
        return config.source, args.profile or config.profile or "default"
    return args.root.resolve(), args.profile or "default"


def _add_git_hooks(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="git_hook_command", required=True)
    install = commands.add_parser("install", help="install only safely absent local hooks")
    install.add_argument("--regenerate", action="store_true")
    install.add_argument("--dry-run", action="store_true")
    run = commands.add_parser("run", help="integrate Loadout into an existing Git hook")
    run.add_argument("event", choices=EVENTS)
    run.add_argument("git_arguments", nargs="*")
    for command in (install, run):
        command.add_argument("--root", type=Path, default=Path.cwd())
        command.add_argument("--profile", default="default")


def _resolve_global(profile: str | None) -> tuple[Path, str]:
    config_path = machine_config_path()
    config = load_machine_config(config_path)
    if config is None:
        raise LoadoutError(f"no machine config at {config_path}; run `loadout init --global` first")
    return config.source, profile or config.profile or "default"


def _dispatch_template(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    if args.template_command == "list":
        return cmd_template_list(root)
    if args.template_command == "add":
        return cmd_template_add(root, args.name)
    if args.template_command == "vendor":
        return cmd_template_vendor(root, args.name)
    return cmd_template_sync(root, args.name)


def _add_init_choices(init: argparse.ArgumentParser) -> None:
    init.add_argument(
        "--starter",
        choices=("none", "frontend", "backend"),
        help="optional project template to vendor; default: none",
    )
    init.add_argument(
        "--git-hooks",
        choices=("none", "check", "regenerate"),
        help="opt into pre-commit validation, optionally with post-checkout/post-merge sync",
    )
    init.add_argument(
        "--registration",
        choices=("keep", "replace"),
        help="resolve a conflicting global machine registration",
    )
    recovery = init.add_mutually_exclusive_group()
    recovery.add_argument("--resume", type=Path, help="resume a protected migration journal")
    recovery.add_argument(
        "--recover",
        type=Path,
        help="restore unchanged transaction postimages; keep successful baseline",
    )


def _dispatch_init(args: argparse.Namespace) -> int:
    if args.resume or args.recover:
        if (
            args.dry_run
            or args.project
            or args.use_global
            or args.source
            or args.harnesses
            or args.mapping
            or args.select_source
            or args.registration
            or args.force
            or args.starter
            or args.git_hooks
        ):
            raise UsageError("--resume/--recover accept only --yes and --json")
        return run_recovery(
            args.resume or args.recover, recover=bool(args.recover), yes=args.yes, as_json=args.json
        )
    if args.force and args.registration == "keep":
        raise UsageError("--force conflicts with --registration keep")
    return run_init(
        InitOptions(
            root=args.root.expanduser().absolute(),
            scope="global" if args.use_global else "project" if args.project else None,
            source=args.source.expanduser().absolute() if args.source else None,
            agents=tuple(args.harnesses or ()),
            mappings=tuple(parse_mapping(value) for value in args.mapping),
            selections=tuple(parse_selection(value) for value in args.select_source),
            registration="replace" if args.force else args.registration,
            dry_run=args.dry_run,
            json=args.json,
            yes=args.yes,
            starter=args.starter,
            git_hooks=args.git_hooks,
        )
    )


def _dispatch_skill(args: argparse.Namespace) -> int:
    root, profile = _resolve_global(args.profile)
    if args.skill_command == "status":
        return cmd_skill_status(root, profile, args.source)
    if args.skill_command == "install":
        return cmd_skill_install(root, profile, args.source, yes=args.yes)
    return cmd_skill_uninstall(root, profile, args.source, yes=args.yes)


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "git-hooks":
        return _dispatch_git_hooks(args)
    if args.command == "explain":
        return cmd_explain(args.root.resolve(), args.name)
    if args.command == "init":
        return _dispatch_init(args)
    if args.command == "harness":
        return cmd_harness_add(project_root(args.root), args.name)
    if args.command in {"skill", "template"}:
        return _dispatch_skill(args) if args.command == "skill" else _dispatch_template(args)
    return _dispatch_outputs(args)


def _dispatch_git_hooks(args: argparse.Namespace) -> int:
    if args.git_hook_command == "install":
        return install_hooks(
            args.root.resolve(),
            regenerate=args.regenerate,
            profile=args.profile,
            dry_run=args.dry_run,
        )
    return run_hook(args.event, args.root.resolve(), args.profile, args.git_arguments)


def _dispatch_outputs(args: argparse.Namespace) -> int:
    root, profile = _resolve_root_and_profile(args)
    if args.command == "sync":
        return cmd_sync(root, profile=profile, force=args.force)
    if args.staged:
        return check_staged(root, profile)
    return cmd_check(root, profile=profile)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(file=sys.stderr)
        return 2
    if (
        (args.command == "harness" and args.harness_command != "add")
        or (args.command == "template" and args.template_command is None)
        or (args.command == "skill" and args.skill_command is None)
    ):
        parser.print_usage(file=sys.stderr)
        return 2
    # Notices and drift reports carry the same em dashes the fragments do, and
    # `print` would raise UnicodeEncodeError under a POSIX/C locale. loadout opens
    # every file as UTF-8 explicitly; its own output gets the same treatment.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        return _dispatch(args)
    except UsageError as error:
        _init_error_json(args, 2)
        print(f"loadout: {error}", file=sys.stderr)
        return 2
    except LoadoutError as error:
        _init_error_json(args, 3)
        print(f"loadout: {error}", file=sys.stderr)
        return 3
    except Exception:
        traceback.print_exc()
        return 4


def _init_error_json(args: argparse.Namespace, code: int) -> None:
    if args.command == "init" and args.json:
        print(json.dumps({"status": "error", "complete": False, "exit_code": code}))
