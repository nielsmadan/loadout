from __future__ import annotations

import json
from datetime import date, datetime, time
from typing import Any

import tomlkit
from tomlkit.exceptions import ParseError

from .errors import LoadoutError
from .output import json_constant as _json_constant
from .output import json_object as _json_object
from .output import sha256


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
            raise TypeError("expected an object")
        return dict(data)
    except (ValueError, TypeError, ParseError) as error:
        raise LoadoutError(f"invalid partial {format_name.upper()} document: {error}") from error


def jsonc_to_json(text: str) -> str:
    """JSON with comments and trailing commas, as plain JSON: what OpenCode's
    jsonc-parser accepts, minus everything a JSON reader refuses."""
    out: list[str] = []
    index, length, in_string = 0, len(text), False
    while index < length:
        char = text[index]
        if in_string:
            out.append(text[index : index + 2] if char == "\\" else char)
            in_string = char != '"'
            index += 2 if char == "\\" else 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index)
            index = length if end == -1 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end == -1:
                raise LoadoutError("a /* comment in a JSONC document is never closed")
            index = end + 2
            continue
        if char in "}]":
            last = len(out) - 1
            while last >= 0 and out[last].isspace():
                last -= 1
            if last >= 0 and out[last] == ",":
                del out[last]
        in_string = char == '"'
        out.append(char)
        index += 1
    return "".join(out)


def key_fingerprints(
    text: str, format_name: str, owned: frozenset[str]
) -> tuple[tuple[str, str], ...]:
    data = parse_document(text, format_name)
    return tuple(
        (
            key,
            sha256(json.dumps(_typed_value(value), ensure_ascii=False, allow_nan=False).encode()),
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
