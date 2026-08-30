# 0018 — A runtime dependency is acceptable for fidelity

**Status:** accepted (2026-08-30)

## Context

Loadout shipped with no runtime dependencies. That was a property worth having in a tool whose
whole job is writing other tools' configuration: nothing to resolve, nothing to pin, nothing
that can break a render.

Codex MCP output is TOML, and
[0017](0017-ownership-may-be-declared-instead-of-derived.md) put loadout in the position of
writing generated keys on top of a `config.toml` it does not own. Emitting TOML by hand can
produce correct syntax, but it cannot preserve the comments, key order, and formatting of the
surrounding document — and the surrounding document is the user's.

Reimplementing that faithfully is a TOML round-tripping library, which is what `tomlkit`
already is.

## Decision

Depend on `tomlkit` (`>=0.15.1`) for TOML output. Zero runtime dependencies is no longer a
project constraint.

The bar is **needed fidelity**, not convenience: a dependency earns its place when it preserves
something about a user's file that loadout would otherwise destroy, or when the alternative is
reimplementing a format's round-trip semantics. Saving ordinary effort is not the test.

## Consequences

Codex settings merges can be built on a document model that survives a rewrite, rather than on
string splicing that silently reorders or drops comments.

The install surface is no longer empty, so a broken or yanked release of a dependency can now
break a render. This is the cost accepted here; it argues for few dependencies, not none.

Future format work should ask the fidelity question first. A parser that only reads is usually
not worth a dependency — the round trip is what is hard.
