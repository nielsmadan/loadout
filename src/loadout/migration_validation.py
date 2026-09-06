from __future__ import annotations

import base64
import json
import os
import subprocess
import sys
import tempfile
import traceback
from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import patch

from . import artifacts
from .artifacts import Copied, Merged
from .destinations import resolve_destination
from .discovery import digest
from .emit import render_all
from .errors import LoadoutError
from .migration_models import GeneratedWrite, Issue, MigrationPlan
from .native_documents import key_fingerprints, parse_document


def validate_plan(plan: MigrationPlan) -> MigrationPlan:
    if plan.issues or plan.already_initialized:
        return plan
    try:
        generated = _reconstruct(plan)
        _compare(plan, generated)
    except (LoadoutError, OSError, ValueError, TypeError, KeyError):
        return replace(
            plan,
            validated=False,
            generated_writes=(),
            issues=(
                Issue(
                    "reconstruction-failed",
                    (plan.inventory.source_root,),
                    "Serialized source failed isolated reconstruction or complete output comparison; originals remain untouched.",
                ),
            ),
        )
    return replace(plan, validated=True, generated_writes=generated)


def _reconstruct(plan: MigrationPlan) -> tuple[GeneratedWrite, ...]:
    with tempfile.TemporaryDirectory(prefix="loadout-migration-check-") as scratch_name:
        scratch = Path(scratch_name).resolve()
        root = scratch / "project" if plan.inventory.scope == "project" else scratch / "source"
        source_root = root / "loadout" if plan.inventory.scope == "project" else root
        source_root.mkdir(parents=True)
        for write in plan.source_writes:
            relative = write.path.relative_to(plan.inventory.source_root)
            path = source_root / relative
            path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
            path.write_bytes(write.content)
            path.chmod(write.mode)
        destinations = {
            route: str(scratch / "destinations" / str(i))
            for i, (route, _) in enumerate(plan.artifact_routes)
        }
        reverse = {destinations[route]: path for route, path in plan.artifact_routes}
        environment = {
            "HOME": str(scratch / "home"),
            "XDG_CONFIG_HOME": str(scratch / "home/.config"),
            "PATH": os.defpath,
        }
        script = "import sys; sys.path.insert(0, sys.argv[1]); from loadout.migration_validation import _worker; _worker()"
        result = subprocess.run(
            [sys.executable, "-I", "-c", script, str(Path(__file__).resolve().parents[1])],
            input=json.dumps(
                {
                    "root": str(root),
                    "destinations": destinations,
                    "originals": {route: str(path) for route, path in plan.artifact_routes},
                    "environment": dict(plan.inventory.destination_environment),
                }
            ).encode(),
            capture_output=True,
            check=False,
            cwd=scratch,
            env=environment,
        )
        if result.returncode:
            raise LoadoutError("Isolated migration reconstruction failed")
        decoded = json.loads(result.stdout)
        generated = []
        formats = {output.path: output.format for output in plan.expected_outputs}
        expected = {output.path: output for output in plan.expected_outputs}
        for output in decoded:
            temporary = Path(output["path"])
            if plan.inventory.scope == "project":
                path = plan.inventory.root / temporary.relative_to(root)
            else:
                parent = next(
                    (p for p in (temporary, *temporary.parents) if str(p) in reverse), None
                )
                if parent is None:
                    raise LoadoutError("Unexpected global migration destination")
                path = reverse[str(parent)] / temporary.relative_to(parent)
            wanted = expected.get(path)
            mode = wanted.mode if output["owned"] is not None and wanted else None
            generated.append(
                GeneratedWrite(
                    path,
                    base64.b64decode(output["content"], validate=True),
                    mode if mode is not None else output["mode"],
                    formats.get(path, output["format"]),
                    tuple(output["owned"]) if output["owned"] is not None else None,
                    output["emit_empty"],
                    "preserve-destination" if output["owned"] is not None else "exact",
                )
            )
        return tuple(generated)


def _compare(plan: MigrationPlan, generated: tuple[GeneratedWrite, ...]) -> None:
    expected = {output.path: output for output in plan.expected_outputs}
    outputs = {
        write.path: write
        for write in generated
        if write.owned is None or write.content.strip() not in {b"", b"{}"} or write.emit_empty
    }
    if outputs.keys() != expected.keys() or outputs.keys() & set(plan.required_absences):
        raise LoadoutError("Reconstructed output inventory differs")
    for path, output in outputs.items():
        want = expected[path]
        if output.owned != want.owned:
            raise LoadoutError("Reconstructed native ownership differs")
        if output.mode != want.mode:
            raise LoadoutError("Reconstructed output mode differs")
        if want.format == "copy":
            if digest(output.content) != want.digest or output.mode != want.mode:
                raise LoadoutError("Reconstructed opaque bytes or mode differs")
            continue
        document = parse_document(output.content.decode(), want.format)
        keys = frozenset(document)
        if key_fingerprints(output.content.decode(), want.format, keys) != want.fingerprints:
            raise LoadoutError("Reconstructed native keys, values or ordering differs")


def _worker() -> None:
    request = json.load(sys.stdin)
    destinations = request["destinations"]

    def isolated_destination(template: str, label: str) -> Path:
        if template not in destinations:
            raise LoadoutError("Migration references an undeclared destination")
        with patch.dict(os.environ, request["environment"], clear=True):
            if str(resolve_destination(template, label)) != request["originals"][template]:
                raise LoadoutError("Migration destination template resolves to another path")
        return Path(destinations[template])

    try:
        with patch.object(artifacts, "resolve_destination", isolated_destination):
            outputs = render_all(Path(request["root"]))
        result: list[dict[str, Any]] = []
        for path, output in outputs.items():
            owned = None
            format_name = "copy"
            emit_empty = False
            mode = 0o600
            if isinstance(output, Copied):
                content = output.read_bytes()
                mode = output.file_mode()
            elif isinstance(output, Merged):
                content = output.document.encode()
                if not output.emit_empty and not parse_document(output.document, output.format):
                    continue
                owned = sorted(output.owned)
                format_name = output.format
                emit_empty = output.emit_empty
            else:
                content = output.encode()
            result.append(
                {
                    "path": str(path),
                    "content": base64.b64encode(content).decode(),
                    "mode": mode,
                    "format": format_name,
                    "owned": owned,
                    "emit_empty": emit_empty,
                }
            )
        json.dump(result, sys.stdout)
    except Exception as error:
        print(
            type(error).__name__,
            [
                (Path(frame.filename).name, frame.lineno)
                for frame in traceback.extract_tb(error.__traceback__)
            ],
            file=sys.stderr,
        )
        sys.exit(1)
