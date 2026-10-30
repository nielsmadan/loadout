from __future__ import annotations

import ast
import copy
import itertools
import math
import re
import shlex
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from .errors import LoadoutError
from .hooks import foreign_variables
from .permissions.renderers import pi_mcp_patterns, render_codex, render_codex_project
from .permissions.rules import (
    CATCH_ALL_ENTRY,
    DECISIONS,
    EXACT_MARK,
    MCP_SEED,
    UNSTATED_DEFAULT,
    Decision,
    Rules,
    is_glob,
)
from .plugins import MARKETPLACES, PLUGINS
from .servers import Server, auth_conflict, literal_header

CATEGORIES = ("allow", "ask", "deny")


@dataclass(frozen=True)
class Note:
    """Something the document held that the source cannot represent."""

    kind: Literal["cannot", "unrecognised", "dropped"]
    detail: str


@dataclass(frozen=True)
class Extraction:
    rules: Rules
    base: dict[str, Any] = field(default_factory=dict)
    notes: tuple[Note, ...] = ()


Extractor = Callable[[Any], Extraction]


@dataclass(frozen=True)
class ValueExtraction:
    """The inverse of a `ValueSpec`: one key's content, and what did not survive.

    Deliberately not an `Extraction`. A value renderer produces one key's value
    and is handed no base, so its inverse has no `rules` to fill and **no
    residual to hold** — there is nothing it could have preserved. The inverse
    of a renderer kind is a kind.

    That is what keeps the residual computable at all. `settings.json` has four
    owners; if every extractor returned "everything except mine", each base
    would carry the others' keys. Value extractors returning none means the
    residual is produced exactly once, by the document extractor.
    """

    value: dict[str, Any]
    notes: tuple[Note, ...] = ()


ValueExtractor = Callable[[Any], ValueExtraction]


# --------------------------------------------------------------------------
# claude-mcp — three plain lists of `server/tool`, nothing else in the file.
# --------------------------------------------------------------------------


def extract_claude_mcp(document: Any) -> Extraction:
    return Extraction(
        Rules(
            mcp_allow=tuple(document.get("allow", [])),
            mcp_ask=tuple(document.get("ask", [])),
            mcp_deny=tuple(document.get("deny", [])),
        )
    )


# --------------------------------------------------------------------------
# claude / claude-project — settings.json. The three owned categories inside
# `permissions` are replaced; every other key, inside `permissions` and beside
# it, belongs to the base. The two renderers differ only in emitted key order,
# which extraction reads by name, so one inverse serves both.
# --------------------------------------------------------------------------

OWNED_CLAUDE_KEYS = ("allow", "deny", "ask")


def _exact_shaped(command: str) -> bool:
    """Whether a bare pattern reads back as an exact entry `parse_rules` accepts.

    Any `*` is a wildcard to Claude, OpenCode and Pi alike, so a pattern holding one
    is not a single command and stays where it was before exact entries existed.
    """
    return bool(command) and "*" not in command and command == command.strip()


def _claude_entry(pattern: str) -> tuple[str, str]:
    """Classify one Claude permission string as ("shell" | "mcp" | "extra", value)."""
    if pattern.startswith("Bash(") and pattern.endswith(")"):
        inner = pattern[len("Bash(") : -1]
        # `foo` renders as `Bash(foo:*)` and a glob renders literally. A source
        # entry of `foo:*` is itself a glob and renders to the same bytes as
        # `foo`; the bare reading is chosen, and both render back identically.
        if inner.endswith(":*") and not is_glob(inner[: -len(":*")]):
            return "shell", inner[: -len(":*")]
        if is_glob(inner):
            return "shell", inner
        if _exact_shaped(inner):
            return "shell", EXACT_MARK + inner
    if pattern.startswith("mcp__"):
        server, separator, tool = pattern[len("mcp__") :].partition("__")
        if separator and server and tool:
            return "mcp", f"{server}/{tool}"
    return "extra", pattern


def extract_claude(document: Any) -> Extraction:
    permissions = document.get("permissions", {})
    shell: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    mcp: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    extra: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    notes: list[Note] = []

    for category in CATEGORIES:
        for pattern in permissions.get(category, []):
            kind, value = _claude_entry(pattern)
            if kind == "extra" and category == "ask":
                # Rules has no claude_extra_ask; there is nowhere to put it.
                notes.append(Note("dropped", f"claude: unowned ask pattern {pattern!r}"))
                continue
            {"shell": shell, "mcp": mcp, "extra": extra}[kind][category].append(value)

    base = copy.deepcopy(document)
    if "permissions" in base:
        base["permissions"] = {
            key: value for key, value in base["permissions"].items() if key not in OWNED_CLAUDE_KEYS
        }

    return Extraction(
        Rules(
            allow=tuple(shell["allow"]),
            ask=tuple(shell["ask"]),
            deny=tuple(shell["deny"]),
            mcp_allow=tuple(mcp["allow"]),
            mcp_ask=tuple(mcp["ask"]),
            mcp_deny=tuple(mcp["deny"]),
            claude_extra_allow=tuple(extra["allow"]),
            claude_extra_deny=tuple(extra["deny"]),
        ),
        base,
        tuple(notes),
    )


# --------------------------------------------------------------------------
# droid — settings.json. The three top-level lists carry command prefixes only;
# MCP policy, globs, and the autonomy default have no representation here.
# --------------------------------------------------------------------------


DROID_KEYS = {
    "commandAllowlist": "allow",
    "commandDenylist": "ask",
    "commandBlocklist": "deny",
}


def extract_droid(document: Any) -> Extraction:
    if not isinstance(document, Mapping):
        return Extraction(Rules(), notes=(Note("unrecognised", "droid: document is not a map"),))

    buckets: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    notes: list[Note] = []
    for key, category in DROID_KEYS.items():
        entries = document.get(key, [])
        if not isinstance(entries, list) or not all(isinstance(entry, str) for entry in entries):
            notes.append(Note("unrecognised", f"droid: {key} is not a list of strings"))
            continue
        buckets[category].extend(entries)

    base = copy.deepcopy(dict(document))
    for key in DROID_KEYS:
        if key in base:
            # Keep the insertion slot so rendering the extracted document does
            # not move owned keys past foreign settings that followed them.
            base[key] = []
    return Extraction(
        Rules(
            allow=tuple(buckets["allow"]),
            ask=tuple(buckets["ask"]),
            deny=tuple(buckets["deny"]),
        ),
        base,
        tuple(notes),
    )


# --------------------------------------------------------------------------
# The lossy pattern forms, collapsed. Rendering is not injective: OpenCode and
# Pi each emit BOTH `foo` and `foo *` for one source entry, because neither
# matcher lets `foo *` match a bare `foo`. Extraction has to recognise the pair
# and collapse it, or the next render doubles the rule.
# --------------------------------------------------------------------------

ARGUMENT_SUFFIX = " *"


