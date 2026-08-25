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

from loadout.extract import EXTRACTORS, VALUE_EXTRACTORS, extract
from loadout.permissions.renderers import RENDERERS, JsonSpec, TextSpec, render_codex_project
from loadout.permissions.rules import UNSTATED_DEFAULT, Rules, is_glob, mcp_parts

CATEGORIES = ("allow", "ask", "deny")

# `RENDERERS` is no longer the permissions renderers — the hooks and plugins
# slices register into it too. These properties cover what extract.py inverts
# *as documents*; the `ValueSpec` renderers are inverted by VALUE_EXTRACTORS and
# covered by tests/test_extract_hooks.py and tests/test_extract_plugins.py,
# because a value renderer takes no base and its inverse holds no residual. A
# renderer inverted by neither must fail this file.
#
# `opencode-hooks` and `pi-hooks` are the generated adapters, and the only
# entries here that are **permanent** rather than deferred. Every other renderer
# writes a data format, so inverting it is work someone could do. These write
# JavaScript and TypeScript: a hooks document is recoverable from loadout's own
# output only because loadout put it there, and a plugin a user wrote by hand —
# the case extraction exists for — is a program, not a document. There is
# nothing to parse back.
#
# `codex-plugins` and `codex-servers` are here for a third reason, and only
# for as long as the registries stay as they are: each inverse exists and is
# pinned by its own test (tests/test_extract_plugins.py,
# tests/test_extract_servers.py), but each is a `DocumentTextSpec` returning a
# fragment, and neither EXTRACTORS (which returns `Rules`) nor VALUE_EXTRACTORS
# (held to the set of `ValueSpec`s) has that shape.
NOT_INVERTED: set[str] = {
    "opencode-hooks",
    "pi-hooks",
    "codex-plugins",
    "codex-servers",
}

INVERTED = sorted(EXTRACTORS)

# Shapes, not realism — the same discipline as tests/fixtures/permissions.toml.
# Bare, multi-word, trailing glob, and a glob that is not the whole final token.
SHELL_POOL = ("alpha", "beta sub", "gamma-*", "delta run --tag=*")

# Server-wide, per-tool, a server name with a dot, and a second server so
# codex-mcp-permissions's sort-by-server has something to reorder.
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
        # A stated catch-all, in each verdict a harness can seed. `allow` is the
        # one with rules under it, so the seed has to survive being written over.
        Rules(allow=SHELL_POOL, mcp_allow=MCP_POOL, default="allow"),
        Rules(deny=SHELL_POOL, default="deny"),
    ]
    return space


CLEAN_SPACE = _clean_space()

