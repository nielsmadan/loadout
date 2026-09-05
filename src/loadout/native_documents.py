from __future__ import annotations

import hashlib
import json
from datetime import date, datetime, time
from typing import Any

import tomlkit
from tomlkit.exceptions import ParseError

from .artifacts import _json_constant, _json_object
from .errors import LoadoutError


def parse_document(text: str, format_name: str) -> dict[str, Any]:
    if not text.strip():
        return {}
    try:
        data = (
            json.loads(text, object_pairs_hook=_json_object, parse_constant=_json_constant)
            if format_name == "json"
            else tomlkit.parse(text).unwrap()
        )
        if not isinstance(data, dict):
            raise ValueError("expected an object")
        return dict(data)
    except (ValueError, ParseError) as error:
        raise LoadoutError(f"invalid partial {format_name.upper()} document: {error}") from error


def key_fingerprints(
    text: str, format_name: str, owned: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    data = parse_document(text, format_name)
    return tuple(
        (
            key,
            hashlib.sha256(
                json.dumps(_typed_value(value), ensure_ascii=False, allow_nan=False).encode()
            ).hexdigest(),
        )
        for key, value in data.items()
        if key in owned
    )


def _typed_value(value: Any) -> Any:
    if isinstance(value, dict):
        return ["object", [[key, _typed_value(item)] for key, item in value.items()]]
    if isinstance(value, list):
        return ["array", [_typed_value(item) for item in value]]
    if isinstance(value, float):
        return ["float", value.hex()]
    if isinstance(value, datetime | date | time):
        return [type(value).__name__, value.isoformat()]
    return [type(value).__name__, value]


def apply_document(existing: str, owned: frozenset[str], document: str, format_name: str) -> str:
    have = parse_document(existing, format_name)
    want = parse_document(document, format_name)
    current = key_fingerprints(existing, format_name, owned)
    desired = key_fingerprints(document, format_name, owned)
    if current == desired:
        return existing
    if format_name == "json":
        result = {key: value for key, value in have.items() if key not in owned}
        result.update(want)
        return json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    result_toml = tomlkit.parse(existing)
    wanted_toml = tomlkit.parse(document)
    reorder = tuple(key for key, _ in current) != tuple(key for key, _ in desired)
    for key in owned:
        if key in result_toml and (reorder or key not in want):
            del result_toml[key]
    current_values = dict(current)
    desired_values = dict(desired)
    for key in wanted_toml:
        if reorder or current_values.get(key) != desired_values.get(key):
            result_toml[key] = wanted_toml[key]
    return tomlkit.dumps(result_toml)