def _split_default(
    patterns: dict[str, str], label: str, *, seeded: bool = True
) -> tuple[Decision | None, dict[str, str], list[Note]]:
    """Take the `*` off a bash map that a renderer seeded.

    `[shell] default` chooses the seeded verdict, so the `*` is the catch-all
    rather than a fixed `ask`. `ask` reads back as *unstated*, since stating it
    renders the same bytes as saying nothing.

    Loadout writes the `*` first; a hand-written map may put it later. These maps
    are last-match-wins, so every entry before the `*` can never apply. Those are
    dropped and named, which keeps the effective policy: keeping them would put
    them after the `*` on the next render, where they would start to apply.

    `seeded` is False for `pi-project`, which writes a `*` only when the source
    states a default. There a written `ask` was stated and stays stated, and a map
    with no `*` is ordinary rather than missing its catch-all. No rule can be
    confused with the `*`: `parse_rules` refuses a bare `*` entry.

    An unrecognised verdict stays in the map so `_collapse_shell` reports it, the
    same contract every other decision in a bash map gets.
    """
    items = list(patterns.items())
    position = next((i for i, (pattern, _) in enumerate(items) if pattern == CATCH_ALL_ENTRY), None)
    if position is None and not seeded:
        return None, dict(patterns), []
    if position is None:
        return (
            None,
            dict(patterns),
            [Note("cannot", f"{label}: no catch-all to read a default from")],
        )
    verdict = items[position][1]
    if verdict not in DECISIONS:
        return None, dict(patterns), []
    shadowed = [
        Note(
            "cannot",
            f"{label}: {pattern!r} ({decision}) comes before the '*' catch-all, so it never "
            "applies and will be dropped",
        )
        for pattern, decision in items[:position]
    ]
    unstated = verdict == UNSTATED_DEFAULT and seeded
    return (None if unstated else verdict), dict(items[position + 1 :]), shadowed


def _drop_mcp_seed(patterns: dict[str, str]) -> dict[str, str]:
    """Pi's MCP map is seeded `*: ask` and no source key moves it.

    `[shell] default` governs bash only, so this stays the strict form: a leading
    `*` with any other verdict was not written by `render_pi` and is left for
    `_collapse_pi_mcp` to report rather than silently discarded.
    """
    items = list(patterns.items())
    if items and items[0] == (CATCH_ALL_ENTRY, MCP_SEED):
        return dict(items[1:])
    return dict(patterns)


def _order_note(patterns: dict[str, str], order: tuple[str, ...], label: str) -> list[Note]:
    """Report a map whose key order no longer says which category each rule came from.

    `render_pi` deletes a key before reassigning it, so a rule listed in two
    categories moves to the end and the map stays grouped by decision.
    `render_pi_project` and `render_opencode` assign in place, so such a key keeps
    its *first* category's position while carrying its *last* category's value —
    and no source ordering renders back to that. The effective policy still
    extracts correctly; only the byte order is lost, which is why it is reported
    rather than guessed at.
    """
    grouped = [p for category in order for p, d in patterns.items() if d == category]
    if grouped == list(patterns):
        return []
    return [
        Note("cannot", f"{label}: rule order not recoverable — a rule appears in two categories")
    ]


def _lone_deny(patterns: dict[str, str], pattern: str, decision: str, bare: str) -> bool:
    """A deny `x *` with no `x` beside it already blocks `x` on OpenCode 1.1.15+ and Pi's
    permission system 5.12.0+, so it reads as the prefix `x`: renderable on every harness
    and never blocking less. An allow or ask stays a glob, since widening one would loosen
    policy on an older version (extraction.md, collapsing the lossy pattern forms)."""
    return (
        decision == "deny"
        and bare != pattern
        and bool(bare.strip())
        and bare not in patterns
        and not is_glob(bare)
    )


def _collapse_shell(
    patterns: dict[str, str], label: str
) -> tuple[dict[str, list[str]], list[Note]]:
    buckets: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    notes: list[Note] = []
    for pattern, decision in patterns.items():
        if decision not in buckets:
            notes.append(Note("unrecognised", f"{label}: decision {decision!r} for {pattern!r}"))
            continue
        bare = pattern.removesuffix(ARGUMENT_SUFFIX)
        if bare != pattern and patterns.get(bare) == decision:
            continue
        if _lone_deny(patterns, pattern, decision, bare):
            buckets[decision].append(bare)
            continue
        # A bare form with no `foo *` companion matches only the whole command,
        # which is what an exact entry renders to.
        alone = patterns.get(pattern + ARGUMENT_SUFFIX) != decision
        buckets[decision].append(
            EXACT_MARK + pattern if alone and _exact_shaped(pattern) else pattern
        )
    return buckets, notes


def _collapse_pi_mcp(
    patterns: dict[str, str], label: str
) -> tuple[dict[str, list[str]], list[Note]]:
    """`server:tool` is the anchor — the only emitted form with an unambiguous split."""
    buckets: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    notes: list[Note] = []
    consumed: set[str] = set()
    for pattern, decision in patterns.items():
        server, separator, tool = pattern.partition(":")
        if not separator or not server or not tool:
            continue
        if decision not in buckets:
            notes.append(Note("unrecognised", f"{label}: decision {decision!r} for {pattern!r}"))
            continue
        entry = f"{server}/{tool}"
        buckets[decision].append(entry)
        consumed.update(pi_mcp_patterns(entry))
    for pattern in patterns:
        if pattern not in consumed:
            notes.append(Note("unrecognised", f"{label}: unpaired mcp pattern {pattern!r}"))
    return buckets, notes


# --------------------------------------------------------------------------
# pi / pi-project — the renderer builds the whole document, so there is no
# base to separate out. `$schema` and the top-level `permission["*"]` are
# structure, not rules.
# --------------------------------------------------------------------------


PI_ORDER = ("allow", "ask", "deny")


def _extract_pi(document: Any, label: str, seeded: bool) -> Extraction:
    """Shared body. `seeded` is the whole difference between the two scopes.

    `render_pi` always writes a bash catch-all, seeded `ask` when the source states
    none; `render_pi_project` writes one only when the source states it. The MCP
    map is seeded at global scope only.
    """
    permission = document.get("permission", {})
    raw_bash = permission.get("bash", {})
    default, bash, default_notes = _split_default(raw_bash, label, seeded=seeded)
    raw_mcp = _drop_mcp_seed(permission.get("mcp", {})) if seeded else permission.get("mcp", {})
    shell, shell_notes = _collapse_shell(bash, label)
    mcp, mcp_notes = _collapse_pi_mcp(raw_mcp, label)
    notes = (
        default_notes
        + shell_notes
        + mcp_notes
        + _order_note(bash, PI_ORDER, f"{label} bash")
        + _order_note(raw_mcp, PI_ORDER, f"{label} mcp")
    )
    return Extraction(
        Rules(
            allow=tuple(shell["allow"]),
            ask=tuple(shell["ask"]),
            deny=tuple(shell["deny"]),
            mcp_allow=tuple(mcp["allow"]),
            mcp_ask=tuple(mcp["ask"]),
            mcp_deny=tuple(mcp["deny"]),
            default=default,
        ),
        notes=tuple(notes),
    )


