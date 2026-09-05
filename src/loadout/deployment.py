from __future__ import annotations

import hashlib
import json
import os
import stat
import subprocess
import tempfile
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from .artifacts import (
    Artifacts,
    Copied,
    Merged,
    Output,
    _json_constant,
    _json_object,
    _no_symlinks,
    artifact_destination,
    relative_path,
)
from .destinations import resolve_destination
from .errors import LoadoutError
from .native_documents import apply_document, key_fingerprints

STATE_DIRECTORY = ".loadout-state"
RECEIPT_VERSION = 1
MAX_MODE = 0o7777
SHA256_LENGTH = 64
PAIR_LENGTH = 2
GIT_FATAL = 128


class DeploymentConflict(LoadoutError):
    pass


@dataclass(frozen=True)
class FrozenFile:
    content: bytes
    mode: int


@dataclass(frozen=True)
class DeploymentEntry:
    anchor: str
    route: str
    root: str
    relative: str
    format: str
    mode: int
    digest: str
    owned: tuple[str, ...] = ()
    values: tuple[tuple[str, str], ...] = ()

    def path(self, scope: str) -> Path:
        base = Path(self.root) / self.route if scope == "project" else Path(self.root)
        return base / self.relative


@dataclass(frozen=True)
class DeploymentScope:
    name: str
    anchor: Path
    source_root: Path
    artifacts: Artifacts | None
    protected: tuple[Path, ...]

    @property
    def receipt_path(self) -> Path:
        return self.source_root / STATE_DIRECTORY / f"{self.name}.json"


@dataclass(frozen=True)
class Receipt:
    entries: tuple[DeploymentEntry, ...] = ()
    pending: tuple[DeploymentEntry, ...] = ()


@dataclass(frozen=True)
class FileChange:
    path: Path
    before: FrozenFile | None
    after: FrozenFile | None


@dataclass(frozen=True)
class ScopePlan:
    scope: DeploymentScope
    receipt_before: FrozenFile | None
    previous: Receipt
    entries: tuple[DeploymentEntry, ...]
    changes: tuple[FileChange, ...]


@dataclass(frozen=True)
class DeploymentPlan:
    scopes: tuple[ScopePlan, ...]
    managed: frozenset[Path]
    drift: tuple[tuple[Path, str, str], ...]
    conflicts: tuple[str, ...]
    detached: tuple[Path, ...]


def _routes(scope: DeploymentScope) -> tuple[tuple[str, Path, bool], ...]:
    if scope.artifacts is None:
        return ()
    return tuple(
        (
            str(record.output or record.destination),
            artifact_destination(record, scope.anchor),
            record.format == "tree",
        )
        for record in scope.artifacts.records
    )


def managed_paths(
    scopes: tuple[DeploymentScope, ...], outputs: Mapping[Path, Output]
) -> frozenset[Path]:
    return frozenset(
        path
        for scope in scopes
        for _, destination, tree in _routes(scope)
        for path in outputs
        if path == destination or (tree and path.is_relative_to(destination))
    )


def _targets(
    scope: DeploymentScope, outputs: Mapping[Path, Output]
) -> dict[Path, tuple[tuple[str, Path, bool], Output]]:
    return {
        path: (route, output)
        for route in _routes(scope)
        for path, output in outputs.items()
        if path == route[1] or (route[2] and path.is_relative_to(route[1]))
    }


def read_file(path: Path) -> FrozenFile | None:
    _no_symlinks(path)
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            raise LoadoutError(f"deployment parent must be a directory: {parent}")
    if not path.exists():
        return None
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode):
        raise LoadoutError(f"deployment path must be a regular file: {path}")
    return FrozenFile(path.read_bytes(), stat.S_IMODE(metadata.st_mode))


