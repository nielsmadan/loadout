from __future__ import annotations

import copy
import json
import stat
import tomllib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import Any, Literal

import tomlkit
from tomlkit.exceptions import ParseError
from tomlkit.items import Item

from .agents import known_agents
from .destinations import resolve_destination
from .documents import merge_documents
from .errors import LoadoutError
from .permissions.merge import merge_rules
from .permissions.renderers import RENDERERS, JsonSpec, TextSpec
from .permissions.rules import EMPTY_RULES, Rules, parse_rules

ArtifactScope = Literal["project", "global"]
CATEGORIES = frozenset(
    {
        "instructions",
        "permissions",
        "mcp-permissions",
        "mcp",
        "settings",
        "hooks",
        "plugins",
        "skills",
        "module-config",
        "support",
        "templates",
    }
)
DOCUMENT_FORMATS = frozenset({"json", "toml"})
OPAQUE_FORMATS = frozenset({"copy", "tree", "text"})
MAX_MODE = 0o7777


@dataclass(frozen=True)
class Copied:
    """A file reproduced from a source path rather than rendered from rules.

    A skill is a tree and only `SKILL.md` goes through composition; the rest is
    carried across untouched. Naming the source instead of its decoded text is
    what lets a byte be a byte: `scripts/` files are executable in three skills
    today, and a mode does not survive a `str`.
    """

    source: Path
    prefix: bytes = b""
    mode: int | None = None

    def read_bytes(self) -> bytes:
        return self.prefix + self.source.read_bytes()

    def file_mode(self) -> int:
        return self.mode if self.mode is not None else stat.S_IMODE(self.source.stat().st_mode)


@dataclass(frozen=True)
class Merged:
    """Keys applied into a destination loadout does not own outright.

    Where a base cannot exist — `~/.codex/config.toml` carries project tables the
    harness writes, a block another tool manages and comments none of it survives
    a reserialise — the deletion guarantee comes from stripping declared keys at
    the destination instead (ADR 0017).

    `owned` is declared, never derived from `document`. A set derived from what is
    being written cannot express a removal: drop the last server and the root goes
    unnamed, so nothing strips it and every server survives with its approval
    intact.
    """

    owned: frozenset[str]
    document: str
    # Where the owned-key record lives, and what it should now say. Read as an
    # input and written as an output by the same slice — the shape ADR 0001
    # forbids a *renderer*, permitted here because the caller does both, exactly
    # as it does when reading a destination. `check` compares it so a stale or
    # hand-edited record is reported rather than silently changing what is
    # stripped.
    records: tuple[tuple[Path, str], ...] = ()
    format: str = "toml"
    native: bool = False
    emit_empty: bool = False


@dataclass(frozen=True)
class FrozenFile:
    content: bytes
    mode: int


Output = str | Copied | Merged | FrozenFile


@dataclass(frozen=True)
class ArtifactInput:
    source: PurePosixPath
    optional: bool = False


@dataclass(frozen=True)
class ArtifactPart:
    category: str
    inputs: tuple[ArtifactInput, ...]
    keys: tuple[str, ...] | None = None
    renderer: str | None = None
    merge: str | None = None
    layered: bool = False

    @property
    def single_input(self) -> ArtifactInput:
        if self.layered or len(self.inputs) != 1:
            raise LoadoutError(f"{self.label}: requires a single source")
        return self.inputs[0]

    @property
    def source(self) -> PurePosixPath:
        return self.single_input.source

    @property
    def label(self) -> str:
        return f"{self.category} ({', '.join(str(i.source) for i in self.inputs)})"


@dataclass(frozen=True)
class PartDocument:
    values: dict[str, Any]
    owned: tuple[str, ...]


@dataclass(frozen=True)
class Artifact:
    agents: tuple[str, ...]
    format: str
    output: PurePosixPath | None = None
    destination: str | None = None
    parts: tuple[ArtifactPart, ...] = ()
    order: tuple[str, ...] = ()
    emit_empty: bool = False
    partial: bool = False
    template_instructions: bool = False
    template_parts: bool = True
    mode: int | None = None
    modes: tuple[tuple[PurePosixPath, int], ...] = ()

    @property
    def label(self) -> str:
        return f"artifact {self.output or self.destination}"


