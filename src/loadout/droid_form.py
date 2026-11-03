"""A droid file in the form Droid writes it: js-yaml 4's `dump`, unfolded and double-quoted."""

from __future__ import annotations

import math
import re
from collections.abc import Mapping, Sequence

# Droid writes these keys in this order and drops every other one.
KEY_ORDER = ("name", "description", "model", "reasoningEffort", "tools", "mcpServers", "version")

_COMPAT = frozenset(
    {"y", "Y", "yes", "Yes", "YES", "on", "On", "ON", "n", "N", "no", "No", "NO"}
    | {"off", "Off", "OFF"}
)
_SEXAGESIMAL = re.compile(r"[-+]?[0-9_]+(?::[0-9_]+)+(?:\.[0-9_]*)?")
_ESCAPES = {
    0x00: "\\0",
    0x07: "\\a",
    0x08: "\\b",
    0x09: "\\t",
    0x0A: "\\n",
    0x0B: "\\v",
    0x0C: "\\f",
    0x0D: "\\r",
    0x1B: "\\e",
    0x22: '\\"',
    0x5C: "\\\\",
    0x85: "\\N",
    0xA0: "\\_",
    0x2028: "\\L",
    0x2029: "\\P",
}
_BOM = 0xFEFF
_FIRST_UNSAFE = frozenset(map(ord, "-?:,[]{}#&*!|=>'\"%@`"))
_FLOAT = re.compile(
    r"[-+]?[0-9]+(?:\.[0-9]*)?(?:[eE][-+]?[0-9]+)?|\.[0-9]+(?:[eE][-+]?[0-9]+)?"
    r"|[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)"
)
_FLOAT_SPECIAL = re.compile(r"[-+]?\.(?:inf|Inf|INF)|\.(?:nan|NaN|NAN)")
_DATE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_DATETIME = re.compile(
    r"[0-9]{4}-[0-9][0-9]?-[0-9][0-9]?(?:[Tt]|[ \t]+)[0-9][0-9]?:[0-9]{2}:[0-9]{2}"
    r"(?:\.[0-9]*)?(?:[ \t]*(?:Z|[-+][0-9][0-9]?(?::[0-9]{2})?))?"
)
_DIGITS = {2: "01", 8: "01234567", 10: "0123456789", 16: "0123456789abcdefABCDEF"}
JS_WHITESPACE = (
    "\t\n\v\f\r \u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
    "\u2028\u2029\u202f\u205f\u3000\ufeff"
)
_PRINTABLE = ((0x20, 0x7E), (0xA1, 0xD7FF), (0xE000, 0xFFFD), (0x10000, 0x10FFFF))
_NOT_PRINTABLE = frozenset({0x2028, 0x2029, _BOM})
_HASH, _COLON, _NEWLINE = ord("#"), ord(":"), ord("\n")
_BYTE, _BMP = 0xFF, 0xFFFF


def _printable(code: int) -> bool:
    return code not in _NOT_PRINTABLE and any(low <= code <= high for low, high in _PRINTABLE)


def _white(code: int | None) -> bool:
    return code in (0x20, 0x09)


def _char_or_white(code: int | None) -> bool:
    return code is not None and _printable(code) and code not in (0x0D, _NEWLINE)


def _plain_safe(code: int, previous: int | None) -> bool:
    char = _char_or_white(code)
    nonwhite = char and not _white(code)
    return (
        (char and code != _HASH and not (previous == _COLON and not nonwhite))
        or (_char_or_white(previous) and not _white(previous) and code == _HASH)
        or (previous == _COLON and nonwhite)
    )


def _finite(number: float) -> bool:
    try:
        return math.isfinite(float(number))
    except OverflowError:
        return False


def _integer(text: str) -> bool:
    digits = text[1:] if text[:1] in ("-", "+") else text
    if digits == "0":
        return True
    base = {"b": 2, "o": 8, "x": 16}.get(digits[1:2]) if digits[:1] == "0" else None
    body = digits[2:] if base else digits
    base = base or 10
    return bool(body) and all(c in _DIGITS[base] for c in body) and _finite(int(body, base))


def _float(text: str) -> bool:
    if _FLOAT.fullmatch(text) is None:
        return False
    try:
        if math.isfinite(float(text)):
            return True
    except ValueError:
        pass
    return _FLOAT_SPECIAL.fullmatch(text) is not None


def _reads_as_another_type(text: str) -> bool:
    return (
        text in {"~", "null", "Null", "NULL", "true", "True", "TRUE", "false", "False", "FALSE"}
        or text == "<<"
        or _integer(text)
        or _float(text)
        or _DATE.fullmatch(text) is not None
        or _DATETIME.fullmatch(text) is not None
    )


def _escaped(char: str) -> str:
    code = ord(char)
    if code in _ESCAPES:
        return _ESCAPES[code]
    if _printable(code):
        return char
    letter, width = ("x", 2) if code <= _BYTE else ("u", 4) if code <= _BMP else ("U", 8)
    return f"\\{letter}{code:0{width}X}"


def _literal(text: str, indent: int) -> str:
    """A `|` block; the source refuses text that would need `+` chomping or an indent indicator."""
    lines = text.removesuffix("\n").split("\n")
    return ("|" if text.endswith("\n") else "|-") + "".join(
        f"\n{' ' * indent}{line}" for line in lines
    )


def scalar(text: str, indent: int = 2) -> str:
    """One string value as Droid's dump writes it, `indent` spaces under its key."""
    if not text:
        return '""'
    if text in _COMPAT or _SEXAGESIMAL.fullmatch(text):
        return f'"{text}"'
    plain = ord(text[0]) not in _FIRST_UNSAFE and _char_or_white(ord(text[0]))
    plain = plain and not _white(ord(text[0])) and not _white(ord(text[-1]))
    plain = plain and ord(text[-1]) != _COLON
    breaks = False
    previous: int | None = None
    for char in text:
        code = ord(char)
        if code == _NEWLINE:
            breaks = True
        elif not _printable(code):
            return '"' + "".join(map(_escaped, text)) + '"'
        plain = plain and _plain_safe(code, previous)
        previous = code
    if breaks:
        return _literal(text, indent)
    if plain and not _reads_as_another_type(text):
        return text
    return '"' + "".join(map(_escaped, text)) + '"'


def _entry(key: str, value: str | Sequence[str]) -> str:
    if isinstance(value, str):
        return f"{key}: {scalar(value)}"
    if not value:
        return f"{key}: []"
    return f"{key}:" + "".join(f"\n  - {scalar(item, 4)}" for item in value)


def document(fields: Mapping[str, str | Sequence[str] | None], body: str) -> str:
    """The whole droid file; `body` is trimmed as Droid trims it on read."""
    entries = [_entry(key, value) for key in KEY_ORDER if (value := fields.get(key)) is not None]
    return "---\n" + "\n".join(entries) + "\n---\n" + body.strip(JS_WHITESPACE)