def extract_pi(document: Any) -> Extraction:
    return _extract_pi(document, "pi", seeded=True)


def extract_pi_project(document: Any) -> Extraction:
    return _extract_pi(document, "pi-project", seeded=False)


# --------------------------------------------------------------------------
# opencode — opencode.json. `permission` is owned whole: bash, then one key per
# MCP target, then the passthrough extras. The base keeps an empty `permission`
# so re-rendering assigns into the same position rather than appending.
# --------------------------------------------------------------------------


OPENCODE_ORDER = ("allow", "deny", "ask")


def extract_opencode(document: Any) -> Extraction:
    permission = document.get("permission", {})
    default, bash, notes = _split_default(permission.get("bash", {}), "opencode")
    shell, shell_notes = _collapse_shell(bash, "opencode")
    notes += shell_notes
    notes += _order_note(bash, OPENCODE_ORDER, "opencode bash")

    mcp: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    mcp_patterns: dict[str, str] = {}
    extra: dict[str, str] = {}
    for key, value in permission.items():
        if key == "bash":
            continue
        # `server_tool` is the emitted form; a tool name containing `_` splits
        # wrong, and an extra key containing one reads as an MCP target. Neither
        # changes the bytes on re-render.
        server, separator, tool = key.rpartition("_")
        if separator and server and tool and value in CATEGORIES:
            mcp[value].append(f"{server}/{tool}")
            mcp_patterns[key] = value
        else:
            extra[key] = value
    notes += _order_note(mcp_patterns, OPENCODE_ORDER, "opencode mcp")

    base = copy.deepcopy(document)
    if "permission" in base:
        base["permission"] = {}

    return Extraction(
        Rules(
            allow=tuple(shell["allow"]),
            ask=tuple(shell["ask"]),
            deny=tuple(shell["deny"]),
            mcp_allow=tuple(mcp["allow"]),
            mcp_ask=tuple(mcp["ask"]),
            mcp_deny=tuple(mcp["deny"]),
            opencode_extra=extra,
            default=default,
        ),
        base,
        tuple(notes),
    )


# --------------------------------------------------------------------------
# codex / codex-project — a Starlark file of prefix_rule calls. Codex appends
# `prefix_rule(pattern=[...], decision="allow")` itself, and its documentation
# spells a rule across lines with comments, so the file is parsed rather than
# matched line by line. Starlark's calls and literals are Python syntax, which
# lets `ast` read them without evaluating anything.
#
# A rule is carried only when the scope's own renderer writes it back as the
# same rule; anything else is refused by line, because a forbidden rule that
# silently vanished would loosen the user's policy.
#
# `codex` lists glob entries in a trailing comment block with no category
# attached, so they are reported and never extracted. `codex-project` runs the
# entry through shlex.split, so quoting does not survive; nothing in the
# document records that it was there.
# --------------------------------------------------------------------------

SKIPPED_MARKER = "# Skipped —"

CODEX_CATEGORY = {"allow": "allow", "forbidden": "deny", "prompt": "ask"}

# A reason and inline examples: Codex shows or validates them, and none of them
# changes which commands run, so they are dropped with a note rather than refused.
CODEX_UNCARRIED = ("justification", "match", "not_match")

CODEX_RENDERERS: dict[str, Callable[[Rules], str]] = {
    "codex": render_codex,
    "codex-project": render_codex_project,
}


@dataclass(frozen=True)
class CodexRule:
    line: int
    alternatives: tuple[tuple[str, ...], ...]
    decision: str
    dropped: tuple[str, ...]


def _join_tokens(tokens: tuple[str, ...]) -> str:
    """Rejoin a prefix_rule's tokens, re-quoting only where a plain space would not survive.

    `render_codex_project` tokenises with `shlex.split`, so a token may itself
    contain whitespace — `echo "a b"` gives `["echo", "a b"]`. Joining that on a
    space and rendering again splits the argument in two, producing a different
    document. Quoting *every* token would close the round trip too, but
    `shlex.quote` also escapes globs, so `delta run --tag=*` would come back
    respelled and stop matching the source it was extracted from.
    """
    return " ".join(shlex.quote(t) if any(c.isspace() for c in t) else t for t in tokens)


def _codex_refusal(label: str, document: str, node: ast.AST, reason: str) -> LoadoutError:
    text = ast.get_source_segment(document, node) or ""
    return LoadoutError(f"{label}: line {getattr(node, 'lineno', 0)}: {reason}: {text}")


CODEX_EXPANSION_LIMIT = 1024


def _codex_positions(value: Any) -> list[list[str]] | None:
    """The literals a pattern allows at each position; their product is every token list
    it matches."""
    if not isinstance(value, list) or not value:
        return None
    positions: list[list[str]] = []
    for element in value:
        options = [element] if isinstance(element, str) else element
        if not isinstance(options, list) or not options:
            return None
        if not all(isinstance(option, str) and option for option in options):
            return None
        positions.append(options)
    return positions


def _encodable(positions: list[list[str]]) -> bool:
    """A lone surrogate parses as a string and breaks the source when it is written."""
    try:
        for options in positions:
            for option in options:
                option.encode("utf-8")
    except UnicodeEncodeError:
        return False
    return True


def _codex_rule(label: str, document: str, statement: ast.stmt) -> CodexRule:
    call = statement.value if isinstance(statement, ast.Expr) else None
    if not (
        isinstance(call, ast.Call)
        and isinstance(call.func, ast.Name)
        and call.func.id == "prefix_rule"
    ):
        raise _codex_refusal(label, document, statement, "only prefix_rule calls can be adopted")
    if call.args or any(keyword.arg is None for keyword in call.keywords):
        raise _codex_refusal(label, document, call, "positional arguments cannot be adopted")
    values: dict[str, Any] = {}
    for keyword in call.keywords:
        assert keyword.arg is not None
        if keyword.arg not in {"pattern", "decision", *CODEX_UNCARRIED}:
            raise _codex_refusal(
                label, document, call, f"prefix_rule parameter {keyword.arg!r} cannot be adopted"
            )
        try:
            values[keyword.arg] = ast.literal_eval(keyword.value)
        # A value too deep to evaluate never parses, so parsing refused it already.
        except (ValueError, TypeError):
            raise _codex_refusal(
                label, document, call, f"{keyword.arg} must be written as a literal"
            ) from None
    positions = _codex_positions(values.get("pattern"))
    if positions is None:
        raise _codex_refusal(
            label, document, call, "pattern must be a non-empty list of strings or string lists"
        )
    if not _encodable(positions):
        raise _codex_refusal(
            label, document, call, "pattern holds a string that is not valid Unicode"
        )
    if (count := math.prod(len(options) for options in positions)) > CODEX_EXPANSION_LIMIT:
        raise _codex_refusal(
            label,
            document,
            call,
            f"pattern matches {count} token lists; at most {CODEX_EXPANSION_LIMIT} can be adopted",
        )
    alternatives = tuple(itertools.product(*positions))
    decision = values.get("decision", "allow")
    if not isinstance(decision, str) or decision not in CODEX_CATEGORY:
        raise _codex_refusal(label, document, call, f"decision {decision!r} is not one Codex reads")
    dropped = tuple(name for name in CODEX_UNCARRIED if name in values)
    return CodexRule(call.lineno, alternatives, decision, dropped)


