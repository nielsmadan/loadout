"""The round-trip property that defines a correct permissions extractor.

Two properties, and they check different things:

**P1 — rules round-trip.** ``extract(render(rules)) == carried(rules)``. ``carried``
is a *declared* projection: what that harness's document is able to carry. Codex's
token matcher cannot express a glob, so ``carried("codex", ...)`` drops globs.
Declaring the loss in the test is the point — a lenient comparison would hide it.

**P2 — document round-trip.** ``render(extract(document)) == document``, byte for
byte, which is the acceptance criterion in the extraction spec. P2 is what catches
the non-injective pattern forms: OpenCode and Pi emit both ``foo`` and ``foo *`` for
one source entry, so an extractor that fails to collapse the pair renders a
different document the second time.

P2 is only claimed where the extractor reports no loss. An extractor that *knows*
it dropped something says so in ``Extraction.notes``, and the loss is pinned by its
own test rather than passed off as a round trip.
"""

from __future__ import annotations

import itertools
import json
from typing import Any

import pytest

from loadout.extract import EXTRACTORS, extract
from loadout.permissions.renderers import RENDERERS, JsonSpec, TextSpec
from loadout.permissions.rules import Rules, is_glob, mcp_parts

CATEGORIES = ("allow", "ask", "deny")

# `RENDERERS` is no longer the permissions renderers — the hooks slice registers
# into it too. These properties cover what extract.py actually inverts, and the
# gap is named here rather than left as whatever the subtraction happens to
# yield: a *third* uninverted renderer must fail this file, not slip in beside
# the two that are known.
NOT_INVERTED = {"claude-hooks", "codex-hooks"}

INVERTED = sorted(EXTRACTORS)

# Shapes, not realism — the same discipline as tests/fixtures/permissions.toml.
# Bare, multi-word, trailing glob, and a glob that is not the whole final token.
SHELL_POOL = ("alpha", "beta sub", "gamma-*", "delta run --tag=*")

# Server-wide, per-tool, a server name with a dot, and a second server so
# codex-mcp's sort-by-server has something to reorder.
MCP_POOL = ("svc/*", "svc/read", "svc.two/write", "other/*")

CLAUDE_EXTRA_ALLOW = ("Read(//tmp/**)",)
CLAUDE_EXTRA_DENY = ("Write(//etc/**)",)
OPENCODE_EXTRA = {"webfetch": "allow"}


def _bucket(pool: tuple[str, ...], assignment: tuple[str, ...]) -> dict[str, tuple[str, ...]]:
    out: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    for entry, category in zip(pool, assignment, strict=True):
        out[category].append(entry)
    return {category: tuple(entries) for category, entries in out.items()}


def _clean_space() -> list[Rules]:
    """Every pool entry in exactly one category.

    P1 holds here. Outside it — the same entry in two categories — every harness
    resolves the conflict its own way (last-match-wins, or a map overwrite), so
    the source category is genuinely unrecoverable and only P2 applies.
    """
    shell_assignments = list(itertools.product(CATEGORIES, repeat=len(SHELL_POOL)))
    mcp_assignments = list(itertools.product(CATEGORIES, repeat=len(MCP_POOL)))
    space = []
    for index, shell_assignment in enumerate(shell_assignments):
        shell = _bucket(SHELL_POOL, shell_assignment)
        mcp = _bucket(MCP_POOL, mcp_assignments[index % len(mcp_assignments)])
        space.append(
            Rules(
                allow=shell["allow"],
                ask=shell["ask"],
                deny=shell["deny"],
                mcp_allow=mcp["allow"],
                mcp_ask=mcp["ask"],
                mcp_deny=mcp["deny"],
            )
        )
    space += [
        Rules(),
        Rules(allow=SHELL_POOL),
        Rules(mcp_deny=MCP_POOL),
        Rules(
            claude_extra_allow=CLAUDE_EXTRA_ALLOW,
            claude_extra_deny=CLAUDE_EXTRA_DENY,
            opencode_extra=dict(OPENCODE_EXTRA),
        ),
        Rules(
            allow=SHELL_POOL,
            mcp_allow=MCP_POOL,
            claude_extra_allow=CLAUDE_EXTRA_ALLOW,
            claude_extra_deny=CLAUDE_EXTRA_DENY,
            opencode_extra=dict(OPENCODE_EXTRA),
        ),
        # Reversed, so a projection that happens to sort is not mistaken for one
        # that preserves order.
        Rules(allow=tuple(reversed(SHELL_POOL)), mcp_ask=tuple(reversed(MCP_POOL))),
    ]
    return space


CLEAN_SPACE = _clean_space()

CONFLICT_SPACE = [
    # zeta-style: the same entry in allow and deny, and not last.
    Rules(allow=("zeta", "eta"), deny=("zeta", "iota push")),
    Rules(allow=("b",), deny=("a", "b")),
    Rules(allow=("x",), ask=("x",), deny=("x",)),
    Rules(mcp_allow=("svc/*", "svc/read"), mcp_deny=("svc/*",)),
    Rules(allow=("gamma-*",), deny=("gamma-*",)),
]

