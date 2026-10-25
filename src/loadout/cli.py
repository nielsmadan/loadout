from __future__ import annotations

import argparse
import json
import sys
import traceback
from pathlib import Path
from typing import NoReturn

from . import __version__
from .commands import (
    cmd_check,
    cmd_skill_install,
    cmd_skill_status,
    cmd_skill_uninstall,
    cmd_sync,
    cmd_template_add,
    cmd_template_list,
    cmd_template_sync,
)
from .completions import (
    SHELLS,
    install_completions,
    resolve_shell,
    status_completions,
    uninstall_completions,
)
from .emit import refuse_unregistered_global
from .errors import LoadoutError, UsageError
from .git_hook_commands import run_hook
from .git_hooks import EVENTS, install_hooks, status_hooks, uninstall_hooks
from .harnesses import KNOWN_HARNESSES
from .init_options import InitOptions
from .init_workflow import run_init
from .machine import load_machine_config, machine_config_path, profile_for, resolve_profile
from .project import project_config_path
from .skill_copy import cmd_skill_copy
from .staged import check_staged


class _ArgumentError(Exception):
    def __init__(self, usage: str, message: str) -> None:
        super().__init__(message)
        self.usage = usage


class _Parser(argparse.ArgumentParser):
    """Raises rather than exiting, so `--json` can still answer with its one object."""

    def error(self, message: str) -> NoReturn:
        raise _ArgumentError(self.format_usage(), message)