def _codex_rules(document: str, label: str) -> list[CodexRule]:
    try:
        module = ast.parse(document)
    except SyntaxError as error:
        # A NUL has neither a line nor its text, only the parser's message.
        where = f"line {error.lineno}: " if error.lineno else ""
        detail = (error.text or "").strip() or error.msg
        raise LoadoutError(f"{label}: {where}not valid Starlark: {detail}") from None
    # A NUL is a SyntaxError on 3.13 and later; these are the parser running out.
    except (RecursionError, MemoryError) as error:
        raise LoadoutError(f"{label}: not valid Starlark: {type(error).__name__}") from None
    rules = [_codex_rule(label, document, statement) for statement in module.body]
    # Capped for the file as well as per rule: each alternative is rendered and read back.
    if (total := sum(len(rule.alternatives) for rule in rules)) > CODEX_EXPANSION_LIMIT:
        raise LoadoutError(
            f"{label}: its patterns match {total} token lists; at most "
            f"{CODEX_EXPANSION_LIMIT} can be adopted from one file"
        )
    return rules


def _carried(name: str, tokens: tuple[str, ...], decision: str) -> str | None:
    """The source entry for one rule, or None when rendering it back gives another rule."""
    entry = _join_tokens(tokens)
    single = {c: (entry,) if c == CODEX_CATEGORY[decision] else () for c in CATEGORIES}
    rendered = CODEX_RENDERERS[name](
        Rules(allow=single["allow"], ask=single["ask"], deny=single["deny"])
    )
    back = [
        (alternative, rule.decision)
        for rule in _codex_rules(rendered, name)
        for alternative in rule.alternatives
    ]
    return entry if back == [(tokens, decision)] else None


def _extract_codex(name: str, document: str) -> Extraction:
    buckets: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    notes: list[Note] = []

    in_skipped = False
    for line in document.splitlines():
        if line.startswith(SKIPPED_MARKER):
            in_skipped = True
        elif in_skipped and line.startswith("#   "):
            notes.append(
                Note("cannot", f"{name}: skipped glob {line[len('#   ') :]!r} has no category")
            )

    for rule in _codex_rules(document, name):
        entries = []
        for tokens in rule.alternatives:
            entry = _carried(name, tokens, rule.decision)
            if entry is None:
                raise LoadoutError(
                    f"{name}: line {rule.line}: the rule for {list(tokens)} cannot be carried: "
                    "loadout would write it back as a different rule"
                )
            entries.append(entry)
        buckets[CODEX_CATEGORY[rule.decision]].extend(entries)
        if rule.dropped:
            named = " or ".join(repr(entry) for entry in entries)
            notes.append(
                Note(
                    "cannot",
                    f"{name}: line {rule.line}: {', '.join(rule.dropped)} of the rule for "
                    f"{named} will be dropped, which does not change which commands run",
                )
            )

    return Extraction(
        Rules(
            allow=tuple(buckets["allow"]),
            ask=tuple(buckets["ask"]),
            deny=tuple(buckets["deny"]),
        ),
        notes=tuple(notes),
    )


def extract_codex(document: Any) -> Extraction:
    return _extract_codex("codex", document)


def extract_codex_project(document: Any) -> Extraction:
    return _extract_codex("codex-project", document)


# --------------------------------------------------------------------------
# codex-mcp — TOML, grouped by server. render_codex_mcp sorts servers and tools
# and resolves an entry listed twice to its last category, so source order does
# not survive; the emitted order is the canonical one and re-rendering is stable.
# --------------------------------------------------------------------------

CODEX_MCP_CATEGORY = {"approve": "allow", "prompt": "ask", "deny": "deny"}

CODEX_MCP_KEYS = ("enabled", "default_tools_approval_mode", "disabled_tools", "tools")