FULL_SPACE = CLEAN_SPACE + CONFLICT_SPACE

BASES: tuple[dict[str, Any], ...] = (
    {},
    {"model": "opus", "permissions": {"defaultMode": "acceptEdits"}},
)


def _unknown_kind(name: str, spec: object) -> AssertionError:
    """Fail by name rather than by arity.

    A renderer kind these properties do not know cannot be round-tripped by
    guessing at its signature, and the `(rules, base)` else-branch would call it
    with the wrong arity and the wrong meaning. Say what is missing instead.
    """
    return AssertionError(
        f"{name}: {type(spec).__name__} has no inverse. Give it an extractor in "
        "EXTRACTORS, a capability in CAPABILITIES, a projection in PROJECTIONS, "
        "and a branch here — do not widen the else-branch to swallow it."
    )


def _render(name: str, rules: Rules, base: dict[str, Any]) -> Any:
    spec = RENDERERS[name]
    if isinstance(spec, TextSpec):
        return spec.fn(rules)
    if isinstance(spec, JsonSpec):
        return spec.fn(rules, base)
    raise _unknown_kind(name, spec)


def _serialize(name: str, document: Any) -> str:
    """Byte-for-byte as emit.py writes it, so key order is part of the comparison.

    Python dicts compare equal regardless of insertion order; the whole point of
    P2 is that insertion order is semantic, so documents are compared as text.
    """
    spec = RENDERERS[name]
    if isinstance(spec, TextSpec):
        assert isinstance(document, str)
        return document
    if isinstance(spec, JsonSpec):
        return json.dumps(document, indent=2, ensure_ascii=spec.ensure_ascii) + "\n"
    raise _unknown_kind(name, spec)


# --------------------------------------------------------------------------
# carried() — the declared projection, written independently of extract.py so
# the property is a specification and not a restatement of the implementation.
# --------------------------------------------------------------------------


def _shell_only(rules: Rules) -> Rules:
    return Rules(allow=rules.allow, ask=rules.ask, deny=rules.deny)


def _mcp_only(rules: Rules) -> Rules:
    return Rules(mcp_allow=rules.mcp_allow, mcp_ask=rules.mcp_ask, mcp_deny=rules.mcp_deny)


def _shell_and_mcp(rules: Rules) -> Rules:
    return Rules(
        allow=rules.allow,
        ask=rules.ask,
        deny=rules.deny,
        mcp_allow=rules.mcp_allow,
        mcp_ask=rules.mcp_ask,
        mcp_deny=rules.mcp_deny,
    )


def _codex_mcp_carried(rules: Rules) -> Rules:
    """codex-mcp groups by server and sorts, so source order does not survive.

    Within a server the emitted order is the wildcard, then denied tools sorted,
    then approved tools sorted — mirroring render_codex_mcp's block layout.
    """
    decisions: dict[str, str] = {}
    for category in ("allow", "ask", "deny"):
        for entry in rules.mcp(category):
            decisions[entry] = category

    servers: dict[str, dict[str, str]] = {}
    for entry, category in decisions.items():
        server, tool = mcp_parts(entry)
        servers.setdefault(server, {})[tool] = category

    buckets: dict[str, list[str]] = {category: [] for category in CATEGORIES}
    for server in sorted(servers):
        tools = servers[server]
        ordered = []
        if "*" in tools:
            ordered.append("*")
        ordered += sorted(t for t, c in tools.items() if t != "*" and c == "deny")
        ordered += sorted(t for t, c in tools.items() if t != "*" and c != "deny")
        for tool in ordered:
            buckets[tools[tool]].append(f"{server}/{tool}")

    return Rules(
        mcp_allow=tuple(buckets["allow"]),
        mcp_ask=tuple(buckets["ask"]),
        mcp_deny=tuple(buckets["deny"]),
    )


def _codex_carried(rules: Rules) -> Rules:
    # A glob is emitted as a comment with no category attached, so it is
    # reported, never extracted.
    return Rules(
        allow=tuple(e for e in rules.allow if not is_glob(e)),
        ask=tuple(e for e in rules.ask if not is_glob(e)),
        deny=tuple(e for e in rules.deny if not is_glob(e)),
    )


def _claude_carried(rules: Rules) -> Rules:
    return Rules(
        allow=rules.allow,
        ask=rules.ask,
        deny=rules.deny,
        mcp_allow=rules.mcp_allow,
        mcp_ask=rules.mcp_ask,
        mcp_deny=rules.mcp_deny,
        claude_extra_allow=rules.claude_extra_allow,
        claude_extra_deny=rules.claude_extra_deny,
    )


