from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .commands import (
    cmd_check,
    cmd_explain,
    cmd_harness_add,
    cmd_init,
    cmd_init_global,
    cmd_sync,
    cmd_template_add,
    cmd_template_list,
    cmd_template_sync,
    cmd_template_vendor,
)
from .errors import LoadoutError
from .machine import load_machine_config, machine_config_path


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

    explain = subparsers.add_parser("explain", help="show where a fragment comes from")
    explain.add_argument("name", help="fragment name, optionally qualified as source/name")
    add_root(explain)

    init = subparsers.add_parser(
        "init", help="scaffold loadout/ for this project, or --global for this machine"
    )
    init.add_argument(
        "--harness",
        dest="harnesses",
        action="append",
        default=None,
        help="harness to generate configuration for (repeatable); required unless --global",
    )
    init.add_argument(
        "--global",
        dest="use_global",
        action="store_true",
        help="scaffold this machine's global source instead of a project",
    )
    init.add_argument(
        "--source",
        type=Path,
        default=None,
        help="[--global] directory to hold the new global source; required unless stdin is a TTY",
    )
    init.add_argument(
        "--force",
        action="store_true",
        help="[--global] overwrite an existing machine config",
    )
    add_root(init)

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


def _dispatch_template(args: argparse.Namespace) -> int:
    root = args.root.resolve()
    if args.template_command == "list":
        return cmd_template_list(root)
    if args.template_command == "add":
        return cmd_template_add(root, args.name)
    if args.template_command == "vendor":
        return cmd_template_vendor(root, args.name)
    return cmd_template_sync(root, args.name)


def _dispatch_init(args: argparse.Namespace) -> int:
    if args.use_global:
        return cmd_init_global(args.source, force=args.force)
    return cmd_init(args.root.resolve(), tuple(args.harnesses))


def _dispatch(args: argparse.Namespace) -> int:
    if args.command == "explain":
        return cmd_explain(args.root.resolve(), args.name)
    if args.command == "init":
        return _dispatch_init(args)
    if args.command == "harness":
        return cmd_harness_add(args.root.resolve(), args.name)
    if args.command == "template":
        return _dispatch_template(args)
    root, profile = _resolve_root_and_profile(args)
    if args.command == "sync":
        return cmd_sync(root, profile=profile, force=args.force)
    return cmd_check(root, profile=profile)


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(file=sys.stderr)
        return 2
    if args.command == "harness" and args.harness_command != "add":
        parser.print_usage(file=sys.stderr)
        return 2
    if args.command == "template" and args.template_command is None:
        parser.print_usage(file=sys.stderr)
        return 2
    if args.command == "init":
        if args.use_global and args.harnesses:
            parser.error("argument --global: not allowed with argument --harness")
        if not args.use_global and not args.harnesses:
            parser.error("the following arguments are required: --harness")
    try:
        return _dispatch(args)
    except LoadoutError as error:
        print(f"loadout: {error}", file=sys.stderr)
        return 3
    except Exception:
        traceback.print_exc()
        return 4
