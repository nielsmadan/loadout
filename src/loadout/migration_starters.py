from __future__ import annotations

import shlex
import stat
import tempfile
from dataclasses import replace
from pathlib import Path

from .artifacts import _no_symlinks
from .bundled_templates import BUNDLED, STARTERS
from .discovery import credential_material, digest, path_preconditions
from .errors import LoadoutError
from .migration_git import ignored_original, privacy_policy
from .migration_models import ExpectedOutput, Issue, MigrationPlan, SourceWrite
from .migration_paths import entry_path
from .migration_validation import validate_plan
from .native_templates import native_template_prefix, validate_template_tree
from .project import load_project_config, project_config_path
from .resolve import ResolvedItem
from .templates import copy_tree, declare, record_hash, resolve_template, template_files, tree_hash


def select_starter(plan: MigrationPlan, name: str | None) -> MigrationPlan:
    if name in {None, "none"}:
        return plan
    plan = replace(plan, starter=name)
    if name not in STARTERS:
        raise LoadoutError(f"unknown starter {name!r}; choose none, frontend or backend")
    if plan.inventory.scope == "global":
        return _issue(
            plan,
            "starter-scope",
            "Frontend/backend starters apply to project init only. A global source may supply "
            "project templates; select this starter with loadout init --project --starter " + name,
        )
    if plan.already_initialized:
        root = shlex.quote(str(plan.inventory.root))
        return _issue(
            plan,
            "starter-existing-source",
            f"Existing source is already initialized. Run `loadout template vendor {name} --root {root}` "
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
    files = tuple(found.path / relative for relative in template_files(found.path))
    directories = (found.path, *(p for p in found.path.rglob("*") if p.is_dir()))
    for path in (*directories, *files):
        for state in path_preconditions(path):
            previous = states.get(state.path)
            if previous is None or previous.digest is None:
                states[state.path] = state
    dependencies = tuple(entry_path(p) for p in files) if found.source != BUNDLED else ()
    policy = privacy_policy(plan.inventory.root.resolve(), dependencies)
    with tempfile.TemporaryDirectory(prefix="loadout-starter-") as temporary:
        root = Path(temporary)
        source_root = root / "loadout"
        for write in plan.source_writes:
            path = source_root / write.path.relative_to(plan.inventory.source_root)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(write.content)
            path.chmod(write.mode)
        config = load_project_config(project_config_path(root))
        native_template_prefix(config, (found,))
        local = source_root / "templates" / name
        copy_tree(found.path, local)
        declare(root, name)
        content_hash = tree_hash(local)
        record_hash(root, name, content_hash)
        config = load_project_config(project_config_path(root))
        prefix = native_template_prefix(config, (ResolvedItem(name, "(vendored)", local),))
        writes = {write.path: write for write in plan.source_writes}
        for path in (project_config_path(root), *(local / p for p in template_files(local))):
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
        expected = {output.path: output for output in plan.expected_outputs}
        assert config.artifacts is not None
        for route in config.artifacts.records:
            if not route.template_instructions or not prefix:
                continue
            assert route.output is not None
            body = writes[plan.inventory.source_root / route.parts[0].source]
            destination = plan.inventory.root / route.output
            expected[destination] = ExpectedOutput(
                destination, "copy", digest(prefix + body.content), body.mode
            )
    selected = replace(
        plan,
        source_writes=tuple(writes.values()),
        expected_outputs=tuple(expected.values()),
        required_absences=tuple(p for p in plan.required_absences if p not in expected),
        preconditions=tuple(states.values()),
        starter_dependencies=dependencies,
        starter_privacy_policy=policy,
        generated_writes=(),
        validated=False,
        notes=(
            *plan.notes,
            f"Vendor starter {name!r} from {found.source}, provenance {content_hash}. "
            "Template instructions precede the original bodies on opted-in routes; source bytes and modes are preserved.",
        ),
    )
    return validate_plan(selected)


def _public_starter(template: ResolvedItem) -> None:
    validate_template_tree(template.path)
    for relative in template_files(template.path):
        path = template.path / relative
        _no_symlinks(path, template.path)
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