def freeze_output(path: Path, output: Output) -> FrozenFile:
    if isinstance(output, Copied):
        frozen = read_file(output.source)
        if frozen is None:
            raise LoadoutError(f"copied source disappeared: {output.source}")
        return frozen
    if isinstance(output, Merged):
        actual = read_file(path)
        text = apply_document(
            actual.content.decode() if actual else "", output.owned, output.document, output.format
        )
        return FrozenFile(text.encode(), actual.mode if actual else 0o600)
    return FrozenFile(output.encode(), 0o600)


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _tracked(path: Path) -> bool:
    ancestor = path.parent
    while not ancestor.exists():
        ancestor = ancestor.parent
    try:
        result = subprocess.run(
            ["git", "-C", str(ancestor), "ls-files", "--", str(path)],
            capture_output=True,
            check=False,
        )
    except OSError as error:
        raise LoadoutError(f"could not verify private receipt tracking: {path}") from error
    if result.returncode == GIT_FATAL and b"not a git repository" in result.stderr:
        return False
    if result.returncode != 0:
        raise LoadoutError(f"could not verify private receipt tracking: {path}")
    return bool(result.stdout.strip())


def _validate_state(scope: DeploymentScope) -> FrozenFile | None:
    path = scope.receipt_path
    _no_symlinks(path)
    if _tracked(path.parent):
        raise LoadoutError(f"deployment receipts must be private and untracked: {path}")
    if path.parent.exists() and path.parent.stat().st_mode & 0o077:
        raise LoadoutError(f"deployment directory must be private (mode 0700): {path.parent}")
    ignored = read_file(path.parent / ".gitignore")
    if ignored is not None and ignored.content != b"*\n":
        raise LoadoutError(
            f"private deployment ignore file must contain '*': {path.parent / '.gitignore'}"
        )
    receipt = read_file(path)
    if receipt is not None and receipt.mode & 0o077:
        raise LoadoutError(f"deployment receipt must be private (mode 0600): {path}")
    return receipt


def _entry(raw: Any, scope: DeploymentScope) -> DeploymentEntry:
    fields = {"anchor", "route", "root", "relative", "format", "mode", "digest", "owned", "values"}
    if not isinstance(raw, dict) or set(raw) != fields:
        raise ValueError("invalid deployment entry fields")
    for key in ("anchor", "root"):
        if (
            not isinstance(raw[key], str)
            or not Path(raw[key]).is_absolute()
            or ".." in Path(raw[key]).parts
        ):
            raise ValueError(f"invalid {key}")
    if not isinstance(raw["route"], str) or not raw["route"] or "\x00" in raw["route"]:
        raise ValueError("invalid route")
    if scope.name == "project":
        relative_path(raw["route"], "receipt route")
        if raw["anchor"] != raw["root"]:
            raise ValueError("project root differs from anchor")
    if raw["relative"] != ".":
        relative_path(raw["relative"], "receipt relative")
    if raw["format"] not in {"whole", "json", "toml"}:
        raise ValueError("invalid document format")
    if type(raw["mode"]) is not int or not 0 <= raw["mode"] <= MAX_MODE:
        raise ValueError("invalid mode")
    owned, values = _entry_fingerprints(raw)
    return DeploymentEntry(
        **{k: v for k, v in raw.items() if k not in {"owned", "values"}},
        owned=owned,
        values=values,
    )


def _entry_fingerprints(raw: dict[str, Any]) -> tuple[tuple[str, ...], tuple[tuple[str, str], ...]]:
    owned = raw["owned"]
    values = raw["values"]
    if (
        not isinstance(owned, list)
        or any(not isinstance(k, str) for k in owned)
        or len(set(owned)) != len(owned)
    ):
        raise ValueError("invalid ownership")
    if not isinstance(values, list) or any(
        not isinstance(v, list)
        or len(v) != PAIR_LENGTH
        or not isinstance(v[0], str)
        or v[0] not in owned
        for v in values
    ):
        raise ValueError("invalid key fingerprints")
    hashes = [raw["digest"]] if raw["format"] == "whole" else [v[1] for v in values]
    if any(
        not isinstance(value, str)
        or len(value) != SHA256_LENGTH
        or any(c not in "0123456789abcdef" for c in value)
        for value in hashes
    ):
        raise ValueError("invalid fingerprint")
    if raw["format"] == "whole" and (owned or values):
        raise ValueError("whole file has partial ownership")
    if raw["format"] != "whole" and raw["digest"] != "":
        raise ValueError("partial document has a whole-file fingerprint")
    if len({v[0] for v in values}) != len(values):
        raise ValueError("duplicate key fingerprints")
    return tuple(owned), tuple((v[0], v[1]) for v in values)


