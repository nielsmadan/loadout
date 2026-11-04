from __future__ import annotations

import json
import os
import re
import stat
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .atomic import replace_atomically
from .errors import LoadoutError
from .json_paths import JsonPath, leaf_digest, lookup, with_path, without_path
from .overlay_lock import claude_json_locks

LeafKind = Literal[
    "create",
    "unchanged",
    "update",
    "adopt",
    "retire",
    "user-defined",
    "owned-key",
    "owned-key-removed",
]


@dataclass(frozen=True)
class LeafVerdict:
    path: JsonPath
    kind: LeafKind


@dataclass(frozen=True)
class LeafResult:
    written: tuple[JsonPath, ...]
    retired: tuple[JsonPath, ...]
    refused: tuple[LeafVerdict, ...]
    digests: Mapping[JsonPath, str]


class _Retry(Exception):
    pass


_FORCEABLE: frozenset[LeafKind] = frozenset({"owned-key", "owned-key-removed"})
_WRITES: frozenset[LeafKind] = frozenset({"create", "update", *_FORCEABLE})
_SURROGATE = re.compile("[\ud800-\udfff]")


def _unparsable(path: Path) -> LoadoutError:
    return LoadoutError(
        f"{path} is not a JSON document loadout can write MCP servers into; fix the JSON in "
        f"{path} (Claude keeps backups under ~/.claude/backups), then run `loadout sync`"
    )


def claude_json_path(environ: Mapping[str, str] | None = None) -> Path:
    env = os.environ if environ is None else environ
    home = Path(env["HOME"]) if env.get("HOME") else Path.home()
    config_dir = env.get("CLAUDE_CONFIG_DIR") or ""
    if config_dir and not Path(config_dir).is_absolute():
        raise LoadoutError(f"CLAUDE_CONFIG_DIR must be an absolute path, not {config_dir}")
    parent = Path(config_dir) if config_dir else home
    path = Path(os.path.abspath(parent / ".claude.json"))
    # Claude Code 2.1.317 h_(): a legacy .config.json in the config dir wins over .claude.json.
    legacy = (Path(config_dir) if config_dir else home / ".claude") / ".config.json"
    try:
        metadata = legacy.stat()
    except OSError:
        metadata = None
    if (
        metadata is not None
        and stat.S_ISREG(metadata.st_mode)
        and (metadata.st_size > 0 or (metadata.st_mode & 0o222 and not path.exists()))
    ):
        raise LoadoutError(
            f"Claude Code reads {legacy}, not {path}; loadout writes MCP servers only into "
            ".claude.json, so move or delete the legacy file first"
        )
    if env.get("CLAUDE_CODE_CUSTOM_OAUTH_URL"):
        raise LoadoutError(
            f"Claude Code reads {parent / '.claude-custom-oauth.json'} while "
            f"CLAUDE_CODE_CUSTOM_OAUTH_URL is set, not {path}; unset it to sync overlay servers"
        )
    return path


def server_path(project: Path, server: str) -> JsonPath:
    return ("projects", str(project), "mcpServers", server)


def _wanted_verdict(
    document: Mapping[str, Any], path: JsonPath, value: Any, digests: frozenset[str] | None
) -> LeafKind:
    present, on_disk = lookup(document, path)
    if not present:
        return "owned-key-removed" if digests else "create"
    digest = leaf_digest(on_disk)
    if digest == leaf_digest(value):
        return "unchanged" if digests else "adopt"
    if not digests:
        return "user-defined"
    return "update" if digest in digests else "owned-key"


def judge_leaves(
    document: Mapping[str, Any],
    wanted: Mapping[JsonPath, Any],
    owned: Mapping[JsonPath, frozenset[str]],
    retiring: Iterable[JsonPath],
) -> tuple[LeafVerdict, ...]:
    retire: list[LeafVerdict] = []
    for path in dict.fromkeys(retiring):
        present, on_disk = lookup(document, path)
        if path in wanted or not present:
            continue
        kind: LeafKind = "retire" if leaf_digest(on_disk) in owned.get(path, ()) else "owned-key"
        retire.append(LeafVerdict(path, kind))
    return (
        *(
            LeafVerdict(path, _wanted_verdict(document, path, value, owned.get(path)))
            for path, value in wanted.items()
        ),
        *retire,
    )


def apply_leaves(
    document: Mapping[str, Any],
    wanted: Mapping[JsonPath, Any],
    retiring: Iterable[JsonPath],
    refused: Iterable[JsonPath],
) -> dict[str, Any]:
    skipped = frozenset(refused)
    result = dict(document)
    for path, value in wanted.items():
        if path not in skipped:
            result = with_path(result, path, value)
    for path in retiring:
        if path not in skipped and path not in wanted:
            result = without_path(result, path)
    return result