def build_parser() -> argparse.ArgumentParser:
    parser = _Parser(
        prog="loadout",
        description="Render one source of truth into configuration for your AI coding "
        "harnesses, and keep the generated files in sync.",
    )
    parser.add_argument("--version", action="version", version=f"loadout {__version__}")
    subparsers = parser.add_subparsers(dest="command")

    def add_root(sub: argparse.ArgumentParser, *, allow_global: bool = False) -> None:
        target = sub.add_mutually_exclusive_group() if allow_global else sub
        target.add_argument(
            "--root",
            type=Path,
            default=Path.cwd(),
            help="source root: the folder holding loadout.toml, or a project root holding loadout/ (default: cwd)",
        )
        if allow_global:
            target.add_argument(
                "--global",
                dest="use_global",
                action="store_true",
                help="use this machine's configured global source instead of --root",
            )

    for name, help_text in (
        (
            "sync",
            (
                "regenerate a project's generated files, or this machine's with --global; "
                "preview with `loadout check`"
            ),
        ),
        (
            "check",
            "exit 1 if a project's generated files, or this machine's with --global, have drifted",
        ),
    ):
        sub = subparsers.add_parser(name, help=help_text, description=help_text)
        add_root(sub, allow_global=True)
        sub.add_argument(
            "--profile",
            default=None,
            help="active profile to render (default: 'default', or the machine "
            "config's profile at the registered source)",
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
            sub.add_argument(
                "--json", action="store_true", help="emit each out-of-sync path as JSON"
            )

    init = subparsers.add_parser(
        "init", help="adopt existing agent configuration into editable loadout source"
    )
    init_subparsers = init.add_subparsers(
        dest="init_command",
        required=True,
        metavar="{project,global}",
    )
    _add_init_project(init_subparsers.add_parser("project", help="adopt project configuration"))
    _add_init_global(
        init_subparsers.add_parser(
            "global", help="adopt global configuration and register its source on this machine"
        )
    )

    template = subparsers.add_parser("template", help="manage this project's templates")
    template_subparsers = template.add_subparsers(dest="template_command")
    add_template = template_subparsers.add_parser(
        "add", help="choose a template and how this project uses it"
    )
    add_template.add_argument(
        "name", nargs="?", help="template name; choose interactively when omitted"
    )
    add_mode = add_template.add_mutually_exclusive_group()
    add_mode.add_argument(
        "--tracked",
        dest="template_mode",
        action="store_const",
        const="tracked",
        help="resolve the template from a source on every render",
    )
    add_mode.add_argument(
        "--vendored",
        dest="template_mode",
        action="store_const",
        const="vendored",
        help="copy the template into this project and record its content hash",
    )
    add_root(add_template)
    add_template.add_argument(
        "--yes",
        action="store_true",
        help="apply a change to shared template parts without a prompt",
    )
    for sub_name, sub_help in (
        ("list", "show each declared template, its mode and how it resolves"),
        ("sync", "update the vendored copy from its source"),
    ):
        template_sub = template_subparsers.add_parser(sub_name, help=sub_help)
        add_root(template_sub)
        if sub_name == "sync":
            template_sub.add_argument("name", help="template name")
            template_sub.add_argument(
                "--yes",
                action="store_true",
                help="apply a change to shared template parts without a prompt",
            )

    integrate = subparsers.add_parser(
        "integrate", help="wire loadout into another system so it runs automatically"
    )
    integrate_subparsers = integrate.add_subparsers(dest="integrate_command")
    skill_help = "manage the bundled loadout skill"
    _add_skill(integrate_subparsers.add_parser("skill", help=skill_help, description=skill_help))
    _add_git_hooks(
        integrate_subparsers.add_parser(
            "git-hooks", help="optional repository-local Git integration"
        )
    )
    _add_completions(
        integrate_subparsers.add_parser("completions", help="shell tab completion for loadout")
    )

    # The generated hook scripts invoke this; nobody types it. Passing help=SUPPRESS
    # renders a literal "==SUPPRESS==" row, so omit help to drop the row and set the
    # metavar to keep the name out of the command list as well.
    hook_event = subparsers.add_parser("hook-event")
    hook_event.add_argument("event", choices=EVENTS)
    hook_event.add_argument("git_arguments", nargs="*")
    hook_event.add_argument(
        "--root", type=Path, default=Path.cwd(), help="loadout source root the hook runs against"
    )
    hook_event.add_argument(
        "--profile",
        default=None,
        help="active profile to render (default: resolved when the hook runs, as for sync)",
    )
    subparsers.metavar = "{" + ",".join(n for n in subparsers.choices if n != "hook-event") + "}"

    return parser


def _add_skill(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="skill_command")
    for sub_name, sub_help in (
        ("install", "vendor the bundled skill into a global loadout source and sync"),
        ("status", "show the bundled skill's global source state"),
        ("uninstall", "remove the owned source copy and its generated outputs"),
    ):
        skill_sub = commands.add_parser(sub_name, help=sub_help)
        skill_sub.add_argument(
            "--profile",
            default=None,
            help="global profile whose sources and configured harnesses to use",
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
    copy = commands.add_parser(
        "copy",
        help="copy the skill into each harness's user skills folder, outside any source",
    )
    copy.add_argument(
        "--harness",
        dest="harnesses",
        action="append",
        required=True,
        choices=sorted(KNOWN_HARNESSES),
        help="harness to copy the skill for (repeatable)",
    )


def _resolve_root_and_profile(args: argparse.Namespace) -> tuple[Path, str]:
    if getattr(args, "use_global", False):
        return _resolve_global(args.profile)
    root = args.root.resolve()
    return root, resolve_profile(root, args.profile)


def _add_git_hooks(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="git_hook_command", required=True)
    install = commands.add_parser("install", help="install only safely absent local hooks")
    install.add_argument(
        "--regenerate",
        action="store_true",
        help="also install the post-checkout and post-merge sync hooks, and rewrite "
        "out-of-date hooks loadout wrote",
    )
    install.add_argument("--dry-run", action="store_true", help="preview without writing any hook")
    status = commands.add_parser("status", help="show which hooks are installed and whose they are")
    uninstall = commands.add_parser("uninstall", help="remove only the hooks loadout wrote")
    uninstall.add_argument(
        "--yes", action="store_true", help="remove the displayed hooks without confirmation"
    )
    for command in (install, status, uninstall):
        command.add_argument(
            "--root",
            type=Path,
            default=Path.cwd(),
            help="source root: the folder holding loadout.toml, or a project root holding loadout/ (default: cwd)",
        )


def _add_completions(parser: argparse.ArgumentParser) -> None:
    commands = parser.add_subparsers(dest="completions_command", required=True)
    for sub_name, sub_help in (
        ("install", "write the completion script where the shell loads it"),
        ("status", "show whether the installed script is present and current"),
        ("uninstall", "remove the completion script loadout wrote"),
    ):
        commands.add_parser(sub_name, help=sub_help).add_argument(
            "--shell",
            choices=SHELLS,
            help="shell to manage (default: the shell running loadout)",
        )


def _dispatch_completions(args: argparse.Namespace) -> int:
    shell = resolve_shell(args.shell)
    action = {
        "install": install_completions,
        "status": status_completions,
        "uninstall": uninstall_completions,
    }[args.completions_command]
    return action(shell, build_parser())


def _resolve_global(profile: str | None) -> tuple[Path, str]:
    config_path = machine_config_path()
    config = load_machine_config(config_path)
    if config is None:
        raise LoadoutError(f"no machine config at {config_path}; run `loadout init global` first")
    return config.source, profile_for(config.source, profile, config)


TEMPLATE_OUTSIDE_PROJECT = (
    "template commands manage a project's templates; to author one, add a "
    "`templates/<name>.toml` manifest to your global source. To see the templates your "
    "sources offer, run `loadout template add` with no name in a project."
)


def _dispatch_template(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    if not project_config_path(root).is_file():
        raise LoadoutError(TEMPLATE_OUTSIDE_PROJECT)
    refuse_unregistered_global(root)
    if args.template_command == "list":
        return cmd_template_list(root)
    if args.template_command == "add":
        return cmd_template_add(root, args.name, args.template_mode, yes=args.yes)
    return cmd_template_sync(root, args.name, yes=args.yes)


def _add_init_choices(init: argparse.ArgumentParser) -> None:
    init.add_argument(
        "--harness",
        dest="harnesses",
        action="append",
        choices=sorted(KNOWN_HARNESSES),
        default=None,
        help="configured harness (repeatable); otherwise use machine defaults, then detection",
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


def _add_init_project(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="source root: the folder holding loadout.toml, or a project root holding loadout/ (default: cwd)",
    )
    _add_init_choices(parser)


def _add_init_global(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=None,
        help="existing global source root, or directory to hold a new source (default: cwd)",
    )
    parser.add_argument(
        "--registration",
        choices=("keep", "replace"),
        help="resolve a conflicting global machine registration",
    )
    _add_init_choices(parser)


def _dispatch_init(args: argparse.Namespace) -> int:
    if args.init_command == "global":
        source = (
            args.directory.expanduser().absolute()
            if args.directory
            else Path.cwd().expanduser().absolute()
        )
        return run_init(
            InitOptions(
                root=source,
                scope="global",
                harnesses=tuple(args.harnesses or ()),
                registration=args.registration,
                dry_run=args.dry_run,
                json=args.json,
                yes=args.yes,
            )
        )
    return run_init(
        InitOptions(
            root=args.root.expanduser().absolute(),
            scope="project",
            harnesses=tuple(args.harnesses or ()),
            dry_run=args.dry_run,
            json=args.json,
            yes=args.yes,
        )
    )


def _dispatch_skill(args: argparse.Namespace) -> int:
    if args.skill_command == "copy":
        return cmd_skill_copy(tuple(args.harnesses))
    root, profile = _resolve_global(args.profile)
    if args.skill_command == "status":
        return cmd_skill_status(root, profile, args.source)
    if args.skill_command == "install":
        return cmd_skill_install(root, profile, args.source, yes=args.yes)
    return cmd_skill_uninstall(root, profile, args.source, yes=args.yes)


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "hook-event":
        root = args.root.resolve()
        profile = resolve_profile(root, args.profile, lenient=True)
        return run_hook(args.event, root, profile, args.git_arguments, args.profile)
    if args.command == "integrate":
        if args.integrate_command == "completions":
            return _dispatch_completions(args)
        return (
            _dispatch_git_hooks(args)
            if args.integrate_command == "git-hooks"
            else _dispatch_skill(args)
        )
    if args.command == "init":
        return _dispatch_init(args)
    if args.command == "template":
        return _dispatch_template(args)
    return _dispatch_outputs(args)


def _dispatch_git_hooks(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    if args.git_hook_command == "install":
        return install_hooks(root, regenerate=args.regenerate, dry_run=args.dry_run)
    if args.git_hook_command == "status":
        return status_hooks(root)
    return uninstall_hooks(root, yes=args.yes)


def _dispatch_outputs(args: argparse.Namespace) -> int:
    root, profile = _resolve_root_and_profile(args)
    if args.command == "sync":
        return cmd_sync(root, profile=profile, force=args.force)
    if args.staged:
        if args.json:
            raise UsageError("--json is not available with --staged")
        return check_staged(root, profile)
    return cmd_check(root, profile=profile, as_json=args.json)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except _ArgumentError as error:
        if _json_requested(sys.argv[1:] if argv is None else argv):
            _print_error_json(2, error)
        sys.stderr.write(error.usage)
        print(f"loadout: error: {error}", file=sys.stderr)
        return 2
    if (
        args.command is None
        or (args.command == "template" and args.template_command is None)
        or (args.command == "integrate" and args.integrate_command is None)
        or (
            args.command == "integrate"
            and args.integrate_command == "skill"
            and args.skill_command is None
        )
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
        _error_json(args, 2, error)
        print(f"loadout: {error}", file=sys.stderr)
        return 2
    except LoadoutError as error:
        _error_json(args, 3, error)
        print(f"loadout: {error}", file=sys.stderr)
        return 3
    except Exception as error:  # noqa: BLE001 - top-level handler reports unexpected errors
        traceback.print_exc()
        _error_json(args, 4, error)
        return 4


def _json_requested(argv: list[str]) -> bool:
    return bool(argv) and argv[0] in {"init", "check"} and "--json" in argv


def _error_json(args: argparse.Namespace, code: int, error: Exception) -> None:
    if args.command in {"init", "check"} and getattr(args, "json", False):
        _print_error_json(code, error)


def _print_error_json(code: int, error: Exception) -> None:
    print(json.dumps({"status": "error", "exit_code": code, "error": str(error)}, indent=2))