@dataclass(frozen=True)
class Artifacts:
    source_root: Path
    path: Path
    scope: ArtifactScope
    records: tuple[Artifact, ...]
    config_path: Path | None = None

    def input_paths(self) -> tuple[Path, ...]:
        return tuple(
            dict.fromkeys(
                self.source_root / item.source
                for record in self.records
                for part in record.parts
                for item in part.inputs
            )
        )

    def agents(self, category: str | None = None) -> tuple[str, ...]:
        return tuple(
            dict.fromkeys(
                agent
                for record in self.records
                if category is None or any(p.category == category for p in record.parts)
                for agent in record.agents
            )
        )


def _string(value: object, label: str) -> str:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise LoadoutError(f"{label} must be a non-empty string without NUL bytes")
    return value


def _strings(value: object, label: str, *, empty: bool = True) -> tuple[str, ...]:
    if not isinstance(value, list):
        raise LoadoutError(f"{label} must be a list of strings")
    result = tuple(_string(v, label) for v in value)
    if len(result) != len(set(result)):
        raise LoadoutError(f"{label} contains duplicate entries")
    if not empty and not result:
        raise LoadoutError(f"{label} must not be empty")
    return result


def _boolean(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise LoadoutError(f"{label} must be a boolean")
    return value


def relative_path(value: object, label: str) -> PurePosixPath:
    raw = _string(value, label)
    path = PurePosixPath(raw)
    if path.is_absolute() or ".." in path.parts or path == PurePosixPath("."):
        raise LoadoutError(f"{label} must be a relative path inside its root: {raw!r}")
    if any(part in {".git", ".loadout-state"} for part in path.parts):
        raise LoadoutError(f"{label} overlaps protected metadata: {raw!r}")
    return path


def _unknown(data: Mapping[str, Any], allowed: set[str], label: str) -> None:
    extra = sorted(set(data) - allowed)
    if extra:
        raise LoadoutError(f"{label}: unrecognised key(s) {', '.join(extra)}")


def _part(category: str, raw: object, label: str) -> ArtifactPart:
    if category not in CATEGORIES:
        raise LoadoutError(f"{label}: unknown category {category!r}")
    if not isinstance(raw, dict):
        raise LoadoutError(f"{label}: contributor must be a table")
    _unknown(raw, {"source", "sources", "merge", "keys", "renderer", "optional"}, label)
    renderer = _string(raw["renderer"], f"{label}.renderer") if "renderer" in raw else None
    if renderer is not None and not isinstance(RENDERERS.get(renderer), JsonSpec | TextSpec):
        raise LoadoutError(f"{label}: {renderer!r} is not a permission rules renderer")
    return ArtifactPart(
        category=category,
        inputs=_inputs(raw, label),
        keys=_strings(raw["keys"], f"{label}.keys") if "keys" in raw else None,
        renderer=renderer,
        merge=_string(raw["merge"], f"{label}.merge") if "merge" in raw else None,
        layered="sources" in raw,
    )


def _input(raw: object, label: str) -> ArtifactInput:
    if not isinstance(raw, dict):
        raise LoadoutError(f"{label}: input must be a table")
    _unknown(raw, {"source", "optional"}, label)
    return ArtifactInput(
        relative_path(raw.get("source"), f"{label}.source"),
        _boolean(raw.get("optional", False), f"{label}.optional"),
    )


def _inputs(raw: dict[str, Any], label: str) -> tuple[ArtifactInput, ...]:
    if "sources" not in raw:
        return (_input({k: raw[k] for k in ("source", "optional") if k in raw}, label),)
    if "source" in raw or "optional" in raw:
        raise LoadoutError(f"{label}: sources cannot accompany source or part-level optional")
    values = raw["sources"]
    if not isinstance(values, list) or not values:
        raise LoadoutError(f"{label}.sources must be a non-empty list of input tables")
    inputs = tuple(_input(value, f"{label}.sources[{i}]") for i, value in enumerate(values))
    if len({item.source for item in inputs}) != len(inputs):
        raise LoadoutError(f"{label}.sources contains duplicate normalized paths")
    return inputs


def _record(raw: object, label: str, scope: ArtifactScope) -> Artifact:
    if not isinstance(raw, dict):
        raise LoadoutError(f"{label} must be a table")
    _unknown(
        raw,
        {
            "agents",
            "format",
            "output",
            "destination",
            "parts",
            "order",
            "emit_empty",
            "category",
            "source",
            "sources",
            "merge",
            "renderer",
            "optional",
            "partial",
            "template_instructions",
            "template_parts",
            "mode",
            "modes",
        },
        label,
    )
    agents = _strings(raw.get("agents"), f"{label}.agents", empty=False)
    unknown = sorted(set(agents) - known_agents())
    if unknown:
        raise LoadoutError(f"{label}: unknown agent(s) {', '.join(unknown)}")
    format_name = _string(raw.get("format"), f"{label}.format")
    if format_name not in DOCUMENT_FORMATS | OPAQUE_FORMATS:
        raise LoadoutError(f"{label}: unknown format {format_name!r}")
    destination_key = "output" if scope == "project" else "destination"
    other_key = "destination" if scope == "project" else "output"
    if other_key in raw or destination_key not in raw:
        raise LoadoutError(f"{label}: {scope} artifacts require only {destination_key!r}")
    output = relative_path(raw["output"], f"{label}.output") if scope == "project" else None
    destination = _string(raw["destination"], f"{label}.destination") if scope == "global" else None
    if format_name in DOCUMENT_FORMATS:
        if any(
            k in raw for k in ("category", "source", "sources", "merge", "renderer", "optional")
        ):
            raise LoadoutError(f"{label}: document contributors belong in parts")
        raw_parts = raw.get("parts")
        if not isinstance(raw_parts, dict) or not raw_parts:
            raise LoadoutError(f"{label}.parts must be a non-empty table of contributors")
        parts = tuple(_part(k, v, f"{label}.parts.{k}") for k, v in raw_parts.items())
    else:
        if "parts" in raw or "order" in raw or "partial" in raw:
            raise LoadoutError(f"{label}: {format_name} records cannot declare parts or order")
        category = _string(raw.get("category"), f"{label}.category")
        part_data = {
            k: raw[k] for k in ("source", "sources", "merge", "renderer", "optional") if k in raw
        }
        parts = (_part(category, part_data, label),)
    record = Artifact(
        agents=agents,
        format=format_name,
        output=output,
        destination=destination,
        parts=parts,
        order=_strings(raw.get("order", []), f"{label}.order"),
        emit_empty=_boolean(raw.get("emit_empty", False), f"{label}.emit_empty"),
        partial=_boolean(raw.get("partial", False), f"{label}.partial"),
        template_instructions=_boolean(
            raw.get("template_instructions", False), f"{label}.template_instructions"
        ),
        template_parts=_boolean(raw.get("template_parts", True), f"{label}.template_parts"),
        mode=_mode(raw["mode"], f"{label}.mode") if "mode" in raw else None,
        modes=_modes(raw["modes"], f"{label}.modes") if "modes" in raw else (),
    )
    if "mode" in raw and not (
        (format_name == "copy" and parts[0].renderer is None)
        or (format_name == "text" and parts[0].merge == "concat")
    ):
        raise LoadoutError(f"{label}: mode requires a copy artifact or composed text instructions")
    if "modes" in raw and format_name != "tree":
        raise LoadoutError(f"{label}: modes requires a tree artifact")
    if record.template_instructions and (
        scope != "project"
        or record.format not in {"copy", "text"}
        or len(record.parts) != 1
        or record.parts[0].category != "instructions"
        or record.parts[0].renderer is not None
        or all(item.optional for item in record.parts[0].inputs)
    ):
        raise LoadoutError(
            f"{label}: template_instructions requires a required project copy/text instruction source"
        )
    _validate_renderers(record)
    _validate_composition(record)
    return record


def _validate_composition(record: Artifact) -> None:
    for part in record.parts:
        if not part.layered:
            if part.merge is not None:
                raise LoadoutError(f"{part.label}: merge requires sources")
            continue
        if record.format in {"copy", "tree"}:
            raise LoadoutError(f"{record.label}: {record.format} requires a single source")
        if part.renderer is not None:
            if part.merge is not None:
                raise LoadoutError(f"{part.label}: permission renderer selects the rule merge")
        elif part.category in {"permissions", "mcp-permissions"}:
            raise LoadoutError(f"{part.label}: layered permissions require a portable renderer")
        elif record.format in DOCUMENT_FORMATS:
            if part.merge != "deep":
                raise LoadoutError(f"{part.label}: document sources require merge = 'deep'")
        elif part.category != "instructions" or part.merge != "concat":
            raise LoadoutError(
                f"{part.label}: text sources require instructions and merge = 'concat'"
            )


def _mode(value: object, label: str) -> int:
    if type(value) is not int or not 0 <= value <= MAX_MODE:
        raise LoadoutError(f"{label} must be an integer filesystem mode between 0 and 4095")
    return value


def _modes(value: object, label: str) -> tuple[tuple[PurePosixPath, int], ...]:
    if not isinstance(value, dict):
        raise LoadoutError(f"{label} must map relative file paths to integer modes")
    modes = tuple((relative_path(path, label), _mode(mode, label)) for path, mode in value.items())
    if len({path for path, _ in modes}) != len(modes):
        raise LoadoutError(f"{label} contains duplicate normalized paths")
    return modes


def _validate_renderers(record: Artifact) -> None:
    for part in record.parts:
        if part.renderer is None:
            continue
        spec = RENDERERS[part.renderer]
        if isinstance(spec, TextSpec) and record.format == "text":
            continue
        if isinstance(spec, JsonSpec) and record.format == "json":
            if not spec.owns_whole_file or len(record.parts) == 1:
                continue
            raise LoadoutError(f"{record.label}: renderer {part.renderer!r} owns the whole file")
        raise LoadoutError(
            f"{record.label}: renderer {part.renderer!r} cannot produce {record.format}"
        )


def _no_symlinks(path: Path, root: Path | None = None) -> None:
    current = path
    while True:
        if current.is_symlink():
            raise LoadoutError(f"artifact path is a symlink: {current}")
        if current in (root, current.parent):
            break
        current = current.parent


def _source(root: Path, relative: PurePosixPath, *, optional: bool = False) -> Path | None:
    path = root / relative
    _no_symlinks(path, root)
    if not path.resolve().is_relative_to(root.resolve()):
        raise LoadoutError(f"artifact source escapes its root: {path}")
    if not path.exists():
        if optional:
            return None
        raise LoadoutError(f"artifact source not found: {path}")
    return path


def load_artifacts(
    path: Path,
    *,
    source_root: Path | None = None,
    scope: ArtifactScope = "project",
    config_path: Path | None = None,
) -> Artifacts:
    root = (source_root or path.parent).absolute()
    path = path.absolute()
    if not path.is_relative_to(root):
        raise LoadoutError(f"artifact index escapes its source root: {path}")
    _no_symlinks(path, root)
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, tomllib.TOMLDecodeError) as error:
        raise LoadoutError(f"{path}: cannot read artifact index: {error}") from error
    _unknown(data, {"artifact"}, str(path))
    records = data.get("artifact", [])
    if not isinstance(records, list):
        raise LoadoutError(f"{path}: artifact must be an array of tables")
    return Artifacts(
        source_root=root,
        path=path,
        scope=scope,
        records=tuple(
            _record(raw, f"{path}: artifact[{i}]", scope) for i, raw in enumerate(records)
        ),
        config_path=config_path.absolute() if config_path else None,
    )


