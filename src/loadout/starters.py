from __future__ import annotations

import shlex
import stat
import tempfile
from dataclasses import replace
from pathlib import Path

from .bundled_templates import BUNDLED, STARTERS
from .discovery import credential_material, path_preconditions
from .errors import LoadoutError
from .migration_git import ignored_original, privacy_policy
from .migration_models import Inventory, Issue, MigrationPlan, SourceWrite
from .migration_paths import entry_path
from .output import no_symlinks as _no_symlinks
from .permissions.rules import parse_rules
from .project import project_config_path
from .resolve import ResolvedItem
from .template_catalog import load_catalog
from .templates import copy_tree, declare, record_hash, resolve_template, template_files, tree_hash


def starter_categories(inventory: Inventory, name: str | None) -> frozenset[str]:
    if inventory.scope != "project" or name not in STARTERS:
        return frozenset()
    assert name is not None
    found = resolve_template(name, inventory.root)
    _public_starter(found)
    if not found.path.is_file():
        instructions = found.path / "instructions.md"
        return frozenset({"instructions"}) if instructions.is_file() else frozenset()
    catalog = load_catalog(found.path)
    categories = {category for category, _ in catalog.parts}
    for path in catalog.paths("permissions"):
        rules = parse_rules(path)
        if rules.mcp_allow or rules.mcp_ask or rules.mcp_deny:
            categories.update(("mcp", "mcp-permissions"))
    return frozenset(categories)


def select_starter(plan: MigrationPlan, name: str | None) -> MigrationPlan:
    if name in {None, "none"}:
        return plan
    plan = replace(plan, starter=name)
    if name not in STARTERS:
        raise LoadoutError(f"unknown starter {name!r}; choose none, frontend or backend")
    if plan.already_initialized:
        root = shlex.quote(str(plan.inventory.root))
        return _issue(
            plan,
            "starter-existing-source",
            "Existing source is already initialized. Run "
            f"`loadout template add {name} --vendored --root {root}` "
            f"(or `loadout template sync {name} --root {root}` if already vendored), "
            f"then `loadout sync --root {root}`.",
        )
    if plan.issues:
        return plan
    try:
        return _select(plan, name)
    except (LoadoutError, OSError, ValueError) as error:
        return _issue(plan, "starter-selection", str(error))


def _issue(plan: MigrationPlan, code: str, message: str) -> MigrationPlan:
    return replace(
        plan,
        validated=False,
        issues=(*plan.issues, Issue(code, (plan.inventory.source_root,), message)),
    )


def _select(plan: MigrationPlan, name: str) -> MigrationPlan:
    found = resolve_template(name, plan.inventory.root)
    _public_starter(found)
    states = {state.path: state for state in plan.preconditions}
    file_root = found.path.parent if found.path.is_file() else found.path
    files = tuple(file_root / relative for relative in template_files(found.path))
    directories = tuple(
        dict.fromkeys(
            (file_root, *(p for path in files for p in path.parents if p.is_relative_to(file_root)))
        )
    )
    directories = (*directories, *(p for p in found.path.rglob("*") if p.is_dir()))
    for path in (*directories, *files):
        for state in path_preconditions(path):
            previous = states.get(state.path)
            if previous is None or previous.digest is None:
                states[state.path] = state
    dependencies = tuple(entry_path(p) for p in files) if found.source != BUNDLED else ()
    policy = privacy_policy(plan.inventory.root.resolve(), dependencies)
    with tempfile.TemporaryDirectory(prefix="loadout-starter-") as temporary:
        root = Path(temporary).resolve()
        source_root = root / "loadout"
        for write in plan.source_writes:
            path = source_root / write.path.relative_to(plan.inventory.source_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(write.content)
            path.chmod(write.mode)
        local = source_root / "templates" / (name + ".toml" if found.path.is_file() else name)
        copy_tree(found.path, local)
        declare(root, name)
        content_hash = tree_hash(local)
        record_hash(root, name, content_hash)
        writes = {write.path: write for write in plan.source_writes}
        local_root = local.parent if local.is_file() else local
        for path in (project_config_path(root), *(local_root / p for p in template_files(local))):
            target = plan.inventory.source_root / path.relative_to(source_root)
            if target not in writes and (target.exists() or target.is_symlink()):
                raise LoadoutError(
                    f"starter source already exists: {target}; resolve its ownership first"
                )
            writes[target] = SourceWrite(
                target, path.read_bytes(), stat.S_IMODE(path.stat().st_mode)
            )
            for state in path_preconditions(target):
                states.setdefault(state.path, state)
    selected = replace(
        plan,
        source_writes=tuple(writes.values()),
        preconditions=tuple(states.values()),
        starter_dependencies=dependencies,
        starter_privacy_policy=policy,
        generated_writes=(),
        validated=False,
        notes=(
            *plan.notes,
            (
                f"Vendor starter {name!r} from {found.source}, provenance {content_hash}. "
                "Template instructions precede the original bodies on opted-in routes; source bytes and modes are preserved."
            ),
        ),
    )
    return selected


def _public_starter(template: ResolvedItem) -> None:
    for relative in template_files(template.path):
        base = template.path.parent if template.path.is_file() else template.path
        path = base / relative
        _no_symlinks(path, base)
        private = template.source != BUNDLED and (
            ignored_original(path) or ".local." in path.name or path.name.endswith(".local")
        )
        format_name = path.suffix.removeprefix(".")
        if private or credential_material(path.read_bytes(), format_name):
            raise LoadoutError(
                f"starter {template.name!r} contains private or credential-bearing source at {relative}. "
                "Provide a public template without private material; keep private configuration in "
                "the project's local artifact sources."
            )
