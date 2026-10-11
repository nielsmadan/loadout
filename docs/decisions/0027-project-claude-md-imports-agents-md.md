# 0027 — A project's CLAUDE.md imports AGENTS.md

**Status:** accepted (2026-10-11).

## Context

Project scope has one instruction order, so a project configured for Claude and any other harness
got two generated files with the same content: `CLAUDE.md` for Claude and `AGENTS.md` for the rest.
Claude now reads `AGENTS.md`, but only as a fallback when no `CLAUDE.md` or `CLAUDE.local.md` is on
the path, only from v2.1.277, and only when the **Project instructions** setting allows it. Removing
`CLAUDE.md` would therefore drop the project's instructions for Claude in cases loadout cannot see,
such as a `CLAUDE.local.md` in a parent directory.

## Decision

The user ruled for the import stub. With Claude plus any other harness, `AGENTS.md` holds the
content and `CLAUDE.md` holds only loadout's banner and `@AGENTS.md`. Claude's docs: *"Keeping the
import never makes Claude read `AGENTS.md` twice, whichever **Project instructions** value you use."*

- A Claude-only project still writes the content to `CLAUDE.md`; a project without Claude writes only
  `AGENTS.md`.
- A `CLAUDE.md` that is a symlink to `AGENTS.md` stays a link and is rendered the same content, since
  writing the stub through it would replace `AGENTS.md`.
- Global scope is unchanged. Loadout never writes `~/.claude/AGENTS.md` or `~/AGENTS.md`, which
  Claude would load in every project under `HOME`.
- Adoption treats a `CLAUDE.md` that only imports `AGENTS.md`, or links to it, as the same document.

## Consequences

- An existing project's generated `CLAUDE.md` becomes the stub on the next `sync`. A hand-written one
  is refused by the guard as modified outside loadout, as before.
- The expected project fixture changed, so its regeneration is its own commit
  ([0009](0009-expected-output-is-reviewed-not-frozen.md)).