def artifact_reference(raw: object, config_path: Path, scope: ArtifactScope) -> Artifacts:
    relative = relative_path(raw, f"{config_path}: artifacts")
    return load_artifacts(
        config_path.parent / relative,
        source_root=config_path.parent,
        scope=scope,
        config_path=config_path,
    )


def artifact_destination(record: Artifact, project_root: Path | None = None) -> Path:
    if record.output is not None:
        if project_root is None:
            raise LoadoutError(
                f"{record.label}: rendering a project artifact requires project_root"
            )
        root = project_root.absolute()
        path = root / record.output
        _no_symlinks(path, root)
        if not path.resolve().is_relative_to(root.resolve()):
            raise LoadoutError(f"{record.label}: destination escapes project root: {path}")
    else:
        assert record.destination is not None
        path = resolve_destination(record.destination, record.label)
        _no_symlinks(path)
    if any(part in {".git", ".loadout-state"} for part in path.parts):
        raise LoadoutError(f"{record.label}: destination overlaps protected metadata: {path}")
    _destination_entry(path, tree=record.format == "tree")
    return path


def _destination_entry(path: Path, *, tree: bool = False) -> None:
    for parent in path.parents:
        if parent.exists() and not parent.is_dir():
            raise LoadoutError(f"artifact destination parent must be a directory: {parent}")
    if path.exists():
        expected = stat.S_ISDIR if tree else stat.S_ISREG
        if not expected(path.stat().st_mode):
            kind = "directory" if tree else "regular file"
            raise LoadoutError(f"artifact destination must be a {kind}: {path}")


