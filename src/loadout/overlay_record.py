from __future__ import annotations

import json
import os
from collections.abc import Iterable, Iterator, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any, Literal

from . import machine
from .atomic import replace_atomically
from .claude_json import LeafResult
from .errors import LoadoutError
from .json_paths import JsonPath, decode_path, encode_path
from .overlay_documents import DocumentRecord
from .overlay_lock import path_key, same_path
from .overlay_registry import load_registry, overlays_path
from .written import VERSION as WRITTEN_VERSION
from .written import WrittenEntry, written_state_path

RECORD_VERSION = 2
RECORDS_DIR = "overlay-written"
RecordStatus = Literal["absent", "ok", "unreadable", "newer"]
NEWER_RECORD = "was written by a newer loadout; upgrade loadout"

_KINDS = frozenset({"text", "copied"})
_RECORD_KEYS = frozenset({"version", "overlay", "name", "home", "shared", "targets"})
_TARGET_KEYS = frozenset(
    {"common_dir", "device", "block", "written", "documents", "leaves", "pending"}
)
_DOCUMENT_KEYS = frozenset(
    {"created", "owned_keys", "owned_rules", "pending_keys", "pending_rules"}
)
_ENTRY_KEYS = frozenset({"kind", "sha256", "norm_sha256"})
_DIGEST_KEYS = frozenset({"path", "sha256"})
_RULE_KEYS = frozenset({"path", "entries"})


@dataclass(frozen=True)
class TargetRecord:
    common_dir: Path
    device: int
    block: str | None
    files: Mapping[Path, WrittenEntry]
    documents: Mapping[Path, DocumentRecord]
    leaves: Mapping[Path, Mapping[JsonPath, str]]
    pending: Mapping[Path, Mapping[JsonPath, str]]


@dataclass(frozen=True)
class OverlayRecord:
    folder: Path
    name: str
    home: Path
    shared: Mapping[Path, WrittenEntry]
    targets: Mapping[Path, TargetRecord]


@dataclass(frozen=True)
class RecordProblem:
    path: Path
    status: Literal["unreadable", "newer"]
    folder: Path | None
    name: str | None


@dataclass(frozen=True)
class RecordScan:
    records: tuple[OverlayRecord, ...]
    problems: tuple[RecordProblem, ...]


def record_problem_message(problem: RecordProblem) -> str:
    what = NEWER_RECORD if problem.status == "newer" else "cannot be read; fix it"
    if problem.name is None:
        loose = problem.status == "unreadable" and problem.path.suffix == ".json"
        return f"the overlay record {problem.path} {what}" + (
            ", or delete it: it names no overlay `overlay remove` could take" if loose else ""
        )
    return (
        f"the overlay record {problem.path} {what}, or run "
        f"`loadout overlay remove {problem.name} --force`"
    )


class UnreadableRecords(LoadoutError):
    def __init__(self, problems: tuple[RecordProblem, ...]) -> None:
        super().__init__(record_problem_message(problems[0]))
        self.problems = problems


class _Malformed(Exception):
    pass


def overlay_record_path(folder: Path) -> Path:
    return machine.config_dir() / RECORDS_DIR / f"{path_key(folder)}.json"


def empty_record(folder: Path, name: str, home: Path) -> OverlayRecord:
    return OverlayRecord(folder=folder, name=name, home=home, shared={}, targets={})


def _encode_entry(entry: WrittenEntry) -> dict[str, str]:
    if entry.kind not in _KINDS:
        raise LoadoutError(f"an overlay records only text and copied files, not {entry.kind}")
    fields = {"kind": entry.kind, "sha256": entry.sha256, "norm_sha256": entry.norm_sha256}
    return {key: value for key, value in fields.items() if value is not None}


def _encode_digests(digests: Mapping[JsonPath, str]) -> list[dict[str, Any]]:
    return [
        {"path": encode_path(path), "sha256": digest} for path, digest in sorted(digests.items())
    ]


def _encode_keys(digests: Mapping[JsonPath, str]) -> list[dict[str, Any]]:
    return [{"path": encode_path(path), "sha256": digest} for path, digest in digests.items()]


def _encode_rules(rules: Mapping[JsonPath, tuple[str, ...]]) -> list[dict[str, Any]]:
    return [
        {"path": encode_path(path), "entries": list(entries)} for path, entries in rules.items()
    ]