def read_receipt(scope: DeploymentScope, frozen: FrozenFile | None) -> Receipt:
    if frozen is None:
        return Receipt()
    try:
        raw = json.loads(
            frozen.content, object_pairs_hook=_json_object, parse_constant=_json_constant
        )
        if not isinstance(raw, dict) or set(raw) != {"version", "scope", "entries", "pending"}:
            raise ValueError("invalid receipt fields")
        if raw["version"] != RECEIPT_VERSION or type(raw["version"]) is not int:
            raise ValueError("unsupported receipt version")
        if raw["scope"] != scope.name:
            raise ValueError("receipt scope differs")
        if not isinstance(raw["entries"], list) or not isinstance(raw["pending"], list):
            raise ValueError("invalid receipt entries")
        return Receipt(
            tuple(_entry(entry, scope) for entry in raw["entries"]),
            tuple(_entry(entry, scope) for entry in raw["pending"]),
        )
    except (ValueError, TypeError, KeyError, UnicodeError) as error:
        raise LoadoutError(f"invalid deployment receipt {scope.receipt_path}: {error}") from error


def _attached(entry: DeploymentEntry, scope: DeploymentScope) -> bool:
    if Path(entry.anchor) != scope.anchor:
        return False
    if scope.name == "project":
        return Path(entry.root) == scope.anchor
    try:
        return resolve_destination(entry.route, "deployment receipt") == Path(entry.root)
    except LoadoutError:
        return False


def _new_entry(
    scope: DeploymentScope,
    route: tuple[str, Path, bool],
    change: FileChange,
    output: Output,
) -> DeploymentEntry:
    after = change.after
    assert after is not None
    values = (
        key_fingerprints(after.content.decode(), output.format, output.owned)
        if isinstance(output, Merged)
        else ()
    )
    return DeploymentEntry(
        anchor=str(scope.anchor),
        route=route[0],
        root=str(scope.anchor if scope.name == "project" else route[1]),
        relative=str(change.path.relative_to(route[1])),
        format=output.format if isinstance(output, Merged) else "whole",
        mode=after.mode,
        digest="" if isinstance(output, Merged) else _digest(after.content),
        owned=tuple(sorted(output.owned)) if isinstance(output, Merged) else (),
        values=values,
    )


def _matches(
    actual: FrozenFile, entry: DeploymentEntry, owned: frozenset[str] | None = None
) -> bool:
    if actual.mode != entry.mode:
        return False
    if entry.format == "whole":
        return _digest(actual.content) == entry.digest
    return (
        key_fingerprints(
            actual.content.decode(),
            entry.format,
            frozenset(entry.owned) if owned is None else owned,
        )
        == entry.values
    )


def _partial_accepted(
    actual: FrozenFile,
    output: Merged,
    previous: tuple[DeploymentEntry, ...],
    mode: int,
) -> bool:
    previous_owned = frozenset(key for entry in previous for key in entry.owned)
    owned = output.owned | previous_owned
    have = key_fingerprints(actual.content.decode(), output.format, owned)
    want = key_fingerprints(output.document, output.format, owned)
    if actual.mode == mode and have == want:
        return True
    candidates = previous or (DeploymentEntry("", "", "", ".", output.format, mode, ""),)
    for entry in candidates:
        if entry.format != output.format or actual.mode != entry.mode:
            continue
        baseline = tuple(value for value in have if value[0] in previous_owned)
        introduced = tuple(value for value in have if value[0] not in previous_owned)
        introduced_keys = {key for key, _ in introduced}
        desired_introduced = tuple(value for value in want if value[0] in introduced_keys)
        if baseline == entry.values and introduced == desired_introduced:
            return True
    return False


def _summary(value: FrozenFile | None) -> str:
    if value is None:
        return "(absent)\n"
    return f"{len(value.content)} bytes, mode {value.mode:04o}, sha256 {_digest(value.content)}\n"