def _overlaps(first: Path, second: Path) -> bool:
    return first.is_relative_to(second) or second.is_relative_to(first)


def validate_artifact_paths(
    artifacts: Artifacts,
    *,
    project_root: Path | None = None,
    occupied: Iterable[Path] = (),
    source_inputs: Iterable[Path] = (),
) -> tuple[Path, ...]:
    destinations = tuple(artifact_destination(r, project_root) for r in artifacts.records)
    claimed = {p.absolute(): "another output" for p in occupied}
    inputs = [
        artifacts.path,
        artifacts.source_root / ".loadout-state",
        *artifacts.input_paths(),
    ]
    if artifacts.config_path is not None:
        inputs.append(artifacts.config_path)
    protected = tuple(p.absolute() for p in source_inputs)
    for path, record in zip(destinations, artifacts.records, strict=True):
        for previous, owner in claimed.items():
            if _overlaps(path, previous):
                raise LoadoutError(
                    f"destination {path} is claimed by both {record.label} and {owner} ({previous})"
                )
        claimed[path] = record.label
    for destination in claimed:
        for source in inputs:
            if _overlaps(destination, source):
                raise LoadoutError(f"artifact source {source} overlaps destination {destination}")
    for destination in destinations:
        for source in protected:
            if _overlaps(destination, source):
                raise LoadoutError(f"artifact destination {destination} overlaps source {source}")
    return destinations


