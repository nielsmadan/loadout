from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from .credentials import credential_material
from .errors import LoadoutError
from .json_paths import (
    JsonPath,
    OwnershipKind,
    display_path,
    dropped_verdict,
    leaf_digest,
    lookup,
    wanted_verdict,
    with_path,
    without_path,
)


@dataclass(frozen=True)
class OwnedJson:
    keys: tuple[tuple[JsonPath, Any], ...]
    rules: tuple[tuple[JsonPath, tuple[str, ...]], ...]


@dataclass(frozen=True)
class DocumentRecord:
    created: bool
    owned_keys: Mapping[JsonPath, str]
    owned_rules: Mapping[JsonPath, tuple[str, ...]]
    pending_keys: Mapping[JsonPath, str] = field(default_factory=dict)
    pending_rules: Mapping[JsonPath, tuple[str, ...]] = field(default_factory=dict)


DocumentKind = OwnershipKind


@dataclass(frozen=True)
class DocumentVerdict:
    path: JsonPath
    kind: DocumentKind


@dataclass(frozen=True)
class AppliedDocument:
    document: dict[str, Any]
    carried: tuple[tuple[str, str], ...]
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


def _carried_entry(path: JsonPath, entry: str) -> tuple[str, str]:
    reason = credential_material(entry.encode())
    shown = entry if reason is None else f"a rule not shown because it holds a secret ({reason})"
    return display_path(path), shown


def _recorded_keys(recorded: DocumentRecord | None) -> Mapping[JsonPath, str]:
    return recorded.owned_keys if recorded is not None else {}


def _recorded_rules(recorded: DocumentRecord | None) -> Mapping[JsonPath, tuple[str, ...]]:
    return recorded.owned_rules if recorded is not None else {}


def _pending_keys(recorded: DocumentRecord | None) -> Mapping[JsonPath, str]:
    return recorded.pending_keys if recorded is not None else {}


def _pending_rules(recorded: DocumentRecord | None) -> Mapping[JsonPath, tuple[str, ...]]:
    return recorded.pending_rules if recorded is not None else {}


def _rule_paths(owned: OwnedJson, recorded: DocumentRecord | None) -> list[JsonPath]:
    return list(
        dict.fromkeys(
            [
                *(path for path, _ in owned.rules),
                *_recorded_rules(recorded),
                *_pending_rules(recorded),
            ]
        )
    )


def _holds_pending(document: Mapping[str, Any], path: JsonPath, digest: str) -> bool:
    present, value = lookup(document, path)
    return present and leaf_digest(value) == digest


def _disk_list(document: Mapping[str, Any], path: JsonPath) -> tuple[bool, list[str] | None]:
    present, value = lookup(document, path)
    if not present:
        return False, []
    if isinstance(value, list) and all(isinstance(entry, str) for entry in value):
        return True, value
    return True, None


def _owned(digest: str | None) -> frozenset[str]:
    return frozenset() if digest is None else frozenset({digest})


def _rule_verdict(
    existing: Mapping[str, Any],
    path: JsonPath,
    rendered: tuple[str, ...],
    before: tuple[str, ...],
    pending: tuple[str, ...],
) -> DocumentKind | None:
    present, disk = _disk_list(existing, path)
    if disk is None:
        return "owned-key" if before else ("user-defined" if rendered else None)
    if any(entry in rendered and entry not in disk for entry in before):
        return "owned-key-removed"
    if any(entry not in disk for entry in rendered):
        return "update" if present else "create"
    if any(entry not in rendered and entry in disk for entry in (*before, *pending)):
        return "update" if rendered else "retire"
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
    pending = _pending_keys(recorded)
    rendered_keys = {path for path, _ in owned.keys}
    rendered_rules = dict(owned.rules)
    before_rules = _recorded_rules(recorded)
    pending_rules = _pending_rules(recorded)
    keys = [
        DocumentVerdict(
            path,
            wanted_verdict(existing, path, value, _owned(digests.get(path)), pending.get(path)),
        )
        for path, value in owned.keys
    ]
    dropped = [
        (path, dropped_verdict(existing, path, _owned(digests.get(path)), pending.get(path)))
        for path in dict.fromkeys([*digests, *pending])
        if path not in rendered_keys
    ]
    rules = [
        (
            path,
            _rule_verdict(
                existing,
                path,
                rendered_rules.get(path, ()),
                before_rules.get(path, ()),
                pending_rules.get(path, ()),
            ),
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
    pending = _pending_keys(recorded)
    document = dict(base)
    for path in _recorded_keys(recorded):
        if path not in rendered_keys:
            document = without_path(document, path)
    for path, digest in pending.items():
        if path not in rendered_keys and _holds_pending(base, path, digest):
            document = without_path(document, path)
    for path, value in owned.keys:
        document = with_path(document, path, value)
    rendered_rules = dict(owned.rules)
    before_rules = _recorded_rules(recorded)
    pending_rules = _pending_rules(recorded)
    carried: list[tuple[str, str]] = []
    owned_rules: dict[JsonPath, tuple[str, ...]] = {}
    for path in _rule_paths(owned, recorded):
        present, disk = _disk_list(base, path)
        rendered = rendered_rules.get(path, ())
        before = (*before_rules.get(path, ()), *pending_rules.get(path, ()))
        if disk is None:
            if rendered:
                raise LoadoutError(f"{display_path(path)} is not a list of rules")
            continue
        merged, kept = carry_saved_rules(rendered, disk, before)
        if merged or present:
            document = with_path(document, path, merged)
        carried.extend(_carried_entry(path, entry) for entry in kept)
        written = tuple(
            entry for entry in _ordered_unique(rendered) if entry in before or entry not in disk
        )
        if written or (path in {**before_rules, **pending_rules} and present and not merged):
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
    pending = [
        path
        for path, digest in recorded.pending_keys.items()
        if _holds_pending(existing, path, digest)
    ]
    for path in dict.fromkeys([*recorded.owned_keys, *pending]):
        present, _ = lookup(document, path)
        if present:
            document = _prune_emptied(without_path(document, path), existing, path[:-1])
    rules = {
        path: (*recorded.owned_rules.get(path, ()), *recorded.pending_rules.get(path, ()))
        for path in dict.fromkeys([*recorded.owned_rules, *recorded.pending_rules])
    }
    for path, entries in rules.items():
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
