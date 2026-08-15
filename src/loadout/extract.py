from __future__ import annotations

import copy
import json
import re
import shlex
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from .errors import LoadoutError
from .hooks import foreign_variables
from .permissions.renderers import pi_mcp_patterns
from .permissions.rules import Rules, dedupe, is_glob

CATEGORIES = ("allow", "ask", "deny")


@dataclass(frozen=True)
class Note:
    """Something the document held that the source cannot represent."""

    kind: str
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
                notes.append(Note("cannot", f"claude: unowned ask pattern {pattern!r}"))
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
# The lossy pattern forms, collapsed. Rendering is not injective: OpenCode and
# Pi each emit BOTH `foo` and `foo *` for one source entry, because neither
# matcher lets `foo *` match a bare `foo`. Extraction has to recognise the pair
# and collapse it, or the next render doubles the rule.
# --------------------------------------------------------------------------

ARGUMENT_SUFFIX = " *"


def _drop_seed(patterns: dict[str, str]) -> dict[str, str]:
    """Both renderers seed their map with `*: ask`; that is not a source rule.

    A source rule for `*` overwrites the seed's value, and under Pi's
    delete-then-reassign it also moves off first position — so only `*: ask`
    still at index 0 is the seed. A source whose only rule is `*` in ask renders
    to exactly the seed and reads back as no rule; the document is unchanged
    either way.
    """
    items = list(patterns.items())
    if items and items[0] == ("*", "ask"):
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
        buckets[decision].append(pattern)
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


def extract_pi(document: Any) -> Extraction:
    permission = document.get("permission", {})
    bash = _drop_seed(permission.get("bash", {}))
    raw_mcp = _drop_seed(permission.get("mcp", {}))
    shell, shell_notes = _collapse_shell(bash, "pi")
    mcp, mcp_notes = _collapse_pi_mcp(raw_mcp, "pi")
    notes = (
        shell_notes
        + mcp_notes
        + _order_note(bash, PI_ORDER, "pi bash")
        + _order_note(raw_mcp, PI_ORDER, "pi mcp")
    )
    return Extraction(
        Rules(
            allow=tuple(shell["allow"]),
            ask=tuple(shell["ask"]),
            deny=tuple(shell["deny"]),
            mcp_allow=tuple(mcp["allow"]),
            mcp_ask=tuple(mcp["ask"]),
            mcp_deny=tuple(mcp["deny"]),
        ),
        notes=tuple(notes),
    )


# --------------------------------------------------------------------------
# opencode — opencode.json. `permission` is owned whole: bash, then one key per
# MCP target, then the passthrough extras. The base keeps an empty `permission`
# so re-rendering assigns into the same position rather than appending.
# --------------------------------------------------------------------------


OPENCODE_ORDER = ("allow", "deny", "ask")


def extract_opencode(document: Any) -> Extraction:
    permission = document.get("permission", {})
    bash = _drop_seed(permission.get("bash", {}))
    shell, notes = _collapse_shell(bash, "opencode")
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
        ),
        base,
        tuple(notes),
    )


# --------------------------------------------------------------------------
# codex / codex-project — Starlark-ish text, one prefix_rule per line. Both
# scopes emit the same decision vocabulary, so one parser serves both.
#
# `codex` lists glob entries in a trailing comment block with no category
# attached, so they are reported and never extracted. `codex-project` runs the
# entry through shlex.split, so quoting does not survive; nothing in the
# document records that it was there.
# --------------------------------------------------------------------------

PREFIX_RULE = re.compile(r'^prefix_rule\(pattern = \[(?P<tokens>.*)\], decision = "(?P<d>\w+)"\)$')

SKIPPED_MARKER = "# Skipped —"

CODEX_CATEGORY = {"allow": "allow", "forbidden": "deny", "prompt": "ask"}


def _join_tokens(tokens: list[str]) -> str:
    """Rejoin a prefix_rule's tokens, re-quoting only where a plain space would not survive.

    `render_codex_project` tokenises with `shlex.split`, so a token may itself
    contain whitespace — `echo "a b"` gives `["echo", "a b"]`. Joining that on a
    space and rendering again splits the argument in two, producing a different
    document. Quoting *every* token would close the round trip too, but
    `shlex.quote` also escapes globs, so `delta run --tag=*` would come back
    respelled and stop matching the source it was extracted from.
    """
    return " ".join(shlex.quote(t) if any(c.isspace() for c in t) else t for t in tokens)


def extract_codex(document: Any) -> Extraction:
    buckets: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    notes: list[Note] = []
    in_skipped = False

    for line in document.splitlines():
        if line.startswith(SKIPPED_MARKER):
            in_skipped = True
            continue
        if in_skipped and line.startswith("#   "):
            notes.append(
                Note("cannot", f"codex: skipped glob {line[len('#   ') :]!r} has no category")
            )
            continue
        match = PREFIX_RULE.match(line)
        if match is None:
            continue
        category = CODEX_CATEGORY.get(match["d"])
        if category is None:
            notes.append(Note("unrecognised", f"codex: decision {match['d']!r}"))
            continue
        buckets[category].append(_join_tokens(json.loads(f"[{match['tokens']}]")))

    return Extraction(
        Rules(
            allow=tuple(buckets["allow"]),
            ask=tuple(buckets["ask"]),
            deny=tuple(buckets["deny"]),
        ),
        notes=tuple(notes),
    )


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


VALUE_EXTRACTORS: dict[str, ValueExtractor] = {
    "claude-hooks": extract_claude_hooks,
    "codex-hooks": extract_codex_hooks,
}