def _json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    document: dict[str, Any] = {}
    for key, value in pairs:
        if key in document:
            raise ValueError(f"duplicate JSON key {key!r}")
        document[key] = value
    return document


def _json_constant(value: str) -> Any:
    raise ValueError(f"invalid JSON constant {value}")


def _literal(path: Path, format_name: str) -> dict[str, Any]:
    try:
        text = path.read_text(encoding="utf-8")
        document = (
            json.loads(text, object_pairs_hook=_json_object, parse_constant=_json_constant)
            if format_name == "json"
            else tomlkit.parse(text).unwrap()
        )
    except (OSError, UnicodeError, ValueError, ParseError) as error:
        raise LoadoutError(f"{path}: invalid {format_name.upper()}: {error}") from error
    if not isinstance(document, dict):
        raise LoadoutError(f"{path}: contributor must contain a {format_name.upper()} object")
    return dict(document)


def _toml_item(value: Any) -> Item:
    if isinstance(value, dict):
        table = tomlkit.inline_table()
        for key, item in value.items():
            table.add(key, _toml_item(item))
        return table
    if isinstance(value, list):
        array = tomlkit.array()
        for item in value:
            array.append(_toml_item(item))
        return array
    result: Item = tomlkit.item(value)
    return result


def compose_document(
    artifact: Artifact, documents: Iterable[Mapping[str, Any] | PartDocument]
) -> str | None:
    merged: dict[str, Any] = {}
    owners: dict[str, ArtifactPart] = {}
    contributors = tuple(
        (part, value if isinstance(value, PartDocument) else prepare_document(part, dict(value)))
        for part, value in zip(artifact.parts, documents, strict=True)
    )
    for part, document in contributors:
        keys = document.owned
        for key in keys:
            if key in owners:
                previous = owners[key]
                raise LoadoutError(
                    f"{artifact.label}: key {key!r} is claimed by both "
                    f"{previous.label} and {part.label}"
                )
            owners[key] = part
    for part, document in contributors:
        keys = document.owned
        undeclared = set(document.values) - set(keys)
        if undeclared:
            for key in sorted(undeclared):
                if key in owners:
                    previous = owners[key]
                    raise LoadoutError(
                        f"{artifact.label}: key {key!r} from {part.label} is owned by "
                        f"{previous.label}"
                    )
            raise LoadoutError(
                f"{artifact.label}: {part.label} contains keys outside its ownership: "
                f"{', '.join(sorted(undeclared))}"
            )
        merged.update(copy.deepcopy(document.values))
    if not merged and not artifact.emit_empty:
        return None
    ordered = dict.fromkeys((*artifact.order, *merged))
    output = {key: merged[key] for key in ordered if key in merged}
    if artifact.format == "json":
        return json.dumps(output, indent=2, ensure_ascii=False, allow_nan=False) + "\n"
    result = tomlkit.document()
    for key, value in output.items():
        result.add(key, _toml_item(value))
    return tomlkit.dumps(result)


