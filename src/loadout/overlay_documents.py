from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any, Literal

from .errors import LoadoutError
from .json_paths import JsonPath, display_path, leaf_digest, lookup, with_path, without_path


@dataclass(frozen=True)
class OwnedJson:
    keys: tuple[tuple[JsonPath, Any], ...]
    rules: tuple[tuple[JsonPath, tuple[str, ...]], ...]


@dataclass(frozen=True)
class DocumentRecord:
    created: bool
    owned_keys: Mapping[JsonPath, str]
    owned_rules: Mapping[JsonPath, tuple[str, ...]]


DocumentKind = Literal[
    "create",
    "unchanged",
    "update",
    "adopt",
    "remove",
    "user-defined",
    "owned-key",
    "owned-key-removed",
]


@dataclass(frozen=True)
class DocumentVerdict:
    path: JsonPath
    kind: DocumentKind


@dataclass(frozen=True)
class AppliedDocument:
    document: dict[str, Any]
    carried: tuple[str, ...]
    record: DocumentRecord
    changed: bool


_QUIET: frozenset[DocumentKind] = frozenset({"unchanged", "adopt"})


def _ordered_unique(entries: Sequence[str]) -> list[str]:
    return list(dict.fromkeys(entries))


def carry_saved_rules(
    rendered: Sequence[str], on_disk: Sequence[str], owned_before: Sequence[str]
) -> tuple[list[str], tuple[str, ...]]:
    excluded = {*rendered, *owned_before}
    carried = tuple(_ordered_unique([entry for entry in on_disk if entry not in excluded]))
    return [*_ordered_unique(rendered), *carried], carried


def _recorded_keys(recorded: DocumentRecord | None) -> Mapping[JsonPath, str]:
    return recorded.owned_keys if recorded is not None else {}


def _recorded_rules(recorded: DocumentRecord | None) -> Mapping[JsonPath, tuple[str, ...]]:
    return recorded.owned_rules if recorded is not None else {}


def _rule_paths(owned: OwnedJson, recorded: DocumentRecord | None) -> list[JsonPath]:
    return list(dict.fromkeys([*(path for path, _ in owned.rules), *_recorded_rules(recorded)]))


def _disk_list(document: Mapping[str, Any], path: JsonPath) -> tuple[bool, list[str] | None]:
    present, value = lookup(document, path)
    if not present:
        return False, []
    if isinstance(value, list) and all(isinstance(entry, str) for entry in value):
        return True, value
    return True, None


def _key_verdict(
    existing: Mapping[str, Any], path: JsonPath, wanted: Any, digest: str | None
) -> DocumentKind:
    present, value = lookup(existing, path)
    if digest is None:
        if not present:
            return "create"
        return "adopt" if leaf_digest(value) == leaf_digest(wanted) else "user-defined"
    if not present:
        return "owned-key-removed"
    on_disk = leaf_digest(value)
    if on_disk == leaf_digest(wanted):
        return "unchanged"
    return "update" if on_disk == digest else "owned-key"


def _dropped_key_verdict(
    existing: Mapping[str, Any], path: JsonPath, digest: str
) -> DocumentKind | None:
    present, value = lookup(existing, path)
    if not present:
        return None
    return "remove" if leaf_digest(value) == digest else "owned-key"


def _rule_verdict(
    existing: Mapping[str, Any],
    path: JsonPath,
    rendered: tuple[str, ...],
    before: tuple[str, ...],
) -> DocumentKind | None:
    present, disk = _disk_list(existing, path)
    if disk is None:
        return "owned-key" if before else ("user-defined" if rendered else None)
    if any(entry in rendered and entry not in disk for entry in before):
        return "owned-key-removed"
    if any(entry not in disk for entry in rendered):
        return "update" if present else "create"
    if any(entry not in rendered and entry in disk for entry in before):
        return "update" if rendered else "remove"
    if not rendered:
        return None
    return "unchanged" if all(entry in before for entry in rendered) else "adopt"


