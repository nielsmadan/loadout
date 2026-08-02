from __future__ import annotations

import argparse
import sys
import traceback
from pathlib import Path

from .commands import cmd_check, cmd_explain, cmd_sync
from .errors import LoadoutError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="loadout")
    subparsers = parser.add_subparsers(dest="command")

    def add_root(sub: argparse.ArgumentParser) -> None:
        sub.add_argument(
            "--root",
            type=Path,
            default=Path.cwd(),
            help="repository root holding loadout.toml (default: cwd)",
        )

    for name, help_text in (
        ("sync", "regenerate every generated file under the repo root"),
        ("check", "exit 1 if any generated file has drifted"),
    ):
        add_root(subparsers.add_parser(name, help=help_text))

    explain = subparsers.add_parser("explain", help="show where a fragment comes from")
    explain.add_argument("name", help="fragment name, optionally qualified as source/name")
    add_root(explain)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command is None:
        parser.print_usage(file=sys.stderr)
        return 2
    root = args.root.resolve()
    try:
        if args.command == "explain":
            return cmd_explain(root, args.name)
        handler = {"sync": cmd_sync, "check": cmd_check}[args.command]
        return handler(root)
    except LoadoutError as error:
        print(f"loadout: {error}", file=sys.stderr)
        return 3
    except Exception:
        traceback.print_exc()
        return 4