def _owned_keys(part: ArtifactPart, document: Mapping[str, Any]) -> tuple[str, ...]:
    if part.keys is not None:
        return part.keys
    if part.renderer is not None:
        spec = RENDERERS[part.renderer]
        assert isinstance(spec, JsonSpec)
        return tuple(spec.fn(EMPTY_RULES, {}))
    return tuple(document)


def prepare_document(
    part: ArtifactPart, values: dict[str, Any], reserved: tuple[str, ...] = ()
) -> PartDocument:
    owned = _owned_keys(part, values)
    if part.keys is None and part.renderer is None:
        owned = tuple(dict.fromkeys((*reserved, *owned)))
    return PartDocument(values, owned)


def _documents(artifacts: Artifacts, artifact: Artifact) -> tuple[PartDocument, ...]:
    return tuple(part_document(artifacts, artifact, part) for part in artifact.parts)


def part_document(
    artifacts: Artifacts, artifact: Artifact, part: ArtifactPart, *, rules: Rules = EMPTY_RULES
) -> PartDocument:
    if part.renderer is not None:
        spec = RENDERERS[part.renderer]
        assert isinstance(spec, JsonSpec)
        native, present = part_rules(artifacts, part)
        combined = merge_rules(rules, native) if rules != EMPTY_RULES else native
        values = (
            spec.fn(combined, {})
            if (present or rules != EMPTY_RULES)
            and (combined != EMPTY_RULES or artifact.emit_empty)
            else {}
        )
        return prepare_document(part, values)
    documents = []
    for path in input_files(artifacts, part):
        value = _literal(path, artifact.format)
        if part.layered and part.keys is not None and (extra := set(value) - set(part.keys)):
            raise LoadoutError(
                f"{part.label}: input {path} contains keys outside its ownership: "
                f"{', '.join(sorted(extra))}"
            )
        documents.append(value)
    values = merge_documents(*documents) if part.layered else next(iter(documents), {})
    reserved = tuple(dict.fromkeys(key for document in documents for key in document))
    return prepare_document(part, values, reserved)


def input_files(artifacts: Artifacts, part: ArtifactPart) -> tuple[Path, ...]:
    paths = []
    for item in part.inputs:
        path = _source(artifacts.source_root, item.source, optional=item.optional)
        if path is not None:
            _regular_file(path)
            paths.append(path)
    return tuple(paths)


def part_rules(artifacts: Artifacts, part: ArtifactPart) -> tuple[Rules, bool]:
    paths = input_files(artifacts, part)
    tiers = tuple(parse_rules(path) for path in paths)
    rules = merge_rules(*tiers) if part.layered else next(iter(tiers), EMPTY_RULES)
    return rules, bool(paths)