def _encode_document(document: DocumentRecord) -> dict[str, Any]:
    pending = {
        "pending_keys": _encode_keys(document.pending_keys),
        "pending_rules": _encode_rules(document.pending_rules),
    }
    return {
        "created": document.created,
        "owned_keys": _encode_keys(document.owned_keys),
        "owned_rules": _encode_rules(document.owned_rules),
        **{key: value for key, value in pending.items() if value},
    }


def _by_path[T](items: Mapping[Path, T]) -> list[tuple[Path, T]]:
    return sorted(items.items(), key=lambda item: str(item[0]))


def _encode_target(entry: TargetRecord) -> dict[str, Any]:
    return {
        "common_dir": str(entry.common_dir),
        "device": entry.device,
        "block": entry.block,
        "written": {str(path): _encode_entry(found) for path, found in _by_path(entry.files)},
        "documents": {
            str(path): _encode_document(found) for path, found in _by_path(entry.documents)
        },
        "leaves": {str(path): _encode_digests(found) for path, found in _by_path(entry.leaves)},
        "pending": {str(path): _encode_digests(found) for path, found in _by_path(entry.pending)},
    }


def render_overlay_record(record: OverlayRecord) -> str:
    document = {
        "version": RECORD_VERSION,
        "overlay": str(record.folder),
        "name": record.name,
        "home": str(record.home),
        "shared": {str(path): _encode_entry(entry) for path, entry in _by_path(record.shared)},
        "targets": {str(root): _encode_target(entry) for root, entry in _by_path(record.targets)},
    }
    return json.dumps(document, indent=2) + "\n"


def _object(raw: object) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise _Malformed
    return raw


def _fields(raw: object, keys: frozenset[str]) -> dict[str, Any]:
    fields = _object(raw)
    if not fields.keys() <= keys:
        raise _Malformed
    return fields


def _string(raw: object) -> str:
    if not isinstance(raw, str):
        raise _Malformed
    return raw


def _absolute(raw: object) -> Path:
    path = Path(_string(raw))
    if not path.is_absolute():
        raise _Malformed
    return path


def _integer(raw: object) -> int:
    if not isinstance(raw, int) or isinstance(raw, bool):
        raise _Malformed
    return raw


def _json_path(raw: object) -> JsonPath:
    path = decode_path(raw)
    if path is None:
        raise _Malformed
    return path


def _decode_entry(raw: object) -> WrittenEntry:
    fields = _fields(raw, _ENTRY_KEYS)
    kind = fields.get("kind")
    sha = fields.get("sha256")
    norm = fields.get("norm_sha256")
    if not isinstance(sha, str) or not (norm is None or isinstance(norm, str)):
        raise _Malformed
    if kind == "text" and norm is not None:
        return WrittenEntry(kind="text", sha256=sha, norm_sha256=norm)
    if kind == "copied":
        return WrittenEntry(kind="copied", sha256=sha, norm_sha256=norm)
    raise _Malformed


def _decode_files(raw: object) -> dict[Path, WrittenEntry]:
    return {_absolute(name): _decode_entry(found) for name, found in _object(raw).items()}


def _decode_digests(raw: object) -> dict[JsonPath, str]:
    if not isinstance(raw, list):
        raise _Malformed
    pairs = [_fields(item, _DIGEST_KEYS) for item in raw]
    return {_json_path(pair.get("path")): _string(pair.get("sha256")) for pair in pairs}


def _decode_rules(raw: object) -> dict[JsonPath, tuple[str, ...]]:
    if not isinstance(raw, list):
        raise _Malformed
    pairs = [_fields(item, _RULE_KEYS) for item in raw]
    return {_json_path(pair.get("path")): _strings(pair.get("entries")) for pair in pairs}


def _strings(raw: object) -> tuple[str, ...]:
    if not isinstance(raw, list):
        raise _Malformed
    return tuple(_string(entry) for entry in raw)


def _decode_document(raw: object) -> DocumentRecord:
    fields = _fields(raw, _DOCUMENT_KEYS)
    created = fields.get("created")
    if not isinstance(created, bool):
        raise _Malformed
    return DocumentRecord(
        created=created,
        owned_keys=_decode_digests(fields.get("owned_keys")),
        owned_rules=_decode_rules(fields.get("owned_rules")),
        pending_keys=_decode_digests(fields.get("pending_keys", [])),
        pending_rules=_decode_rules(fields.get("pending_rules", [])),
    )