def _protected(path: Path, scopes: tuple[DeploymentScope, ...]) -> None:
    if ".git" in path.parts or STATE_DIRECTORY in path.parts:
        raise LoadoutError(f"deployment overlaps protected metadata: {path}")
    for scope in scopes:
        inputs = list(scope.protected)
        if scope.artifacts:
            inputs += [
                scope.artifacts.path,
                *(
                    scope.artifacts.source_root / part.source
                    for record in scope.artifacts.records
                    for part in record.parts
                ),
            ]
        for source in inputs:
            if path.is_relative_to(source) or source.is_relative_to(path):
                raise LoadoutError(f"deployment {path} overlaps source {source}")


def _target_change(
    path: Path, output: Output, baseline: tuple[DeploymentEntry, ...]
) -> tuple[FileChange, bool]:
    actual = read_file(path)
    if not isinstance(output, Merged):
        if any(entry.format != "whole" for entry in baseline):
            raise LoadoutError(
                f"deployment ownership changed at {path}; reconcile the receipt first"
            )
        after = freeze_output(path, output)
        accepted = (
            actual is None or actual == after or any(_matches(actual, entry) for entry in baseline)
        )
        return FileChange(path, actual, after), accepted
    if any(entry.format != output.format for entry in baseline):
        raise LoadoutError(f"deployment ownership changed at {path}; reconcile the receipt first")
    mode = baseline[-1].mode if baseline else actual.mode if actual else 0o600
    owned = output.owned | frozenset(key for entry in baseline for key in entry.owned)
    text = apply_document(
        actual.content.decode() if actual else "", owned, output.document, output.format
    )
    if not text and output.emit_empty:
        text = "{}\n" if output.format == "json" else ""
    accepted = actual is None or _partial_accepted(actual, output, baseline, mode)
    if actual is None and not output.document and not output.emit_empty:
        return FileChange(path, None, None), accepted
    return FileChange(path, actual, FrozenFile(text.encode(), mode)), accepted


def _retirement(path: Path, baseline: tuple[DeploymentEntry, ...]) -> tuple[FileChange, bool]:
    actual = read_file(path)
    if actual is None:
        return FileChange(path, None, None), True
    latest = baseline[-1]
    owned = frozenset(key for entry in baseline for key in entry.owned)
    after = None
    if latest.format != "whole":
        after = FrozenFile(
            apply_document(actual.content.decode(), owned, "", latest.format).encode(), actual.mode
        )
    accepted = any(_matches(actual, entry, owned) for entry in baseline)
    if after is not None and actual == after and actual.mode == latest.mode:
        accepted = True
    return FileChange(path, actual, after), accepted


def _claim_deployment(path: Path, scope: DeploymentScope, claimed: dict[Path, str]) -> None:
    for other, owner in claimed.items():
        if owner != scope.name and (path.is_relative_to(other) or other.is_relative_to(path)):
            raise LoadoutError(f"deployment receipt collision between scopes: {path} and {other}")
    claimed[path] = scope.name


def _collect_prior(
    scope: DeploymentScope,
    previous: Receipt,
    scopes: tuple[DeploymentScope, ...],
    claimed: dict[Path, str],
) -> tuple[dict[Path, list[DeploymentEntry]], list[DeploymentEntry], set[Path]]:
    prior: dict[Path, list[DeploymentEntry]] = {}
    entries: list[DeploymentEntry] = []
    detached: set[Path] = set()
    for entry in (*previous.entries, *previous.pending):
        path = entry.path(scope.name)
        if not _attached(entry, scope):
            detached.add(path)
            if entry not in entries:
                entries.append(entry)
            continue
        _protected(path, scopes)
        _claim_deployment(path, scope, claimed)
        prior.setdefault(path, []).append(entry)
    return prior, entries, detached


