from __future__ import annotations

import json
import re
import tomllib
from collections.abc import Iterator, Mapping
from dataclasses import dataclass, field
from io import StringIO
from typing import Any

from .errors import LoadoutError

KeyPath = tuple[str, ...]
_KEY = r"""(?:[A-Za-z0-9_-]+|"(?:[^"\\\r\n]|\\.)*"|'[^'\r\n]*')"""
_PATH = rf"{_KEY}(?:\s*\.\s*{_KEY})*"
_HEADER = re.compile(rf"^\s*(\[\[?)\s*({_PATH})\s*(\]\]?)\s*(?:#.*)?$")
_ASSIGNMENT = re.compile(rf"^\s*({_PATH})\s*=")


def key_path(text: str) -> KeyPath:
    if not re.fullmatch(_PATH, text.strip()):
        raise LoadoutError(f"invalid managed TOML key path: {text!r}")
    try:
        return tuple(
            next(iter(tomllib.loads(f"{part.group()} = 0"))) for part in re.finditer(_KEY, text)
        )
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"invalid managed TOML key path: {text!r}") from error


def key_name(path: KeyPath) -> str:
    return ".".join(
        part if re.fullmatch(r"[A-Za-z0-9_-]+", part) else json.dumps(part, ensure_ascii=False)
        for part in path
    )


def contains(parent: KeyPath, child: KeyPath) -> bool:
    return child[: len(parent)] == parent


def overlapping_keys(left: frozenset[str], right: frozenset[str]) -> frozenset[str]:
    parsed_left = {key: key_path(key) for key in left}
    parsed_right = tuple(key_path(key) for key in right)
    return frozenset(
        key
        for key, path in parsed_left.items()
        if any(contains(path, other) or contains(other, path) for other in parsed_right)
    )


def leaf_values(values: Mapping[str, Any], prefix: KeyPath = ()) -> Iterator[tuple[KeyPath, Any]]:
    for key, value in values.items():
        path = (*prefix, key)
        if isinstance(value, dict):
            yield from leaf_values(value, path)
        else:
            yield path, value


@dataclass
class Entry:
    text: str
    path: KeyPath = ()


@dataclass
class Section:
    path: KeyPath = ()
    header: str = ""
    array: bool = False
    entries: list[Entry] = field(default_factory=list)

    def text(self) -> str:
        return self.header + "".join(entry.text for entry in self.entries)

    def append(self, entry: Entry) -> None:
        if self.header and not self.header.endswith("\n"):
            self.header += "\n"
        position = len(self.entries)
        while position and not self.entries[position - 1].text.strip():
            position -= 1
        if position and not self.entries[position - 1].text.endswith("\n"):
            self.entries[position - 1].text += "\n"
        self.entries.insert(position, entry)


@dataclass
class _ValueScan:
    quote: str = ""
    depth: int = 0

    def consume(self, line: str, start: int = 0) -> None:
        position = start
        while position < len(line):
            char = line[position]
            if self.quote:
                if self.quote[0] == '"' and char == "\\":
                    position += 2
                    continue
                if line.startswith(self.quote, position):
                    position += len(self.quote)
                    if self.quote in ('"""', "'''"):
                        while position < len(line) and line[position] == self.quote[0]:
                            position += 1
                    self.quote = ""
                    continue
            elif char == "#":
                break
            elif char in "\"'":
                self.quote = char * 3 if line.startswith(char * 3, position) else char
                position += len(self.quote)
                continue
            elif char in "[{":
                self.depth += 1
            elif char in "]}":
                self.depth -= 1
            position += 1


def _statement(first: str, start: int, lines: Iterator[str]) -> str:
    scan = _ValueScan()
    scan.consume(first, start)
    parts = [first]
    while scan.quote or scan.depth:
        line = next(lines, None)
        if line is None:
            raise LoadoutError("cannot locate the end of a TOML assignment")
        parts.append(line)
        scan.consume(line)
    return "".join(parts)


def sections(text: str) -> list[Section]:
    try:
        tomllib.loads(text)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"cannot edit invalid TOML: {error}") from error
    result = [Section()]
    lines = iter(StringIO(text))
    for line in lines:
        header = _HEADER.fullmatch(line.rstrip("\r\n"))
        if header:
            result.append(Section(key_path(header[2]), line, header[1] == "[["))
            continue
        assignment = _ASSIGNMENT.match(line)
        if assignment is None:
            result[-1].entries.append(Entry(line))
            continue
        statement = _statement(line, assignment.end(), lines)
        result[-1].entries.append(Entry(statement, (*result[-1].path, *key_path(assignment[1]))))
    return result


def _owned(path: KeyPath, owned: tuple[KeyPath, ...]) -> bool:
    return bool(path) and any(contains(key, path) for key in owned)