def judge_document(
    existing: Mapping[str, Any] | None, owned: OwnedJson, recorded: DocumentRecord | None
) -> tuple[DocumentVerdict, ...]:
    if existing is None:
        return (
            *(DocumentVerdict(path, "create") for path, _ in owned.keys),
            *(DocumentVerdict(path, "create") for path, entries in owned.rules if entries),
        )
    digests = _recorded_keys(recorded)
    rendered_keys = {path for path, _ in owned.keys}
    rendered_rules = dict(owned.rules)
    before_rules = _recorded_rules(recorded)
    keys = [
        DocumentVerdict(path, _key_verdict(existing, path, value, digests.get(path)))
        for path, value in owned.keys
    ]
    dropped = [
        (path, _dropped_key_verdict(existing, path, digest))
        for path, digest in digests.items()
        if path not in rendered_keys
    ]
    rules = [
        (
            path,
            _rule_verdict(existing, path, rendered_rules.get(path, ()), before_rules.get(path, ())),
        )
        for path in _rule_paths(owned, recorded)
    ]
    return (
        *keys,
        *(DocumentVerdict(path, kind) for path, kind in [*dropped, *rules] if kind is not None),
    )


def apply_owned(
    existing: Mapping[str, Any] | None, owned: OwnedJson, recorded: DocumentRecord | None
) -> AppliedDocument:
    base: Mapping[str, Any] = existing if existing is not None else {}
    rendered_keys = {path for path, _ in owned.keys}
    document = dict(base)
    for path in _recorded_keys(recorded):
        if path not in rendered_keys:
            document = without_path(document, path)
    for path, value in owned.keys:
        document = with_path(document, path, value)
    rendered_rules = dict(owned.rules)
    before_rules = _recorded_rules(recorded)
    carried: list[str] = []
    owned_rules: dict[JsonPath, tuple[str, ...]] = {}
    for path in _rule_paths(owned, recorded):
        present, disk = _disk_list(base, path)
        rendered = rendered_rules.get(path, ())
        before = before_rules.get(path, ())
        if disk is None:
            if rendered:
                raise LoadoutError(f"{display_path(path)} is not a list of rules")
            continue
        merged, kept = carry_saved_rules(rendered, disk, before)
        if merged or present:
            document = with_path(document, path, merged)
        carried.extend(f"{display_path(path)}: {entry}" for entry in kept)
        written = tuple(
            entry for entry in _ordered_unique(rendered) if entry in before or entry not in disk
        )
        if written or (path in before_rules and present and not merged):
            owned_rules[path] = written
    verdicts = judge_document(existing, owned, recorded)
    record = DocumentRecord(
        created=existing is None or (recorded is not None and recorded.created),
        owned_keys={path: leaf_digest(value) for path, value in owned.keys},
        owned_rules=owned_rules,
    )
    return AppliedDocument(
        document=document,
        carried=tuple(carried),
        record=record,
        changed=any(verdict.kind not in _QUIET for verdict in verdicts),
    )


def judge_strip(
    existing: Mapping[str, Any], recorded: DocumentRecord
) -> tuple[DocumentVerdict, ...]:
    return judge_document(existing, OwnedJson(keys=(), rules=()), recorded)


def _prune_emptied(
    document: dict[str, Any], original: Mapping[str, Any], path: JsonPath
) -> dict[str, Any]:
    for depth in range(len(path), 0, -1):
        prefix = path[:depth]
        present, value = lookup(document, prefix)
        _, before = lookup(original, prefix)
        if not present or value not in ({}, []) or before in ({}, []):
            break
        document = without_path(document, prefix)
    return document


def strip_owned(existing: Mapping[str, Any], recorded: DocumentRecord) -> dict[str, Any] | None:
    document = dict(existing)
    for path in recorded.owned_keys:
        present, _ = lookup(document, path)
        if present:
            document = _prune_emptied(without_path(document, path), existing, path[:-1])
    for path, entries in recorded.owned_rules.items():
        present, disk = _disk_list(document, path)
        if not present or disk is None:
            continue
        if not entries and not disk:
            document = _prune_emptied(without_path(document, path), existing, path[:-1])
            continue
        kept = [entry for entry in disk if entry not in entries]
        if kept != disk:
            document = _prune_emptied(with_path(document, path, kept), existing, path)
    if recorded.created and not document:
        return None
    return document
