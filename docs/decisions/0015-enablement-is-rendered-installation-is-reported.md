# 0015 — Enablement is rendered; installation is reported

**Status:** accepted (2026-08-15, plugins slice)

## Context

A plugin is two things: content on disk, and a declaration that it is switched on. loadout
configures a machine, so the declaration is squarely in scope. Getting the content there is
installation, and the four harnesses do it four ways — a git clone, an `npm install`, a
marketplace add, a file dropped in a directory.

Between the two sits a third thing, and it is the one that forced the decision: **marketplace
registration.** Claude and Codex both address a plugin as `<name>@<marketplace>` and both need
the marketplace registered before that name resolves. They put the registration in very
different places.

Codex keeps it in `config.toml`:

```toml
[marketplaces.nolabs-ai]
source_type = "local"
source      = "/Users/me/.codex/plugins/marketplaces/nolabs-ai"
```

Claude keeps it in `~/.claude/plugins/known_marketplaces.json`, alongside `installLocation`
paths and `lastUpdated` timestamps, and rewrites it itself. That file is an install registry
wearing a config file's clothes.

Rendering it would break [0008](0008-generated-files-carry-no-machine-state.md) outright —
timestamps and machine paths are exactly what a generated file may not carry — and would put
loadout in a write race with the harness over a file the harness maintains. The same posture
had already been reached for Claude's MCP surface, for the same reason.

## Decision

**loadout renders enablement and never installs.** No cloning, fetching, `npm install`,
marketplace add, or writing of any harness-managed install registry.

**A marketplace registration is rendered when it is ordinary configuration, and reported when
it is machine state.** Codex's is two literal strings in a file loadout already stages, so it
renders. Claude's is not, so `unregistered_marketplaces` names what the harness does not know
and the user runs the add command.

**Presence is enablement.** A rendered reference is on; a profile switches one off by removing
it with a `null` overlay. Neither harness's explicit off — Claude's `false`, Codex's
`enabled = false` — is rendered or relied upon.

**A reference a harness cannot address is skipped and reported, not refused.** Claude and Codex
need a marketplace, Pi needs a source, and a plugin set spanning both is the ordinary case
rather than a corner one.

## Consequences

- The two unwritable Claude surfaces — MCP servers and marketplaces — get one answer instead of
  two special cases: read, compare, report, never mutate.
- A first sync on a machine that has never registered a marketplace renders `enabledPlugins`
  entries that do not resolve yet. They are inert rather than harmful, and the report says what
  to run. Refusing to render them instead would make the common bootstrap order — configure,
  then install — impossible.
- Skipping rather than refusing means a fragment can be wrong in a way nothing fails on. The
  report is the only signal, and it has no command surface yet, so today it is visible to tests
  and to a caller rather than to a user. That gap is the slice's, and naming it is the point.
- loadout cannot state "this plugin is installed but off", because absence is the only off it
  has. If a harness turns out to distinguish the two in a way that matters, this is the decision
  to revisit.
- Nothing here covers *authoring* a plugin — generating the per-harness manifests one package
  needs. That serves plugin authors rather than someone configuring a machine, it needs its own
  command surface, and it would make loadout a build tool. Recorded as out of scope, not as
  impossible.