def extract_codex_mcp(document: Any) -> Extraction:
    data = tomllib.loads(document)
    buckets: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    notes: list[Note] = []

    for server, block in data.get("mcp_servers", {}).items():
        for key in block:
            if key not in CODEX_MCP_KEYS:
                notes.append(Note("unrecognised", f"codex-mcp: {server}.{key}"))
        if block.get("enabled") is False:
            buckets["deny"].append(f"{server}/*")
        elif "default_tools_approval_mode" in block:
            mode = block["default_tools_approval_mode"]
            category = CODEX_MCP_CATEGORY.get(mode)
            if category is None:
                notes.append(Note("unrecognised", f"codex-mcp: {server} default mode {mode!r}"))
            else:
                buckets[category].append(f"{server}/*")
        for tool in block.get("disabled_tools", []):
            buckets["deny"].append(f"{server}/{tool}")
        for tool, tool_block in block.get("tools", {}).items():
            mode = tool_block.get("approval_mode")
            category = CODEX_MCP_CATEGORY.get(mode)
            if category is None:
                notes.append(Note("unrecognised", f"codex-mcp: {server}/{tool} mode {mode!r}"))
                continue
            buckets[category].append(f"{server}/{tool}")

    return Extraction(
        Rules(
            mcp_allow=tuple(buckets["allow"]),
            mcp_ask=tuple(buckets["ask"]),
            mcp_deny=tuple(buckets["deny"]),
        ),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------
# hooks — the inverse of the two `ValueSpec` renderers. Both return the value of
# the `hooks` key; they differ only in whether anything else in the file has an
# owner, and in which harness's variables cannot be re-rendered.
# --------------------------------------------------------------------------


def _foreign_variable_notes(value: Any, harness: str) -> tuple[Note, ...]:
    """Hooks that extract cleanly and cannot be rendered back.

    Extraction must not apply the cross-harness variable check — refusing to
    read a hook that exists on the machine loses it, and the design principle is
    to report what cannot be represented rather than drop it. But the round trip
    then does not close: rendering refuses. So it is extracted *and* noted, which
    is what property 2's "wherever notes is empty" clause exists for.
    """
    if not isinstance(value, Mapping):
        return ()
    return tuple(
        Note("cannot", f"{found} cannot be rendered back to {harness}")
        for found in foreign_variables(value, harness)
    )


def extract_claude_hooks(document: Any) -> ValueExtraction:
    """`settings.json` -> the hooks fragment.

    Nothing is noted for the rest of the file: `settings` is the residual slice
    there, so every other key has an owner.
    """
    value = document.get("hooks", {}) if isinstance(document, Mapping) else {}
    return ValueExtraction(copy.deepcopy(dict(value)), _foreign_variable_notes(value, "claude"))


def extract_codex_hooks(document: Any) -> ValueExtraction:
    """`hooks.json` -> the hooks fragment.

    Unlike Claude's, this file has **no residual slice** — `hooks` is the only
    key loadout writes and no settings document underlies it. So a key loadout
    does not write has nowhere to go and is reported: a hand-made hooks.json may
    carry anything, and Cursor's equivalent has a `version`.
    """
    if not isinstance(document, Mapping):
        return ValueExtraction({})
    value = document.get("hooks", {})
    unowned = [key for key in document if key != "hooks"]
    notes = _foreign_variable_notes(value, "codex")
    if unowned:
        notes += (Note("cannot", f"hooks.json holds unowned key(s): {', '.join(unowned)}"),)
    return ValueExtraction(copy.deepcopy(dict(value)), notes)


def extract_droid_hooks(document: Any) -> ValueExtraction:
    if not isinstance(document, Mapping):
        return ValueExtraction({})
    value = document.get("hooks", {})
    unowned = [key for key in document if key != "hooks"]
    notes = _foreign_variable_notes(value, "droid")
    if unowned:
        notes += (Note("cannot", f"hooks.json holds unowned key(s): {', '.join(unowned)}"),)
    return ValueExtraction(copy.deepcopy(dict(value)), notes)


# --------------------------------------------------------------------------
# plugins — three inverses of one slice, and each loses a different half of the
# portable reference, because each harness can only state the half it addresses
# by. Claude and Codex carry `<name>@<marketplace>` and no source; Pi carries a
# source and no name. So `carried` is a real projection here, not the identity
# hooks got.
#
# Enablement is presence in all three. A plugin a harness lists as explicitly
# off has no representation in a fragment — removal is how a profile turns one
# off — so it is reported rather than extracted as something.
# --------------------------------------------------------------------------


def _addressed(key: str, harness: str) -> tuple[str, str] | Note:
    name, separator, marketplace = key.rpartition("@")
    if not separator or not name or not marketplace:
        return Note("unrecognised", f"{harness}: {key!r} is not <name>@<marketplace>")
    return name, marketplace


def _enablement(
    document: Mapping[str, Any], harness: str, enabled_of: Callable[[Any], Any]
) -> tuple[dict[str, Any], list[Note]]:
    """The shared half of Claude's and Codex's inverse: `<name>@<marketplace>`.

    Where the flag *sits* is the only difference — Claude's entry is the boolean
    itself, Codex's is a table holding one — so that is the parameter and the
    addressing is not duplicated.
    """
    references: dict[str, Any] = {}
    notes: list[Note] = []
    for key, entry in document.items():
        addressed = _addressed(key, harness)
        if isinstance(addressed, Note):
            notes.append(addressed)
            continue
        enabled = enabled_of(entry)
        if enabled is not True:
            notes.append(Note("cannot", f"{harness}: {key} is {enabled!r}, which is not enabled"))
            continue
        name, marketplace = addressed
        references[name] = {"marketplace": marketplace}
    return references, notes


def _fragment(marketplaces: dict[str, Any], references: dict[str, Any]) -> dict[str, Any]:
    """Sections in the order `render_codex_plugins` emits them, so re-rendering
    reproduces the file rather than a reordering of it."""
    fragment: dict[str, Any] = {}
    if marketplaces:
        fragment[MARKETPLACES] = marketplaces
    if references:
        fragment[PLUGINS] = references
    return fragment


def extract_claude_plugins(document: Any) -> ValueExtraction:
    """`settings.json` -> the plugins fragment.

    The marketplace *registration* is not here to extract: Claude keeps it in
    `known_marketplaces.json`, which loadout never reads as configuration and
    never writes (ADR 0008). A name is all `enabledPlugins` carries, and a name
    is all this recovers.
    """
    enabled = document.get("enabledPlugins", {}) if isinstance(document, Mapping) else {}
    if not isinstance(enabled, Mapping):
        return ValueExtraction({}, (Note("unrecognised", "claude: enabledPlugins is not a map"),))
    references, notes = _enablement(enabled, "claude", lambda entry: entry)
    return ValueExtraction(_fragment({}, references), tuple(notes))


def extract_droid_plugins(document: Any) -> ValueExtraction:
    if not isinstance(document, Mapping):
        return ValueExtraction({})
    enabled = document.get("enabledPlugins", {})
    if not isinstance(enabled, Mapping):
        return ValueExtraction({}, (Note("unrecognised", "droid: enabledPlugins is not a map"),))
    references, notes = _enablement(enabled, "droid", lambda entry: entry)
    raw_marketplaces = document.get("extraKnownMarketplaces", {})
    if not isinstance(raw_marketplaces, Mapping):
        notes.append(Note("unrecognised", "droid: extraKnownMarketplaces is not a map"))
        raw_marketplaces = {}
    marketplaces: dict[str, Any] = {}
    for name, block in raw_marketplaces.items():
        if not isinstance(block, Mapping):
            notes.append(Note("unrecognised", f"droid: marketplace {name!r} is not a map"))
            continue
        marketplaces[name] = copy.deepcopy(dict(block))
    used = {ref["marketplace"] for ref in references.values()}
    for name in marketplaces:
        if name not in used:
            notes.append(Note("cannot", f"droid: marketplace {name!r} is enabled by no plugin"))
    return ValueExtraction(_fragment(marketplaces, references), tuple(notes))


CODEX_PLUGIN_KEYS = ("enabled",)


def extract_codex_plugins(document: Any) -> ValueExtraction:
    """`config.toml`'s plugin tables -> the plugins fragment.

    Reads the staged file loadout renders, so it sees only the two tables this
    slice owns; a real `config.toml` carries `[projects.…]` and much else, and
    every key outside them belongs to somebody other than loadout.

    A marketplace declared and used by nothing is not in the rendered file to
    read back — `render_codex_plugins` emits only the registrations its plugins
    reach through — so that loss is the renderer's and is recorded with it.
    """
    data = tomllib.loads(document)
    tables = data.get(PLUGINS, {})
    references, notes = _enablement(tables, "codex", lambda block: block.get("enabled"))
    for key, block in tables.items():
        stray = sorted(k for k in block if k not in CODEX_PLUGIN_KEYS)
        if stray:
            notes.append(Note("unrecognised", f"codex: {key} carries {', '.join(stray)}"))
    marketplaces = {name: dict(block) for name, block in data.get(MARKETPLACES, {}).items()}
    return ValueExtraction(_fragment(marketplaces, references), tuple(notes))


def _pi_name(source: str) -> str:
    """A package's name as Pi's own source syntax spells it.

    `npm:@scope/pkg@1.2.3` and `git:github.com/user/repo@v1` both put the name in
    the last path segment ahead of the pinned ref (`docs/packages.md`, shipped
    with Pi). The ref is dropped from the *name* only — the reference keeps the
    source verbatim, so the pin survives and re-rendering is byte-identical.
    """
    body = source.partition(":")[2] or source
    segment = body.rstrip("/").rpartition("/")[2]
    pinned = segment.rfind("@")
    return segment[:pinned] if pinned > 0 else segment


def extract_pi_plugins(document: Any) -> ValueExtraction:
    """`settings.json`'s `packages` -> the plugins fragment.

    **The name is not in the document.** A Pi package is a source and nothing
    else, so the key a reference is filed under — the key a profile overlay names
    to switch the plugin off — has to be derived, and a derived identifier is an
    invention however sensible it looks. Every entry is therefore noted, and this
    is the one extractor whose document round trip closes while `notes` is not
    empty: re-rendering needs only the source, which survived exactly.

    A derivation that collides with a name already taken falls back to the source
    itself, which is unique by construction. Silently merging two packages into
    one reference would drop one of them.
    """
    packages = document.get("packages", []) if isinstance(document, Mapping) else []
    if not isinstance(packages, list):
        return ValueExtraction({}, (Note("unrecognised", "pi: packages is not a list"),))

    references: dict[str, Any] = {}
    notes: list[Note] = []
    for entry in packages:
        if isinstance(entry, str):
            source, options = entry, {}
        elif isinstance(entry, Mapping) and isinstance(entry.get("source"), str):
            source = entry["source"]
            options = {k: copy.deepcopy(v) for k, v in entry.items() if k != "source"}
            if not options:
                # `pi install` writes `{"source": x}` for a package with nothing
                # to filter — the live settings.json has one — and the renderer
                # emits the string form for that, which Pi's own docs make
                # equivalent. Equivalent is not identical, so it is reported.
                notes.append(
                    Note("cannot", f"pi: {source} filters nothing, so it renders as a string")
                )
        else:
            notes.append(Note("unrecognised", f"pi: package entry {entry!r} names no source"))
            continue
        name = _pi_name(source)
        if not name or name in references:
            name = source
        reference: dict[str, Any] = {"source": source}
        if options:
            reference["pi"] = options
        references[name] = reference
        notes.append(Note("cannot", f"pi: {source} carries no plugin name; filed as {name!r}"))
    return ValueExtraction(_fragment({}, references), tuple(notes))


# `codex-plugins` is absent deliberately, and not for want of an inverse:
# `extract_codex_plugins` above is written and pinned by
# tests/test_extract_plugins.py.
#
# The blocker is an input contract, not the type test. Every extractor here takes
# the **parsed document** its renderer's key holds; `extract_codex_plugins` takes
# **TOML text**, because `codex-plugins` is a `DocumentTextSpec` that writes a
# whole file. Registering it would make `extract_value(name, x)` mean two
# different things about `x` depending on the name, and a dict passed where text
# is expected fails somewhere less obvious than the call.
#
# So this needs a registry keyed by what a renderer *produces*, with the input
# type carried alongside — a shared invariant to change on purpose, not in
# passing. The equality in tests/test_extract_hooks.py is what enforces it today.
#
# --------------------------------------------------------------------------
# mcp server definitions — the inverses of `claude-project-servers` and
# `opencode-servers`, plus the three global-scope renderers. All but one take
# a **parsed JSON document** — `.mcp.json`'s content, opencode.json's whole
# document, the flat staged document `claude-servers` writes, and Pi's
# `mcpServers` document — the same input contract every other member of this
# registry already has, so they belong here despite `claude-project-servers`
# and `pi-servers` being `DocumentJsonSpec` rather than `ValueSpec`: unlike
# `codex-plugins`, the render-time distinction (composes into one key vs.
# owns the file outright) has no bearing on what extraction is handed.
#
# `codex-servers` is the one exception, for `codex-plugins`'s exact reason:
# it is a `DocumentTextSpec` writing TOML text, not a parsed document, so its
# inverse is written and pinned below but left out of `VALUE_EXTRACTORS` — see
# the comment above `NOT_INVERTED` in tests/test_extract_roundtrip.py.
# --------------------------------------------------------------------------

CLAUDE_SERVER_KEYS = frozenset({"type", "url", "headers", "command", "args", "env"})
OPENCODE_SERVER_KEYS = frozenset({"type", "url", "headers", "command", "environment"})

CLAUDE_BEARER = re.compile(r"^Bearer \$\{(?P<var>[^}]+)\}$")
OPENCODE_BEARER = re.compile(r"^Bearer \{env:(?P<var>[^}]+)\}$")


def _one_auth(
    auth_env_var: str | None, headers: dict[str, str], label: str, source: str
) -> tuple[dict[str, str], tuple[Note, ...]]:
    """A server authenticates one way: an Authorization header beside the auth variable
    would render as a second one, so the header is not adopted, and said so."""
    clash = auth_conflict(auth_env_var, headers)
    if clash is None:
        return headers, ()
    kept = {key: value for key, value in headers.items() if key.casefold() != "authorization"}
    return kept, (
        Note(
            "cannot", f"{label}: {clash} beside {source} is not adopted; the server keeps {source}"
        ),
    )


def _extract_headers(
    headers: Any, pattern: re.Pattern[str] | None, label: str
) -> tuple[str | None, dict[str, str], tuple[Note, ...]]:
    """An Authorization header naming one variable in the harness's reference syntax
    becomes the auth variable; every other header with a literal value is carried."""
    if not isinstance(headers, Mapping):
        return None, {}, (Note("unrecognised", f"{label}: headers is not a table"),)
    auth_env_var = None
    literal: dict[str, str] = {}
    notes: list[Note] = []
    for name, value in headers.items():
        match = (
            pattern.fullmatch(value)
            if pattern is not None and name == "Authorization" and isinstance(value, str)
            else None
        )
        if match is not None:
            auth_env_var = match["var"]
        elif literal_header(value):
            literal[name] = value
        else:
            notes.append(
                Note("cannot", f"{label}: header {name} holds a reference with no source form")
            )
    return auth_env_var, literal, tuple(notes)


def _extract_claude_server(
    name: str, entry: Any, label: str = "claude-servers"
) -> tuple[Server | None, tuple[Note, ...]]:
    notes: list[Note] = []
    if not isinstance(entry, Mapping):
        notes.append(Note("unrecognised", f"{label}: {name} is not a table"))
        return None, tuple(notes)
    stray = sorted(set(entry) - CLAUDE_SERVER_KEYS)
    if stray:
        notes.append(Note("cannot", f"{label}: {name} carries {', '.join(stray)}"))
    # Claude Code reads an entry with a command and no type as stdio.
    kind = entry.get("type", "stdio" if "command" in entry else None)
    if kind == "http":
        url = entry.get("url")
        if not isinstance(url, str):
            notes.append(Note("unrecognised", f"{label}: {name} http entry has no url"))
            return None, tuple(notes)
        auth_env_var, headers, found = _extract_headers(
            entry.get("headers", {}), CLAUDE_BEARER, f"{label}: {name}"
        )
        notes.extend(found)
        headers, found = _one_auth(auth_env_var, headers, f"{label}: {name}", "Bearer ${VAR}")
        notes.extend(found)
        return Server(
            name=name, transport="http", url=url, auth_env_var=auth_env_var, headers=headers
        ), tuple(notes)
    if kind == "stdio":
        command = entry.get("command")
        if not isinstance(command, str):
            notes.append(Note("unrecognised", f"{label}: {name} stdio entry has no command"))
            return None, tuple(notes)
        return Server(
            name=name,
            transport="stdio",
            command=command,
            args=tuple(entry.get("args", ())),
            env=dict(entry.get("env", {})),
        ), tuple(notes)
    notes.append(Note("unrecognised", f"{label}: {name} unknown type {kind!r}"))
    return None, tuple(notes)


ServerExtractor = Callable[[str, Any], tuple[Server | None, tuple[Note, ...]]]


def _collect_servers(
    config: Any, extract: ServerExtractor, not_a_table: str
) -> tuple[dict[str, Server], tuple[Note, ...]]:
    """Each server a table of entries holds, and what its entries could not carry."""
    if not isinstance(config, Mapping):
        return {}, (Note("unrecognised", not_a_table),)
    servers: dict[str, Server] = {}
    notes: list[Note] = []
    for name, entry in config.items():
        server, found = extract(name, entry)
        notes.extend(found)
        if server is not None:
            servers[name] = server
    return servers, tuple(notes)


def _unowned(document: Mapping[str, Any], label: str) -> tuple[Note, ...]:
    """A note for top-level keys beside `mcpServers` in a file nothing else owns."""
    unowned = sorted(set(document) - {"mcpServers"})
    if not unowned:
        return ()
    return (Note("cannot", f"{label} holds unowned key(s): {', '.join(unowned)}"),)


def extract_claude_servers(document: Any) -> ValueExtraction:
    """`.mcp.json` -> the servers fragment.

    The file has no other owner — `claude-project-servers` renders it whole, the
    same as `codex-hooks`'s `hooks.json` — so a stray top-level key is reported
    rather than kept: there is no base for it to belong to.
    """
    if not isinstance(document, Mapping):
        return ValueExtraction(
            {}, (Note("unrecognised", "claude-servers: document is not a table"),)
        )
    servers, notes = _collect_servers(
        document.get("mcpServers", {}),
        _extract_claude_server,
        "claude-servers: mcpServers is not a table",
    )
    return ValueExtraction(servers, (*_unowned(document, "claude-servers: .mcp.json"), *notes))


def extract_droid_servers(document: Any) -> ValueExtraction:
    if not isinstance(document, Mapping):
        return ValueExtraction(
            {}, (Note("unrecognised", "droid-servers: document is not a table"),)
        )
    servers, notes = _collect_servers(
        document.get("mcpServers", {}),
        lambda name, entry: _extract_claude_server(name, entry, "droid-servers"),
        "droid-servers: mcpServers is not a table",
    )
    return ValueExtraction(servers, (*_unowned(document, "droid-servers: mcp.json"), *notes))


def _extract_opencode_server(name: str, entry: Any) -> tuple[Server | None, tuple[Note, ...]]:
    notes: list[Note] = []
    if not isinstance(entry, Mapping):
        notes.append(Note("unrecognised", f"opencode-servers: {name} is not a table"))
        return None, tuple(notes)
    stray = sorted(set(entry) - OPENCODE_SERVER_KEYS)
    if stray:
        notes.append(Note("cannot", f"opencode-servers: {name} carries {', '.join(stray)}"))
    kind = entry.get("type")
    if kind == "remote":
        url = entry.get("url")
        if not isinstance(url, str):
            notes.append(Note("unrecognised", f"opencode-servers: {name} remote entry has no url"))
            return None, tuple(notes)
        label = f"opencode-servers: {name}"
        auth_env_var, headers, found = _extract_headers(
            entry.get("headers", {}), OPENCODE_BEARER, label
        )
        notes.extend(found)
        headers, found = _one_auth(auth_env_var, headers, label, "Bearer {env:VAR}")
        notes.extend(found)
        return Server(
            name=name, transport="http", url=url, auth_env_var=auth_env_var, headers=headers
        ), tuple(notes)
    if kind == "local":
        command = entry.get("command")
        if (
            not isinstance(command, list)
            or not command
            or not all(isinstance(c, str) for c in command)
        ):
            notes.append(
                Note("unrecognised", f"opencode-servers: {name} local entry has no command")
            )
            return None, tuple(notes)
        head, *rest = command
        return Server(
            name=name,
            transport="stdio",
            command=head,
            args=tuple(rest),
            env=dict(entry.get("environment", {})),
        ), tuple(notes)
    notes.append(Note("unrecognised", f"opencode-servers: {name} unknown type {kind!r}"))
    return None, tuple(notes)


def extract_opencode_servers(document: Any) -> ValueExtraction:
    """opencode.json -> the servers fragment, from the `mcp` key's value.

    Nothing is noted for the rest of the document: `permission` and everything
    else in opencode.json has its own owner, the same as `claude-hooks` leaves
    the rest of settings.json alone.
    """
    mcp = document.get("mcp", {}) if isinstance(document, Mapping) else {}
    servers, notes = _collect_servers(
        mcp, _extract_opencode_server, "opencode-servers: mcp is not a table"
    )
    return ValueExtraction(servers, notes)


def extract_claude_global_servers(document: Any) -> ValueExtraction:
    """The staged document `render_claude_servers` writes -> the servers fragment.

    The servers sit under `mcpServers` in `.claude.json`, as in `.mcp.json`, and each
    entry has the project-scope shape, so the entry-level logic is shared; only what
    else the file may hold differs.
    """
    if not isinstance(document, Mapping):
        return ValueExtraction(
            {}, (Note("unrecognised", "claude-servers: document is not a table"),)
        )
    # Unlike `.mcp.json`, unowned keys here are expected rather than reportable:
    # loadout owns `mcpServers` and the harness owns the hundred keys beside it.
    servers, notes = _collect_servers(
        document.get("mcpServers", {}),
        _extract_claude_server,
        "claude-servers: mcpServers is not a table",
    )
    return ValueExtraction(servers, notes)


PI_SERVER_KEYS = frozenset({"url", "bearerTokenEnv", "headers", "command", "args", "env"})


def _extract_pi_server(name: str, entry: Any) -> tuple[Server | None, tuple[Note, ...]]:
    notes: list[Note] = []
    if not isinstance(entry, Mapping):
        notes.append(Note("unrecognised", f"pi-servers: {name} is not a table"))
        return None, tuple(notes)
    stray = sorted(set(entry) - PI_SERVER_KEYS)
    if stray:
        notes.append(Note("cannot", f"pi-servers: {name} carries {', '.join(stray)}"))
    if "url" in entry:
        url = entry.get("url")
        if not isinstance(url, str):
            notes.append(Note("unrecognised", f"pi-servers: {name} http entry has no url"))
            return None, tuple(notes)
        auth_env_var = entry.get("bearerTokenEnv")
        if auth_env_var is not None and not isinstance(auth_env_var, str):
            notes.append(Note("unrecognised", f"pi-servers: {name} bearerTokenEnv is not a string"))
            auth_env_var = None
        _, headers, found = _extract_headers(entry.get("headers", {}), None, f"pi-servers: {name}")
        notes.extend(found)
        headers, found = _one_auth(auth_env_var, headers, f"pi-servers: {name}", "bearerTokenEnv")
        notes.extend(found)
        return Server(
            name=name, transport="http", url=url, auth_env_var=auth_env_var, headers=headers
        ), tuple(notes)
    if "command" in entry:
        command = entry.get("command")
        if not isinstance(command, str):
            notes.append(Note("unrecognised", f"pi-servers: {name} stdio entry has no command"))
            return None, tuple(notes)
        return Server(
            name=name,
            transport="stdio",
            command=command,
            args=tuple(entry.get("args", ())),
            env=dict(entry.get("env", {})),
        ), tuple(notes)
    notes.append(Note("unrecognised", f"pi-servers: {name} entry has neither url nor command"))
    return None, tuple(notes)


def extract_pi_servers(document: Any) -> ValueExtraction:
    """mcp.json -> the servers fragment, from the `mcpServers` key's value.

    Pi has no other owner for this file, so a stray top-level key is reported
    rather than kept — the same shape as `claude-servers`' `.mcp.json`.
    """
    if not isinstance(document, Mapping):
        return ValueExtraction({}, (Note("unrecognised", "pi-servers: document is not a table"),))
    servers, notes = _collect_servers(
        document.get("mcpServers", {}),
        _extract_pi_server,
        "pi-servers: mcpServers is not a table",
    )
    return ValueExtraction(servers, (*_unowned(document, "pi-servers: mcp.json"), *notes))


CODEX_SERVER_HTTP_KEYS = frozenset({"url", "bearer_token_env_var", "http_headers"})
CODEX_SERVER_STDIO_KEYS = frozenset({"command", "args", "env"})


def _not_adopted(name: str, keys: list[str]) -> str:
    """Keys of a Codex server that loadout's server source cannot hold, so the next sync
    writes the server without them."""
    return (
        f"codex-servers: {name} carries {', '.join(keys)}, which loadout does not adopt; "
        "the next sync writes the server without them"
    )


def _extract_codex_server(name: str, block: Any) -> tuple[Server | None, tuple[Note, ...]]:
    notes: list[Note] = []
    if not isinstance(block, Mapping):
        notes.append(Note("unrecognised", f"codex-servers: {name} is not a table"))
        return None, tuple(notes)
    if "url" in block:
        stray = sorted(set(block) - CODEX_SERVER_HTTP_KEYS)
        if stray:
            notes.append(Note("cannot", _not_adopted(name, stray)))
        url = block.get("url")
        if not isinstance(url, str):
            notes.append(Note("unrecognised", f"codex-servers: {name} http entry has no url"))
            return None, tuple(notes)
        auth_env_var = block.get("bearer_token_env_var")
        if auth_env_var is not None and not isinstance(auth_env_var, str):
            notes.append(
                Note("unrecognised", f"codex-servers: {name} bearer_token_env_var is not a string")
            )
            auth_env_var = None
        label = f"codex-servers: {name}"
        _, headers, found = _extract_headers(block.get("http_headers", {}), None, label)
        notes.extend(found)
        headers, found = _one_auth(auth_env_var, headers, label, "bearer_token_env_var")
        notes.extend(found)
        return Server(
            name=name, transport="http", url=url, auth_env_var=auth_env_var, headers=headers
        ), tuple(notes)
    if "command" in block:
        stray = sorted(set(block) - CODEX_SERVER_STDIO_KEYS)
        if stray:
            notes.append(Note("cannot", _not_adopted(name, stray)))
        command = block.get("command")
        if not isinstance(command, str):
            notes.append(Note("unrecognised", f"codex-servers: {name} stdio entry has no command"))
            return None, tuple(notes)
        env = block.get("env", {})
        return Server(
            name=name,
            transport="stdio",
            command=command,
            args=tuple(block.get("args", ())),
            env=dict(env) if isinstance(env, Mapping) else {},
        ), tuple(notes)
    notes.append(Note("unrecognised", f"codex-servers: {name} entry has neither url nor command"))
    return None, tuple(notes)


def extract_codex_servers(document: Any) -> ValueExtraction:
    """codex/config.toml's staged copy -> the servers fragment.

    Text, like `codex-mcp-permissions` and `codex-plugins`: parsed with
    `tomllib.loads` the render side never touches (ADR 0001), then walked the
    same way `extract_codex_mcp` walks its own `[mcp_servers.*]` tables — a
    different shape under the same table name, since policy and definitions
    are different keys.

    Not registered in `VALUE_EXTRACTORS` for `codex-plugins`'s exact reason —
    see the comment above this section and the one above `NOT_INVERTED` in
    tests/test_extract_roundtrip.py.
    """
    data = tomllib.loads(document)
    servers, notes = _collect_servers(
        data.get("mcp_servers", {}),
        _extract_codex_server,
        "codex-servers: mcp_servers is not a table",
    )
    return ValueExtraction(servers, notes)


VALUE_EXTRACTORS: dict[str, ValueExtractor] = {
    "claude-hooks": extract_claude_hooks,
    "codex-hooks": extract_codex_hooks,
    "droid-hooks": extract_droid_hooks,
    "claude-plugins": extract_claude_plugins,
    "droid-plugins": extract_droid_plugins,
    "pi-plugins": extract_pi_plugins,
    "claude-project-servers": extract_claude_servers,
    "droid-servers": extract_droid_servers,
    "opencode-servers": extract_opencode_servers,
    "claude-servers": extract_claude_global_servers,
    "pi-servers": extract_pi_servers,
}


EXTRACTORS: dict[str, Extractor] = {
    "claude": extract_claude,
    "claude-project": extract_claude,
    "claude-mcp-permissions": extract_claude_mcp,
    "codex": extract_codex,
    "codex-mcp-permissions": extract_codex_mcp,
    "codex-project": extract_codex_project,
    "droid": extract_droid,
    "opencode": extract_opencode,
    "pi": extract_pi,
    "pi-project": extract_pi_project,
}


def extract_value(name: str, document: Any) -> ValueExtraction:
    try:
        extractor = VALUE_EXTRACTORS[name]
    except KeyError:
        raise LoadoutError(f"no value extractor for renderer: {name}") from None
    return extractor(document)


def extract(name: str, document: Any) -> Extraction:
    try:
        extractor = EXTRACTORS[name]
    except KeyError:
        raise LoadoutError(f"no extractor for renderer: {name}") from None
    return extractor(document)