def _decode_leaf_files(raw: object) -> dict[Path, dict[JsonPath, str]]:
    return {_absolute(name): _decode_digests(found) for name, found in _object(raw).items()}


def _decode_target(raw: object) -> TargetRecord:
    fields = _fields(raw, _TARGET_KEYS)
    block = fields.get("block")
    return TargetRecord(
        common_dir=_absolute(fields.get("common_dir")),
        device=_integer(fields.get("device")),
        block=None if block is None else _string(block),
        files=_decode_files(fields.get("written")),
        documents={
            _absolute(name): _decode_document(found)
            for name, found in _object(fields.get("documents")).items()
        },
        leaves=_decode_leaf_files(fields.get("leaves")),
        pending=_decode_leaf_files(fields.get("pending")),
    )


def _decode_record(raw: object) -> OverlayRecord:
    fields = _fields(raw, _RECORD_KEYS)
    version = fields.get("version")
    if _integer(version) != RECORD_VERSION:
        raise _Malformed
    return OverlayRecord(
        folder=_absolute(fields.get("overlay")),
        name=_string(fields.get("name")),
        home=_absolute(fields.get("home")),
        shared=_decode_files(fields.get("shared")),
        targets={
            _absolute(root): _decode_target(found)
            for root, found in _object(fields.get("targets")).items()
        },
    )


def _parsed(text: str) -> OverlayRecord:
    try:
        raw = json.loads(text)
    except ValueError:
        raise _Malformed from None
    return _decode_record(raw)


def _newer(raw: object) -> bool:
    version = raw.get("version") if isinstance(raw, dict) else None
    return isinstance(version, int) and not isinstance(version, bool) and version > RECORD_VERSION


def _named(raw: object) -> tuple[Path | None, str | None]:
    fields = raw if isinstance(raw, dict) else {}
    folder, name = fields.get("overlay"), fields.get("name")
    return (
        Path(folder) if isinstance(folder, str) and Path(folder).is_absolute() else None,
        name if isinstance(name, str) else None,
    )


def _registered_name(path: Path) -> str | None:
    try:
        overlays = load_registry(overlays_path())
    except (LoadoutError, OSError):
        return None
    return next((found.name for found in overlays if path_key(found.folder) == path.stem), None)


def _load(path: Path) -> OverlayRecord | RecordProblem | None:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        return RecordProblem(path, "unreadable", None, _registered_name(path))
    try:
        raw = json.loads(text)
    except ValueError:
        return RecordProblem(path, "unreadable", None, _registered_name(path))
    folder, name = _named(raw)
    if _newer(raw):
        return RecordProblem(path, "newer", folder, name)
    try:
        return _decode_record(raw)
    except _Malformed:
        return RecordProblem(path, "unreadable", folder, name)


def _listing() -> tuple[list[tuple[Path, OverlayRecord | RecordProblem]], RecordProblem | None]:
    directory = machine.config_dir() / RECORDS_DIR
    try:
        paths = sorted(path for path in directory.iterdir() if path.suffix == ".json")
    except FileNotFoundError:
        return [], None
    except OSError:
        return [], RecordProblem(directory, "unreadable", None, None)
    loaded = [(path, _load(path)) for path in paths]
    return [(path, found) for path, found in loaded if found is not None], None


def _belongs(found: OverlayRecord | RecordProblem, folder: Path) -> bool:
    return found.folder is not None and same_path(found.folder, folder)


def _locate(folder: Path) -> OverlayRecord | RecordProblem | None:
    own = overlay_record_path(folder)
    found = _load(own)
    if isinstance(found, RecordProblem):
        return found
    if found is not None:
        return (
            found
            if same_path(found.folder, folder)
            else RecordProblem(own, "unreadable", found.folder, found.name)
        )
    listing, problem = _listing()
    if problem is not None:
        return problem
    matches = [held for _, held in listing if _belongs(held, folder)]
    problems = [held for held in matches if isinstance(held, RecordProblem)]
    return next(iter([*problems, *matches]), None)


def record_files(folder: Path) -> tuple[Path, ...]:
    own = overlay_record_path(folder)
    listing, _ = _listing()
    held = [path for path, found in listing if _belongs(found, folder)]
    return tuple(path for path in dict.fromkeys([own, *held]) if path.exists())


