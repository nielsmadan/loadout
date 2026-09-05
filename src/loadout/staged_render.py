from __future__ import annotations

import json
import os
import shlex
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from unittest.mock import patch

from . import artifacts, emit, manifest, templates
from .artifacts import Copied
from .destinations import resolve_destination
from .errors import LoadoutError
from .manifest import (
    InstructionTarget,
    ModuleConfigTarget,
    PermissionTarget,
    SkillsTarget,
    load_profile,
    manifest_path,
)
from .project import load_project_config, project_config_path, project_outputs
from .resolve import ResolvedItem
from .sources import Source, parse_sources


@dataclass
class Snapshot:
    tree: Path
    original: Path
    relative: Path
    environment: dict[str, str]
    gitlinks: tuple[Path, ...]

    @property
    def root(self) -> Path:
        return self.tree / self.relative

    @property
    def destinations(self) -> Path:
        return self.tree.parent / (self.tree.name + "-destinations")

    @property
    def home(self) -> Path:
        return self.tree.parent / (self.tree.name + "-home")

    def inside(self, path: Path) -> None:
        try:
            resolved = path.resolve()
        except (OSError, RuntimeError) as error:
            raise LoadoutError(f"invalid staged symlink: {path}") from error
        if not resolved.is_relative_to(self.tree):
            raise LoadoutError(f"dependency escapes staged tree: {path}; vendor a regular source")

    def dependency(self, path: Path) -> None:
        self.inside(path)
        if any(p.is_relative_to(path) or path.is_relative_to(p) for p in self.gitlinks):
            raise LoadoutError(
                f"submodule dependency is not a staged file tree: {path}; vendor its files"
            )
        for directory, directories, files in os.walk(path, followlinks=False):
            for name in (*directories, *files):
                child = Path(directory) / name
                if child.is_symlink():
                    self.inside(child)

    def audit(self, event: str, args: tuple[Any, ...]) -> None:
        if event not in {"open", "os.listdir", "os.scandir"} or isinstance(args[0], int):
            return
        path = Path(os.fsdecode(args[0])).absolute()
        if not any(
            path.resolve().is_relative_to(p) for p in (self.tree, self.destinations, self.home)
        ):
            raise LoadoutError(f"dependency read outside staged tree: {path}")

    def sources(self, entries: list[dict[str, object]], base: Path) -> tuple[Source, ...]:
        for entry in entries:
            if isinstance(entry, dict) and isinstance(entry.get("path"), str):
                raw = str(entry["path"])
                if Path(raw).is_absolute():
                    raise LoadoutError(f"absolute source path: {raw}; use a staged relative source")
                self.dependency(base / raw)
        return parse_sources(entries, base)

    def destination(self, template: str, label: str) -> Path:
        with patch.dict(os.environ, self.environment, clear=True):
            actual = resolve_destination(template, label)
        return (
            self.tree / actual.relative_to(self.original)
            if actual.is_relative_to(self.original)
            else self.destinations / actual.relative_to(actual.anchor)
        )

    def upstream(self, name: str, config_path: Path | None = None) -> ResolvedItem:
        command = shlex.join(
            ["loadout", "template", "vendor", name, "--root", str(self.original / self.relative)]
        )
        raise LoadoutError(
            f"template {name!r} is not staged; run {command} and stage "
            f"loadout/templates/{name} with loadout/config.toml"
        )

    def project_ownership(self, *, render: bool) -> set[Path]:
        if render:
            self.dependency(project_config_path(self.root).parent)
        self.inside(project_config_path(self.root))
        if not project_config_path(self.root).is_file():
            return set()
        config = load_project_config(project_config_path(self.root))
        if render:
            for name in config.templates:
                path = templates.vendored_path(self.root, name)
                self.dependency(path)
                if not path.is_dir():
                    self.upstream(name)
        return {self.root / path for path in project_outputs(config)}

    def global_ownership(self, profile: str) -> set[Path]:
        self.inside(manifest_path(self.root))
        if not manifest_path(self.root).is_file():
            return set()
        config = load_profile(self.root, profile)
        owned = set()
        targets: tuple[InstructionTarget | PermissionTarget, ...] = (
            *config.targets,
            *config.permissions,
        )
        for target in targets:
            if not emit._selected(target, profile):
                continue
            if target.path is not None:
                owned.add(self.root / str(target.path))
            owned.update(
                self.destination(str(value), "staged ownership") for value in target.destinations
            )
        trees: tuple[SkillsTarget | ModuleConfigTarget, ...] = (
            *config.skills,
            *config.module_config,
        )
        for tree in trees:
            owned.update(
                self.destination(str(value), "staged ownership") for value in tree.destinations
            )
        if config.artifacts:
            owned.update(
                self.destination(record.destination, record.label)
                for record in config.artifacts.records
                if record.destination
            )
        return owned

    def inspect(self, profile: str, *, render: bool) -> tuple[str, ...]:
        sys.addaudithook(self.audit)
        self.inside(self.root)
        with (
            patch.dict(
                os.environ,
                {"HOME": str(self.home), "XDG_CONFIG_HOME": str(self.home / ".config")},
                clear=True,
            ),
            patch.object(manifest, "parse_sources", self.sources if render else lambda *_: ()),
            patch.object(artifacts, "resolve_destination", self.destination),
            patch.object(emit, "resolve_destination", self.destination),
            patch.object(templates, "resolve_upstream_template", self.upstream),
        ):
            owned = self.project_ownership(render=render) | self.global_ownership(profile)
            configured = (
                project_config_path(self.root).is_file() or manifest_path(self.root).is_file()
            )
            if render and configured:
                for path, output in emit.render_all(self.root, profile).items():
                    owned.add(path)
                    if isinstance(output, Copied):
                        output.read_bytes()
                        output.source.stat()
        return tuple(
            sorted(
                str(path.relative_to(self.tree)) for path in owned if path.is_relative_to(self.tree)
            )
        )


def main() -> None:
    try:
        request = json.load(sys.stdin)
        tree = Path(request["snapshot"])
        snapshot = Snapshot(
            tree,
            Path(request["original"]),
            Path(request["relative"]),
            request["environment"],
            tuple(tree / path for path in request["gitlinks"]),
        )
        json.dump(snapshot.inspect(request["profile"], render=request["render"]), sys.stdout)
    except (LoadoutError, OSError, ValueError, RuntimeError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