def _replacement(entry: Entry, desired: Entry) -> Entry:
    have = _ASSIGNMENT.match(entry.text)
    want = _ASSIGNMENT.match(desired.text)
    assert have is not None and want is not None
    return Entry(entry.text[: have.end()] + desired.text[want.end() :], entry.path)


def _merge_entries(
    section: Section, desired: dict[KeyPath, Entry], owned: tuple[KeyPath, ...]
) -> None:
    kept: list[Entry] = []
    for entry in section.entries:
        if _owned(entry.path, owned):
            if replacement := desired.pop(entry.path, None):
                kept.append(_replacement(entry, replacement))
        else:
            if entry.path and any(contains(entry.path, path) for path in owned):
                raise LoadoutError(
                    f"cannot manage a child of inline or scalar key {key_name(entry.path)!r}; "
                    "expand it into a TOML table first"
                )
            kept.append(entry)
    section.entries = kept


def _can_open(path: KeyPath, existing: list[Section]) -> bool:
    return not any(
        entry.path and contains(path, entry.path) and contains(section.path, path)
        for section in existing
        for entry in section.entries
    )


def _insert_pending(
    kept: list[Section], pending: dict[KeyPath, Entry], desired: list[Section]
) -> None:
    for section in desired:
        entries = [entry for entry in section.entries if entry.path in pending]
        if not entries:
            continue
        matches = [have for have in kept if have.path == section.path and not have.array]
        if matches:
            target = matches[0]
        elif _can_open(section.path, kept):
            target = Section(section.path, section.header)
            kept.append(target)
        else:
            target = max(
                (have for have in kept if not have.array and contains(have.path, section.path)),
                key=lambda have: len(have.path),
            )
        for entry in entries:
            match = _ASSIGNMENT.match(entry.text)
            assert match is not None
            relative = entry.path[len(target.path) :]
            text = key_name(relative) + " =" + entry.text[match.end() :]
            target.append(Entry(text, entry.path))
            del pending[entry.path]


def _section_suffix(section: Section) -> str:
    suffix = ""
    for entry in reversed(section.entries):
        if entry.text.strip():
            break
        suffix = entry.text + suffix
    return suffix


def _replace_owned_sections(
    section: Section, full: list[Section], array_groups: dict[KeyPath, str]
) -> Section | None:
    owner = next((path for path in array_groups if contains(path, section.path)), None)
    matches = (
        [candidate for candidate in full if contains(owner, candidate.path)]
        if owner is not None
        else [
            candidate
            for candidate in full
            if candidate.path == section.path and candidate.array == section.array
        ][:1]
    )
    if not matches:
        return None
    for match in matches:
        full.remove(match)
    text = "\n\n".join(match.text().rstrip("\r\n") for match in matches) + "\n"
    suffix = array_groups[owner] if owner is not None else _section_suffix(section)
    return Section(section.path, text + suffix, section.array)


def _join_sections(blocks: list[Section], original_sections: set[int]) -> str:
    result = ""
    for section in blocks:
        text = section.text()
        if not text:
            continue
        if id(section) not in original_sections:
            text = text.rstrip("\r\n") + "\n"
            if result:
                result += "\n" if result.endswith("\n") else "\n\n"
        result += text
    return result


def apply_paths(existing: str, owned_names: frozenset[str], document: str) -> str:
    paths = sorted(key_path(name) for name in owned_names)
    owned = tuple(
        path
        for path in paths
        if not any(other != path and contains(other, path) for other in paths)
    )
    have = sections(existing)
    want = sections(document if document.endswith("\n") else document + "\n")
    desired = {
        entry.path: entry
        for section in want
        if not _owned(section.path, owned)
        for entry in section.entries
        if entry.path
    }
    full = [section for section in want if _owned(section.path, owned)]
    array_groups: dict[KeyPath, str] = {}
    for owner in owned:
        if any(section.array and contains(owner, section.path) for section in (*have, *want)):
            previous = [section for section in have if contains(owner, section.path)]
            array_groups[owner] = _section_suffix(previous[-1]) if previous else ""
    kept: list[Section] = []
    arrays: list[KeyPath] = []
    for section in have:
        if section.array:
            arrays.append(section.path)
        if _owned(section.path, owned):
            if replacement := _replace_owned_sections(section, full, array_groups):
                kept.append(replacement)
            continue
        for array in arrays:
            if contains(array, section.path) and any(contains(array, key) for key in owned):
                raise LoadoutError(
                    f"cannot manage individual keys inside array table {key_name(array)!r}"
                )
        _merge_entries(section, desired, owned)
        kept.append(section)
    original_sections = {id(section) for section in kept}
    _insert_pending(kept, desired, want)
    kept.extend(full)
    result = _join_sections(kept, original_sections)
    try:
        tomllib.loads(result)
    except tomllib.TOMLDecodeError as error:
        raise LoadoutError(f"cannot safely apply managed TOML keys: {error}") from error
    return result