CONFLICT_SPACE = [
    # A stated `ask` renders exactly what an unstated default renders, so P1's
    # identity cannot hold for it — the same "only P2 applies" reason as the
    # entries below. Here rather than deleted, so P2 and idempotence still run it
    # over every renderer and every base.
    Rules(allow=SHELL_POOL, mcp_allow=MCP_POOL, default="ask"),
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


def _stated_default(rules: Rules) -> str | None:
    """What a bash catch-all can carry back: everything except a stated `ask`.

    Seeding `ask` is what an unstated default already renders, so the two are one
    document and the extractor reads the shorter spelling.
    """
    return None if rules.default == UNSTATED_DEFAULT else rules.default


def _codex_mcp_carried(rules: Rules) -> Rules:
    """codex-mcp-permissions groups by server and sorts, so source order does not survive.

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
        default=_stated_default(rules),
    )


def _pi_carried(rules: Rules) -> Rules:
    """Pi carries the default; `pi-project` emits no catch-all, so it does not.

    Spelled out rather than composed from `_shell_and_mcp`, because a projection
    built from another projection restates it instead of declaring Pi's contract.
    """
    return Rules(
        allow=rules.allow,
        ask=rules.ask,
        deny=rules.deny,
        mcp_allow=rules.mcp_allow,
        mcp_ask=rules.mcp_ask,
        mcp_deny=rules.mcp_deny,
        default=_stated_default(rules),
    )


PROJECTIONS = {
    "claude": _claude_carried,
    "claude-project": _claude_carried,
    "claude-mcp-permissions": _mcp_only,
    "codex": _codex_carried,
    "codex-mcp-permissions": _codex_mcp_carried,
    "codex-project": _shell_only,
    "opencode": _opencode_carried,
    "pi": _pi_carried,
    "pi-project": _shell_and_mcp,
}


def carried(name: str, rules: Rules) -> Rules:
    assert name in PROJECTIONS, f"no declared projection for renderer {name!r}"
    return PROJECTIONS[name](rules)


# --------------------------------------------------------------------------
# The properties.
# --------------------------------------------------------------------------


def test_pi_project_keeps_a_leading_star_because_nothing_seeded_it() -> None:
    """`render_pi_project` writes no catch-all, so a leading `*` there is a source
    rule. Reading it as the default deleted it and re-rendered a different
    document — silently, with empty notes claiming a clean round trip."""
    document = _render("pi-project", Rules(allow=("*",)), {})
    extraction = extract("pi-project", document)
    assert extraction.rules.allow == ("*",)
    assert extraction.rules.default is None
    assert extraction.notes == ()
    assert _serialize("pi-project", _render("pi-project", extraction.rules, {})) == _serialize(
        "pi-project", document
    )


@pytest.mark.parametrize("name", ["opencode", "pi"])
def test_a_carrier_without_a_catch_all_reports_rather_than_assuming_one(name: str) -> None:
    """A foreign document with no `*` is not a document that said `ask` — the
    harness's own built-in applies and loadout cannot know it."""
    extraction = extract(name, {"permission": {"bash": {"ls": "allow"}}})
    assert extraction.rules.default is None
    assert any("catch-all" in note.detail for note in extraction.notes)


@pytest.mark.parametrize("name", ["opencode", "pi"])
def test_an_unrecognised_catch_all_verdict_is_reported_not_swallowed(name: str) -> None:
    """Every other decision in a bash map gets a note; this path used to swallow
    it and hand `merge_rules` a value that raised a bare ValueError."""
    extraction = extract(name, {"permission": {"bash": {"*": "yolo", "ls": "allow"}}})
    assert extraction.rules.default is None
    assert any(note.kind == "unrecognised" for note in extraction.notes)


@pytest.mark.parametrize("name", ["opencode", "pi"])
def test_a_default_of_ask_is_the_same_document_as_no_default(name: str) -> None:
    """The one spelling P1 cannot preserve, pinned rather than passed off.

    The first assertion is the reason (see `_stated_default`); the last is that
    P2 still holds regardless.
    """
    stated = _render(name, Rules(allow=("alpha",), default="ask"), {})
    unstated = _render(name, Rules(allow=("alpha",)), {})
    assert _serialize(name, stated) == _serialize(name, unstated)
    assert extract(name, stated).rules.default is None
    assert _serialize(name, _render(name, extract(name, stated).rules, {})) == _serialize(
        name, stated
    )


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
    unnamed = set(RENDERERS) - set(EXTRACTORS) - set(VALUE_EXTRACTORS) - NOT_INVERTED
    assert not unnamed, (
        f"renderer(s) with no inverse and no entry in NOT_INVERTED: {sorted(unnamed)}. "
        "Give each an extractor in EXTRACTORS, a capability in CAPABILITIES and a "
        "projection in PROJECTIONS — or name it here with the reason it is deferred."
    )
    assert set(EXTRACTORS) <= set(RENDERERS)


def test_every_inverted_renderer_has_a_declared_projection() -> None:
    assert sorted(PROJECTIONS) == INVERTED


def test_a_token_holding_a_space_survives_the_codex_project_round_trip() -> None:
    """Joining tokens on a space is not the inverse of `shlex.split`.

    `echo "a b"` renders to the tokens `["echo", "a b"]`. Rejoining those with a
    plain space and rendering again splits the quoted argument into two, giving a
    different document from an extractor that reported no loss — a silent P2
    violation the enumerated space never reached, because no pool entry contains
    a quoted space.

    A token holding whitespace is re-quoted so the split round-trips. The source
    spelling normalises (`"a b"` comes back as `'a b'`), which is a P1
    normalisation, not a document loss.

    `render_codex` is unaffected: it tokenises with `str.split`, so a token can
    never contain whitespace and the quoting branch cannot fire.
    """
    document = render_codex_project(Rules(allow=('echo "a b"',)))
    extraction = extract("codex-project", document)

    assert extraction.notes == ()
    assert extraction.rules.allow == ("echo 'a b'",)
    assert render_codex_project(extraction.rules) == document


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