def read_overlay_record(folder: Path, name: str, home: Path) -> tuple[OverlayRecord, RecordStatus]:
    empty = empty_record(folder, name, home)
    found = _locate(folder)
    if found is None:
        return empty, "absent"
    if isinstance(found, RecordProblem):
        return empty, found.status
    return found, "ok"


def scan_overlay_records() -> RecordScan:
    listing, problem = _listing()
    return RecordScan(
        records=tuple(found for _, found in listing if isinstance(found, OverlayRecord)),
        problems=(
            (problem,)
            if problem is not None
            else tuple(found for _, found in listing if isinstance(found, RecordProblem))
        ),
    )


def every_overlay_record() -> tuple[OverlayRecord, ...]:
    return scan_overlay_records().records


def _sync_directory(directory: Path) -> None:
    descriptor = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _checked(record: OverlayRecord, path: Path) -> str:
    text = render_overlay_record(record)
    try:
        same = _parsed(text) == record
    except _Malformed:
        same = False
    if not same:
        raise LoadoutError(
            f"the overlay record {path} would not read back as rendered; nothing was written"
        )
    return text


def _held(record: OverlayRecord, path: Path) -> list[Path]:
    listing, problem = _listing()
    if problem is not None:
        raise LoadoutError(f"cannot write the overlay record {path}: cannot list {problem.path}")
    held = [
        (found_path, found)
        for found_path, found in listing
        if found_path == path or _belongs(found, record.folder)
    ]
    newer = [
        found_path
        for found_path, found in held
        if isinstance(found, RecordProblem) and found.status == "newer"
    ]
    if newer:
        raise LoadoutError(f"the overlay record {newer[0]} {NEWER_RECORD}")
    foreign = [
        found
        for found_path, found in listing
        if found_path == path
        and isinstance(found, OverlayRecord)
        and not _belongs(found, record.folder)
    ]
    if foreign:
        raise LoadoutError(
            f"the overlay record {path} holds overlay {foreign[0].name} at {foreign[0].folder}; "
            f"run `loadout overlay remove {foreign[0].name}` first"
        )
    return [found_path for found_path, _ in held if found_path != path]