def leaf_refusal(verdict: LeafVerdict) -> str:
    project, server = verdict.path[1], verdict.path[-1]
    if verdict.kind == "user-defined":
        return (
            f"MCP server {server} for {project} in .claude.json was not written by loadout; "
            f"run `claude mcp remove {server} -s local` in {project}, then sync again"
        )
    if verdict.kind == "owned-key-removed":
        return f"MCP server {server} for {project} was removed outside loadout; sync with --force to restore it"
    return f"MCP server {server} for {project} was edited outside loadout; sync with --force to replace it"


def _read_bytes(path: Path) -> bytes | None:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _parse(path: Path, raw: bytes | None) -> dict[str, Any]:
    if raw is None:
        return {}
    try:
        document = json.loads(raw)
    except ValueError:
        raise _unparsable(path) from None
    if not isinstance(document, dict):
        raise _unparsable(path)
    return document


def _serialize(path: Path, document: Mapping[str, Any], raw: bytes | None) -> bytes:
    dumped = json.dumps(document, indent=2, ensure_ascii=False)
    text = _SURROGATE.sub(lambda match: f"\\u{ord(match.group()):04x}", dumped)
    try:
        return (text + "\n" if raw is not None and raw.endswith(b"\n") else text).encode()
    except UnicodeEncodeError as error:
        raise LoadoutError(f"{path} cannot be written as UTF-8: {error.reason}") from None


def _refused(verdicts: Iterable[LeafVerdict], force: bool) -> tuple[LeafVerdict, ...]:
    return tuple(
        verdict
        for verdict in verdicts
        if verdict.kind == "user-defined" or (verdict.kind in _FORCEABLE and not force)
    )


def _surviving_digest(
    document: Mapping[str, Any], path: JsonPath, digests: frozenset[str]
) -> str | None:
    present, value = lookup(document, path)
    if present and leaf_digest(value) in digests:
        return leaf_digest(value)
    return next(iter(digests)) if len(digests) == 1 else None


def _owned_after(
    document: Mapping[str, Any],
    wanted: Mapping[JsonPath, Any],
    owned: Mapping[JsonPath, frozenset[str]],
    retiring: tuple[JsonPath, ...],
    refused: tuple[LeafVerdict, ...],
) -> dict[JsonPath, str]:
    refused_paths = {verdict.path for verdict in refused}
    let_go = set(retiring) - refused_paths
    taken = {
        path: leaf_digest(value) for path, value in wanted.items() if path not in refused_paths
    }
    survivors = {
        path: _surviving_digest(document, path, digests)
        for path, digests in owned.items()
        if digests and path not in taken and path not in let_go
    }
    return {**taken, **{path: digest for path, digest in survivors.items() if digest is not None}}


def _write_once(
    path: Path,
    wanted: Mapping[JsonPath, Any],
    owned: Mapping[JsonPath, frozenset[str]],
    retiring: tuple[JsonPath, ...],
    force: bool,
) -> LeafResult:
    target = Path(os.path.realpath(path))
    raw = _read_bytes(target)
    document = _parse(path, raw)
    verdicts = judge_leaves(document, wanted, owned, retiring)
    refused = _refused(verdicts, force)
    refused_paths = {verdict.path for verdict in refused}
    acting = [verdict for verdict in verdicts if verdict.path not in refused_paths]
    result = LeafResult(
        written=tuple(v.path for v in acting if v.path in wanted and v.kind in _WRITES),
        retired=tuple(v.path for v in acting if v.path not in wanted),
        refused=refused,
        digests=_owned_after(document, wanted, owned, retiring, refused),
    )
    if not result.written and not result.retired:
        return result
    content = _serialize(path, apply_leaves(document, wanted, retiring, refused_paths), raw)
    mode = stat.S_IMODE(target.stat().st_mode) if raw is not None else 0o600

    def unchanged_since_read() -> None:
        if _read_bytes(target) != raw:
            raise _Retry

    replace_atomically(
        target,
        lambda handle: handle.write(content),
        mode=mode,
        before_rename=unchanged_since_read,
    )
    return result


def write_leaves(
    path: Path,
    wanted: Mapping[JsonPath, Any],
    owned: Mapping[JsonPath, frozenset[str]],
    retiring: Iterable[JsonPath],
    *,
    force: bool,
) -> LeafResult:
    retire = tuple(retiring)
    with claude_json_locks(path):
        for _ in range(2):
            try:
                return _write_once(path, wanted, owned, retire, force)
            except _Retry:
                continue
    raise LoadoutError(f"{path} changed while loadout held the locks; run sync again")
