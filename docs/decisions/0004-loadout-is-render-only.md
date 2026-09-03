# 0004 — loadout renders; it does not mutate sources

**Status:** accepted (2026-08-02, milestone 1)

## Context

The system loadout replaces has both a renderer and a mutation CLI (`aiperm`) that edits the
rule source and then regenerates. The obvious move was to absorb both.

## Decision

loadout is **render-only**. It reads sources and writes generated files. It never edits a
source, and there is no `loadout allow` / `loadout deny`.

Mutation is somebody else's job: a person editing the TOML, or a separate tool.

## Consequences

- Every command is safe to run at any time. `sync` and `check` have no failure mode that
  damages a source.
- No permission rule is needed to let an agent *widen* its own permissions through loadout —
  a real hazard in the system being replaced, where the mutation CLI was allowlisted bare and
  any agent could grant itself anything.
- The source stays hand-editable, which keeps the format honest: if a rule file becomes
  awkward to write by hand, that is a signal about the format rather than something a
  generator can paper over.
- Cost: convenience UX ("allow this command") must live outside loadout. Judgement about
  *which* rule to add belongs in a skill; loadout only needs to answer whether a command
  currently matches, which is a read-only query.

## Amendment (2026-08-06, milestone 4)

`loadout init` and `loadout harness add` write configuration files, which the decision as
originally worded appears to forbid.

The safety argument was always about **rules**: there is no `loadout allow`, so no path
exists by which an agent widens its own permissions through loadout — a live hazard in the
system being replaced, where the mutation CLI was allowlisted bare and any agent could grant
itself anything. Enabling a harness changes *where* existing rules are rendered, never *what*
they permit.

Amended to read: **loadout never mutates rules; scaffolding commands may write
configuration.** Recorded as an amendment rather than a silent reinterpretation.

## Amendment (2026-09-03): this ADR does not govern external invocation

It was cited three times — `agents.py`, [servers.md](../reference/servers.md#claudes-global-entry-is-staged-not-written)
and [config.md](../reference/config.md#mcp) — for the proposition that "render and invoke stay
separate (ADR 0004)", used to justify staging Claude's global MCP servers to a file that
something else feeds to `claude mcp add-json`.

**Nothing above says that.** This ADR is about loadout never editing the *rule source* it
renders from — there is no `loadout allow`, so no agent widens its own permissions through
loadout. Whether loadout shells out to a harness's CLI is a different question that this
decision never considered, and citing it for that reads a general prohibition out of a specific
one.

The staging may still be the right shape for other reasons — a read-modify-write against a file
the harness rewrites continuously carries a race this ADR has nothing to say about. But it is
not required here, and the factual premise the citations rested on ("there is no file loadout
can render into") was false: `${CLAUDE_CONFIG_DIR:-~}/.claude.json` carries a top-level
`mcpServers` map, which is where `claude mcp add-json --scope user` writes.

Scope, stated once so it is not re-derived: **0004 constrains what loadout writes into its own
sources. It says nothing about what loadout may invoke, or which destinations it may own.**