def write_overlay_record(record: OverlayRecord) -> None:
    path = overlay_record_path(record.folder)
    text = _checked(record, path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
    except OSError as error:
        raise LoadoutError(f"cannot write the overlay record {path}: {error}") from None
    stale = _held(record, path)
    try:
        replace_atomically(path, lambda handle: handle.write(text.encode("utf-8")), durable=True)
        _sync_directory(path.parent)
        for old in stale:
            old.unlink(missing_ok=True)
        if stale:
            _sync_directory(path.parent)
    except OSError as error:
        raise LoadoutError(f"cannot write the overlay record {path}: {error}") from None


def with_target(record: OverlayRecord, root: Path, entry: TargetRecord) -> OverlayRecord:
    return replace(record, targets={**record.targets, root: entry})


def without_target(record: OverlayRecord, root: Path) -> OverlayRecord:
    return replace(
        record, targets={found: entry for found, entry in record.targets.items() if found != root}
    )


def united_document(before: DocumentRecord | None, after: DocumentRecord) -> DocumentRecord:
    keys = before.owned_keys if before is not None else {}
    rules = before.owned_rules if before is not None else {}
    return DocumentRecord(
        created=(before is not None and before.created) or after.created,
        owned_keys=dict(keys),
        owned_rules=dict(rules),
        pending_keys={
            path: digest for path, digest in after.owned_keys.items() if keys.get(path) != digest
        },
        pending_rules={
            path: added
            for path, entries in after.owned_rules.items()
            if (added := tuple(entry for entry in entries if entry not in rules.get(path, ())))
        },
    )


def with_pending(entry: TargetRecord, file: Path, digests: Mapping[JsonPath, str]) -> TargetRecord:
    pending = {**entry.pending.get(file, {}), **digests}
    return replace(entry, pending={**entry.pending, file: pending})


def settled_leaves(entry: TargetRecord, file: Path, result: LeafResult) -> TargetRecord:
    recorded = entry.leaves.get(file, {})
    pending = entry.pending.get(file, {})
    retired = frozenset(result.retired)
    settled = {
        path: result.digests[path] if path in result.digests else recorded[path]
        for path in dict.fromkeys([*recorded, *pending])
        if path not in retired and (path in result.digests or path in recorded)
    }
    leaves = {found: held for found, held in entry.leaves.items() if found != file}
    return replace(
        entry,
        leaves={**leaves, file: settled} if settled else leaves,
        pending={found: held for found, held in entry.pending.items() if found != file},
    )


def _under(path: Path, prefixes: tuple[str, ...]) -> bool:
    return str(path).startswith(prefixes)


def _held_paths(record: OverlayRecord) -> Iterator[Path]:
    for entry in record.targets.values():
        yield from entry.files
        yield from entry.documents


def recorded_paths_within(
    records: Iterable[OverlayRecord], within: Iterable[Path]
) -> frozenset[Path]:
    prefixes = tuple(f"{folder}{os.sep}" for folder in within)
    if not prefixes:
        return frozenset()
    return frozenset(
        path for record in records for path in _held_paths(record) if _under(path, prefixes)
    )


def every_overlay_recorded_path(within: Iterable[Path]) -> frozenset[Path]:
    folders = tuple(within)
    return recorded_paths_within(every_overlay_record(), folders) if folders else frozenset()


def _project_claims() -> Iterator[tuple[Path, str]]:
    try:
        records = sorted((machine.config_dir() / "written").glob("*.json"))
    except OSError:
        return
    for record in records:
        try:
            document = json.loads(record.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        root = _written_root(document)
        if root is None or written_state_path(root) != record:
            continue
        yield from ((Path(name), str(root)) for name in _written_names(document))


def _written_root(document: object) -> Path | None:
    root = document.get("root") if isinstance(document, dict) else None
    return Path(root) if isinstance(root, str) and Path(root).is_absolute() else None


def _written_names(document: object) -> list[str]:
    if not isinstance(document, dict) or document.get("version") != WRITTEN_VERSION:
        return []
    entries = document.get("written")
    if not isinstance(entries, dict):
        return []
    return [
        name
        for name, raw in entries.items()
        if isinstance(raw, dict) and isinstance(raw.get("kind"), str)
    ]


def _others(folder: Path, force: bool) -> list[OverlayRecord]:
    scan = scan_overlay_records()
    problems = tuple(
        problem
        for problem in scan.problems
        if problem.path != overlay_record_path(folder) and not _belongs(problem, folder)
    )
    if problems and not force:
        raise UnreadableRecords(problems)
    return [record for record in scan.records if not _belongs(record, folder)]


@dataclass(frozen=True)
class ForeignClaims:
    paths: Mapping[Path, str]
    leaves: Mapping[tuple[Path, JsonPath], str]


NO_CLAIMS = ForeignClaims(paths={}, leaves={})


def foreign_claims(folder: Path, *, force: bool = False) -> ForeignClaims:
    others = _others(folder, force)
    paths: dict[Path, str] = {}
    for path, owner in (
        *(
            (path, f"overlay {record.name}")
            for record in others
            for path in (*_held_paths(record), *record.shared)
        ),
        *_project_claims(),
    ):
        paths.setdefault(path, owner)
    leaves: dict[tuple[Path, JsonPath], str] = {}
    for record in others:
        for entry in record.targets.values():
            for file, digests in (*entry.leaves.items(), *entry.pending.items()):
                for leaf in digests:
                    leaves.setdefault((file, leaf), f"overlay {record.name}")
    return ForeignClaims(paths=paths, leaves=leaves)


def claimed_paths(paths: Iterable[Path], claims: ForeignClaims) -> dict[Path, str]:
    return {path: claims.paths[path] for path in dict.fromkeys(paths) if path in claims.paths}


def claimed_skills(skills: Iterable[Path], claims: ForeignClaims) -> dict[Path, str]:
    return {
        skill: owner
        for skill in dict.fromkeys(skills)
        if (
            owner := next(
                (found for path, found in claims.paths.items() if path.is_relative_to(skill)),
                None,
            )
        )
        is not None
    }


def claimed_leaves(
    leaves: Iterable[tuple[Path, JsonPath]], claims: ForeignClaims
) -> dict[tuple[Path, JsonPath], str]:
    return {leaf: claims.leaves[leaf] for leaf in dict.fromkeys(leaves) if leaf in claims.leaves}