def prepare_deployment(
    scopes: tuple[DeploymentScope, ...],
    outputs: Mapping[Path, Output],
    *,
    force: bool = False,
) -> DeploymentPlan:
    plans: list[ScopePlan] = []
    managed: set[Path] = set()
    drift: list[tuple[Path, str, str]] = []
    conflicts: list[str] = []
    detached: set[Path] = set()
    claimed: dict[Path, str] = {}
    for scope in scopes:
        frozen = _validate_state(scope)
        previous = read_receipt(scope, frozen)
        prior, entries, scope_detached = _collect_prior(scope, previous, scopes, claimed)
        detached.update(scope_detached)
        changes: list[FileChange] = []
        active: set[Path] = set()
        for path, (route, output) in _targets(scope, outputs).items():
            _protected(path, scopes)
            _claim_deployment(path, scope, claimed)
            managed.add(path)
            active.add(path)
            change, accepted = _target_change(path, output, tuple(prior.get(path, ())))
            if not accepted and not force:
                conflicts.append(
                    f"{path} was modified outside loadout or has no accepted deployment baseline"
                )
            changes.append(change)
            if change.before != change.after:
                drift.append((path, _summary(change.before), _summary(change.after)))
            if change.after is not None:
                entries.append(_new_entry(scope, route, change, output))
        for path, baseline_list in prior.items():
            if path in active:
                continue
            if any(
                path == current or path.is_relative_to(current) or current.is_relative_to(path)
                for current in outputs
            ):
                raise LoadoutError(f"retired deployment overlaps a current output: {path}")
            change, accepted = _retirement(path, tuple(baseline_list))
            if not accepted:
                conflicts.append(
                    f"retired deployment {path} was modified outside loadout; preserve or reconcile it before sync"
                )
            changes.append(change)
            if change.before != change.after:
                drift.append((path, _summary(change.before), _summary(change.after)))
        if previous.pending or tuple(entries) != previous.entries:
            drift.append(
                (
                    scope.receipt_path,
                    "deployment ownership is not current\n",
                    "current deployment receipt\n",
                )
            )
        plans.append(ScopePlan(scope, frozen, previous, tuple(entries), tuple(changes)))
    return DeploymentPlan(
        tuple(plans), frozenset(managed), tuple(drift), tuple(conflicts), tuple(sorted(detached))
    )


def atomic_install(path: Path, frozen: FrozenFile) -> None:
    _no_symlinks(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".loadout-")
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(frozen.content)
            handle.flush()
            os.fchmod(handle.fileno(), frozen.mode)
            os.fsync(handle.fileno())
        _no_symlinks(path)
        os.replace(temporary, path)
    except BaseException:
        Path(temporary).unlink(missing_ok=True)
        raise


def _receipt_bytes(scope: DeploymentScope, receipt: Receipt) -> FrozenFile:
    document = {
        "version": RECEIPT_VERSION,
        "scope": scope.name,
        "entries": [asdict(entry) for entry in receipt.entries],
        "pending": [asdict(entry) for entry in receipt.pending],
    }
    return FrozenFile((json.dumps(document, indent=2) + "\n").encode(), 0o600)


def apply_deployment(
    plan: DeploymentPlan,
    *,
    write: Callable[[Path, FrozenFile | None], None] | None = None,
) -> list[Path]:
    def install(path: Path, value: FrozenFile | None) -> None:
        if write is not None:
            write(path, value)
        elif value is None:
            path.unlink()
        else:
            atomic_install(path, value)

    if plan.conflicts:
        raise DeploymentConflict("; ".join(plan.conflicts))
    for scope_plan in plan.scopes:
        if _validate_state(scope_plan.scope) != scope_plan.receipt_before:
            raise LoadoutError(
                f"deployment receipt changed after planning: {scope_plan.scope.receipt_path}"
            )
        for change in scope_plan.changes:
            if read_file(change.path) != change.before:
                raise LoadoutError(f"deployment changed after planning: {change.path}")
    written: list[Path] = []
    for scope_plan in plan.scopes:
        scope = scope_plan.scope
        directory = scope.receipt_path.parent
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not (directory / ".gitignore").exists():
            install(directory / ".gitignore", FrozenFile(b"*\n", 0o600))
        pending = Receipt(
            (*scope_plan.previous.entries, *scope_plan.previous.pending), scope_plan.entries
        )
        install(scope.receipt_path, _receipt_bytes(scope, pending))
        for change in scope_plan.changes:
            if read_file(change.path) != change.before:
                raise LoadoutError(f"deployment changed after planning: {change.path}")
            if change.before == change.after:
                continue
            install(change.path, change.after)
            written.append(change.path)
        install(scope.receipt_path, _receipt_bytes(scope, Receipt(scope_plan.entries)))
    return written