EXTRACTORS: dict[str, Extractor] = {
    "claude": extract_claude,
    "claude-project": extract_claude,
    "claude-mcp": extract_claude_mcp,
    "codex": extract_codex,
    "codex-mcp": extract_codex_mcp,
    "codex-project": extract_codex,
    "opencode": extract_opencode,
    "pi": extract_pi,
    "pi-project": extract_pi,
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


# --------------------------------------------------------------------------
# Reconciling several harnesses into one source.
#
# Divergence is reported, never unioned. Two harnesses that disagree about a
# command have no single source rule that reproduces both, and choosing the
# permissive reading renders a WIDER permission set than what was on disk — a
# privilege escalation performed by an onboarding tool. The entry is withheld
# and named in the report instead.
#
# Silence counts as disagreement, but only from a harness that could have
# spoken. Codex's token matcher cannot express a glob, so Codex is not a voter
# on glob entries; counting its silence would suppress a rule every harness
# that can express it agrees on.
# --------------------------------------------------------------------------

ABSENT = "absent"

SHELL = "shell"
MCP = "mcp"


@dataclass(frozen=True)
class Capability:
    """Which kinds of rule a harness's document is able to state at all."""

    shell: bool
    mcp: bool
    globs: bool = True


CAPABILITIES: dict[str, Capability] = {
    "claude": Capability(shell=True, mcp=True),
    "claude-mcp": Capability(shell=False, mcp=True),
    "claude-project": Capability(shell=True, mcp=True),
    # globs=False: render_codex diverts glob entries to an uncategorised comment
    # block, so the document cannot state a decision for one.
    "codex": Capability(shell=True, mcp=False, globs=False),
    "codex-mcp": Capability(shell=False, mcp=True),
    # render_codex_project does not skip globs; it emits them as prefix_rules.
    "codex-project": Capability(shell=True, mcp=False),
    "opencode": Capability(shell=True, mcp=True),
    "pi": Capability(shell=True, mcp=True),
    "pi-project": Capability(shell=True, mcp=True),
}


@dataclass(frozen=True)
class Divergence:
    kind: str
    entry: str
    verdicts: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class Merged:
    rules: Rules
    divergences: tuple[Divergence, ...] = ()
    notes: tuple[Note, ...] = ()


def _capability(name: str) -> Capability:
    try:
        return CAPABILITIES[name]
    except KeyError:
        # Never default to "not a voter": a harness silently excluded from the
        # vote cannot veto, and its denials would vanish into a wider source.
        raise LoadoutError(f"no extraction capability declared for harness: {name}") from None


def _can_state(name: str, kind: str, entry: str) -> bool:
    capability = _capability(name)
    if kind == MCP:
        return capability.mcp
    return capability.shell and (capability.globs or not is_glob(entry))


def _reconcile(
    extractions: Mapping[str, Extraction], kind: str
) -> tuple[dict[str, list[str]], list[Divergence]]:
    # A source may list one entry in two categories — tests/fixtures/permissions.toml
    # does, to show last-match-wins. So a harness's verdict is every category it put
    # the entry in, `allow+deny`, not whichever came last: collapsing to one would
    # drop the shadowed entry from the source without saying so.
    votes: dict[str, dict[str, list[str]]] = {}
    for name, extraction in extractions.items():
        for category in CATEGORIES:
            rules = extraction.rules
            entries = rules.shell(category) if kind == SHELL else rules.mcp(category)
            for entry in entries:
                votes.setdefault(entry, {}).setdefault(name, []).append(category)

    buckets: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    divergences: list[Divergence] = []
    for entry, cast in votes.items():
        verdicts = tuple(
            (name, "+".join(cast[name]) if name in cast else ABSENT)
            for name in extractions
            if _can_state(name, kind, entry)
        )
        agreed = {verdict for _, verdict in verdicts}
        if len(agreed) == 1 and ABSENT not in agreed:
            for category in next(iter(agreed)).split("+"):
                buckets[category].append(entry)
        else:
            divergences.append(Divergence(kind, entry, verdicts))
    return buckets, divergences


def merge_extractions(extractions: Mapping[str, Extraction]) -> Merged:
    """Reconcile one source from several harnesses, withholding what they disagree on.

    Emission order follows the order of `extractions`: an entry takes the
    position of the first harness that stated it. Order is semantic — OpenCode
    and Pi resolve last-match-wins — so the caller chooses which harness's
    ordering the source inherits.
    """
    shell, shell_divergences = _reconcile(extractions, SHELL)
    mcp, mcp_divergences = _reconcile(extractions, MCP)

    extra_allow: list[str] = []
    extra_deny: list[str] = []
    opencode_extra: dict[str, str] = {}
    notes: list[Note] = []
    for extraction in extractions.values():
        extra_allow += extraction.rules.claude_extra_allow
        extra_deny += extraction.rules.claude_extra_deny
        for key, value in extraction.rules.opencode_extra.items():
            opencode_extra.setdefault(key, value)
        notes += extraction.notes

    return Merged(
        Rules(
            allow=tuple(shell["allow"]),
            ask=tuple(shell["ask"]),
            deny=tuple(shell["deny"]),
            mcp_allow=tuple(mcp["allow"]),
            mcp_ask=tuple(mcp["ask"]),
            mcp_deny=tuple(mcp["deny"]),
            claude_extra_allow=tuple(dedupe(extra_allow)),
            claude_extra_deny=tuple(dedupe(extra_deny)),
            opencode_extra=opencode_extra,
        ),
        tuple(shell_divergences + mcp_divergences),
        tuple(notes),
    )
