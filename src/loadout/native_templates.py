from __future__ import annotations

import tomllib
from dataclasses import replace
from pathlib import Path

from .artifacts import _no_symlinks, render_artifacts
from .errors import LoadoutError
from .project import TEMPLATE_INSTRUCTIONS, ProjectConfig, load_project_config, project_config_path
from .resolve import ResolvedItem
from .templates import resolve_template, template_files


def native_template_prefix(config: ProjectConfig, templates: tuple[ResolvedItem, ...]) -> bytes:
    blocks = []
    for template in templates:
        validate_template_tree(template.path)
        for relative in template_files(template.path):
            path = template.path / relative
            _no_symlinks(path, template.path)
            content = path.read_bytes()
            if relative == Path(TEMPLATE_INSTRUCTIONS):
                try:
                    text = content.decode("utf-8").strip()
                except UnicodeError as error:
                    raise LoadoutError(
                        f"template {template.name!r}: instructions.md must be UTF-8"
                    ) from error
                if text:
                    blocks.append(text)
            elif not _empty_scaffold(relative, content):
                category = relative.parts[0].removesuffix(".toml")
                raise LoadoutError(
                    f"template {template.name!r}: populated {category} contribution at {relative} "
                    "cannot compose with native artifact routes. Move this contribution into an "
                    "explicit project artifact source and remove it from the template before selecting it."
                )
    if not blocks:
        return b""
    routes = config.artifacts.records if config.artifacts else ()
    consumers = {a for route in routes if route.template_instructions for a in route.agents}
    missing = set(config.harnesses) - consumers
    if missing:
        raise LoadoutError(
            f"template instructions have no opted-in artifact route for {', '.join(sorted(missing))}. "
            "Set template_instructions = true on each intended required project copy/text "
            "instruction route in artifacts.toml, then retry the template command."
        )
    return ("\n\n".join(blocks) + "\n\n").encode()


def validate_template_tree(tree: Path) -> None:
    _no_symlinks(tree, tree)
    for path in tree.rglob("*"):
        _no_symlinks(path, tree)


def _empty_scaffold(relative: Path, content: bytes) -> bool:
    if relative.name == ".gitkeep":
        return not content.strip()
    if relative.as_posix() in {"permissions.toml", "mcp.toml"}:
        try:
            return not tomllib.loads(content.decode())
        except (ValueError, UnicodeError):
            return False
    return False


def validate_template_change(root: Path, name: str, template: ResolvedItem) -> None:
    config = load_project_config(project_config_path(root))
    if config.presets:
        return
    names = tuple(dict.fromkeys((*config.templates, name)))
    config = replace(config, templates=names)
    resolved = tuple(
        template if declared == name else resolve_template(declared, root) for declared in names
    )
    prefix = native_template_prefix(config, resolved)
    if config.artifacts is not None:
        render_artifacts(
            config.artifacts,
            project_root=root,
            source_inputs=(project_config_path(root).parent, *(t.path for t in resolved)),
            instruction_prefix=prefix,
        )