def _composed_instructions(
    artifacts: Artifacts, artifact: Artifact, destination: Path, prefix: bytes
) -> dict[Path, Output]:
    blocks = []
    for path in input_files(artifacts, artifact.parts[0]):
        try:
            body = path.read_text(encoding="utf-8").strip()
        except UnicodeError as error:
            raise LoadoutError(f"instruction input must be UTF-8: {path}") from error
        if body:
            blocks.append(body)
    body_bytes = ("\n\n".join(blocks) + "\n").encode() if blocks else b""
    content = (prefix if artifact.template_instructions else b"") + body_bytes
    if not content and not artifact.emit_empty:
        return {}
    output = (
        FrozenFile(content, artifact.mode) if artifact.mode is not None else content.decode("utf-8")
    )
    return {destination: output}


def _regular_file(path: Path) -> None:
    if not stat.S_ISREG(path.stat().st_mode):
        raise LoadoutError(f"artifact source must be a regular file: {path}")


def _opaque(
    artifacts: Artifacts, artifact: Artifact, destination: Path, instruction_prefix: bytes
) -> dict[Path, Output]:
    part = artifact.parts[0]
    if artifact.format == "text" and part.renderer is not None:
        spec = RENDERERS[part.renderer]
        assert isinstance(spec, TextSpec)
        rules, present = part_rules(artifacts, part)
        if not present or (rules == EMPTY_RULES and not artifact.emit_empty):
            return {}
        return {destination: spec.fn(rules)}
    authored = part.single_input
    path = _source(artifacts.source_root, authored.source, optional=authored.optional)
    if path is None:
        return {}
    if artifact.format == "tree":
        if not path.is_dir():
            raise LoadoutError(f"artifact tree source must be a directory: {path}")
        outputs: dict[Path, Output] = {}
        modes = dict(artifact.modes)
        for item in sorted(path.rglob("*")):
            if any(part in {".git", ".loadout-state"} for part in item.relative_to(path).parts):
                raise LoadoutError(f"artifact tree contains protected metadata: {item}")
            _no_symlinks(item, artifacts.source_root)
            if item.is_dir():
                continue
            _regular_file(item)
            if item.name != ".gitkeep":
                target = destination / item.relative_to(path)
                _no_symlinks(target)
                _destination_entry(target)
                outputs[target] = Copied(
                    item, mode=modes.get(PurePosixPath(item.relative_to(path)))
                )
        return outputs
    _regular_file(path)
    prefix = instruction_prefix if artifact.template_instructions else b""
    if path.stat().st_size == 0 and not artifact.emit_empty and not prefix:
        return {}
    return {destination: Copied(path, prefix, artifact.mode)}


def render_artifacts(
    artifacts: Artifacts,
    *,
    project_root: Path | None = None,
    occupied: Iterable[Path] = (),
    source_inputs: Iterable[Path] = (),
    instruction_prefix: bytes = b"",
) -> dict[Path, Output]:
    destinations = validate_artifact_paths(
        artifacts, project_root=project_root, occupied=occupied, source_inputs=source_inputs
    )
    outputs: dict[Path, Output] = {}
    for artifact, destination in zip(artifacts.records, destinations, strict=True):
        outputs.update(render_artifact(artifacts, artifact, destination, instruction_prefix))
    return outputs


def render_artifact(
    artifacts: Artifacts, artifact: Artifact, destination: Path, instruction_prefix: bytes = b""
) -> dict[Path, Output]:
    if artifact.format in DOCUMENT_FORMATS:
        return render_document(artifact, destination, _documents(artifacts, artifact))
    if artifact.parts[0].merge == "concat":
        return _composed_instructions(artifacts, artifact, destination, instruction_prefix)
    return _opaque(artifacts, artifact, destination, instruction_prefix)


def render_document(
    artifact: Artifact, destination: Path, documents: tuple[PartDocument, ...]
) -> dict[Path, Output]:
    document = compose_document(artifact, documents)
    if artifact.partial:
        owned = frozenset(key for prepared in documents for key in prepared.owned)
        return {
            destination: Merged(
                owned,
                document or "",
                format=artifact.format,
                native=True,
                emit_empty=artifact.emit_empty,
            )
        }
    return {destination: document} if document is not None else {}
