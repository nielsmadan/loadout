from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from typing import Any, Literal

from .errors import LoadoutError
from .written import canonical

JsonPath = tuple[str, ...]
_BARE = re.compile(r"[A-Za-z0-9_-]+")
OwnershipKind = Literal[
    "create",
    "unchanged",
    "update",
    "adopt",
    "retire",
    "user-defined",
    "owned-key",
    "owned-key-removed",
]


def lookup(document: Mapping[str, Any], path: JsonPath) -> tuple[bool, Any]:
    current: Any = document
    for key in path:
        if not isinstance(current, Mapping) or key not in current:
            return False, None
        current = current[key]
    return True, current


def with_path(document: Mapping[str, Any], path: JsonPath, value: Any) -> dict[str, Any]:
    if not path:
        raise LoadoutError("a JSON key path must name at least one key")
    head, rest = path[0], path[1:]
    result = dict(document)
    if not rest:
        result[head] = value
        return result
    child = document.get(head, {})
    if not isinstance(child, Mapping):
        raise LoadoutError(f"cannot write {'.'.join(path)}: {head} is not a JSON object")
    result[head] = with_path(child, rest, value)
    return result


def without_path(document: Mapping[str, Any], path: JsonPath) -> dict[str, Any]:
    if not path or path[0] not in document:
        return dict(document)
    head, rest = path[0], path[1:]
    if not rest:
        return {key: value for key, value in document.items() if key != head}
    child = document[head]
    if not isinstance(child, Mapping):
        return dict(document)
    return {**document, head: without_path(child, rest)}


def leaf_digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def wanted_verdict(
    document: Mapping[str, Any],
    path: JsonPath,
    value: Any,
    owned: frozenset[str],
    pending: str | None,
) -> OwnershipKind:
    present, on_disk = lookup(document, path)
    if not present:
        return "owned-key-removed" if owned else "create"
    digest = leaf_digest(on_disk)
    if digest == leaf_digest(value):
        return "unchanged" if owned else "adopt"
    if digest == pending:
        return "update"
    if not owned:
        return "user-defined"
    return "update" if digest in owned else "owned-key"


def dropped_verdict(
    document: Mapping[str, Any], path: JsonPath, owned: frozenset[str], pending: str | None
) -> OwnershipKind | None:
    present, value = lookup(document, path)
    if not present:
        return None
    digest = leaf_digest(value)
    if digest in owned or digest == pending:
        return "retire"
    return "owned-key" if owned else None


def encode_path(path: JsonPath) -> list[str]:
    return list(path)


def decode_path(raw: object) -> JsonPath | None:
    if not isinstance(raw, list) or not raw or not all(isinstance(key, str) for key in raw):
        return None
    return tuple(raw)


def display_path(path: JsonPath) -> str:
    return "".join(
        (f".{key}" if index else key) if _BARE.fullmatch(key) else f"[{json.dumps(key)}]"
        for index, key in enumerate(path)
    )