def _opencode_carried(rules: Rules) -> Rules:
    return Rules(
        allow=rules.allow,
        ask=rules.ask,
        deny=rules.deny,
        mcp_allow=rules.mcp_allow,
        mcp_ask=rules.mcp_ask,
        mcp_deny=rules.mcp_deny,
        opencode_extra=dict(rules.opencode_extra),
    )


PROJECTIONS = {
    "claude": _claude_carried,
    "claude-project": _claude_carried,
    "claude-mcp": _mcp_only,
    "codex": _codex_carried,
    "codex-mcp": _codex_mcp_carried,
    "codex-project": _shell_only,
    "opencode": _opencode_carried,
    "pi": _shell_and_mcp,
    "pi-project": _shell_and_mcp,
}


def carried(name: str, rules: Rules) -> Rules:
    assert name in PROJECTIONS, f"no declared projection for renderer {name!r}"
    return PROJECTIONS[name](rules)


# --------------------------------------------------------------------------
# The properties.
# --------------------------------------------------------------------------


@pytest.mark.parametrize("name", INVERTED)
def test_extraction_recovers_every_rule_the_harness_can_carry(name: str) -> None:
    for rules in CLEAN_SPACE:
        document = _render(name, rules, {})
        assert extract(name, document).rules == carried(name, rules), f"{name}: {rules}"


@pytest.mark.parametrize("name", INVERTED)
def test_reextraction_reproduces_the_document_byte_for_byte(name: str) -> None:
    for base in BASES:
        for rules in FULL_SPACE:
            document = _render(name, rules, base)
            extraction = extract(name, document)
            if extraction.notes:
                continue
            again = _render(name, extraction.rules, extraction.base)
            assert _serialize(name, again) == _serialize(name, document), f"{name}: {rules}"


@pytest.mark.parametrize("name", INVERTED)
def test_extraction_is_idempotent(name: str) -> None:
    """A second pass over the re-rendered document must find the same rules.

    Distinct from P2: a document can be stable while the extractor keeps
    reinterpreting it, which is the shape a half-done pair collapse takes.
    """
    for rules in FULL_SPACE:
        first = extract(name, _render(name, rules, {}))
        second = extract(name, _render(name, first.rules, first.base))
        assert second.rules == first.rules, f"{name}: {rules}"


@pytest.mark.parametrize("name", INVERTED)
def test_every_inverted_renderer_round_trips_an_empty_source(name: str) -> None:
    assert extract(name, _render(name, Rules(), {})).rules == Rules()


def test_no_renderer_lacks_an_inverse_without_being_named() -> None:
    """Every renderer without an extractor must be one that was named here.

    `claude-hooks` and `codex-hooks` are `ValueSpec` renderers belonging to the
    hooks slice; inverting them is its own piece of work with its own design
    questions (a hook fragment is not `Rules`, and a value renderer is handed no
    base to hold a residual in).

    Subset rather than equality, deliberately. Whether those two are present
    depends on which commit is checked out — they arrive with the hooks merge —
    and that is environmental. What must hold on either side of a rebase is the
    safety property: a renderer added without an extractor is never absorbed
    silently.
    """
    unnamed = set(RENDERERS) - set(EXTRACTORS) - NOT_INVERTED
    assert not unnamed, (
        f"renderer(s) with no inverse and no entry in NOT_INVERTED: {sorted(unnamed)}. "
        "Give each an extractor in EXTRACTORS, a capability in CAPABILITIES and a "
        "projection in PROJECTIONS — or name it here with the reason it is deferred."
    )
    assert set(EXTRACTORS) <= set(RENDERERS)


def test_every_inverted_renderer_has_a_declared_projection() -> None:
    assert sorted(PROJECTIONS) == INVERTED


def test_the_base_keeps_keys_other_slices_own() -> None:
    """`Extraction.base` is the render-time residual, NOT the settings fragment.

    `settings.json` has four owners — settings, permissions, hooks, plugins. This
    extractor strips only what `render_claude` writes, so `hooks` and
    `enabledPlugins` stay in the base and the document round-trips.

    They must not be routed to a settings fragment as they stand: the extraction
    spec requires owned keys to be stripped before a settings fragment is
    written, or a user edits `hooks` there and watches it lose to the hooks slice
    on every render. That stripping belongs to whatever composes the slices,
    which does not exist yet. Doing it *here* would break the document round trip
    by dropping content this renderer is required to reproduce — so the boundary
    is pinned rather than left to be discovered by whoever wires it.
    """
    document: dict[str, Any] = {
        "hooks": {"PreToolUse": []},
        "enabledPlugins": ["example"],
        "permissions": {"allow": ["Bash(ls:*)"], "deny": [], "ask": []},
    }
    extraction = extract("claude", document)

    assert extraction.rules.allow == ("ls",)
    assert list(extraction.base) == ["hooks", "enabledPlugins", "permissions"]
    assert _serialize("claude", _render("claude", extraction.rules, extraction.base)) == _serialize(
        "claude", document
    )
