# 0007 — loadout's scope is all agent configuration

**Status:** accepted (2026-08-07, milestone 5 planning)

## Context

loadout began as an instruction and permission renderer. Around it, `~/ac` still runs three
other generators: `mcp/sync.py` (MCP server definitions), the skills sync, and
`templates/deploy.py` (`aiconf`, per-project-type configuration).

Templates forced the question, because one template carries four artifact types at once —
permissions, instructions, skills, and MCP — and they cannot all be adopted in one milestone.

An argument was raised for leaving skills and MCP definitions permanently outside: loadout's
value is rendering one source into five *different* formats, and skills are files copied
wholesale while MCP definitions are near-identical JSON everywhere. Neither needs a five-way
render.

## Decision

**loadout is positioned to handle all agent configuration** — or at least every part of it
that is complicated. Skills, MCP, and templates are in scope. The five-way-render argument
does not bound the project.

The reasoning that settles it is the one this repo keeps rediscovering: an artifact deployed
by a separate generator is written in one harness's format to one harness's path, and
silently reaches only that harness. That has already happened twice — the `CLAUDE.md`
fragment edit that reached Claude alone, and every template permission grant, which is stored
in Claude's wire syntax under `.claude/`. Whether an artifact *needs* a five-way render is a
weaker test than whether it *should be authored once*.

## Consequences

- `aiconf` disappears eventually rather than shrinking. Same for the standalone skills and
  MCP generators.
- Templates are a **dimension, not a milestone**. The mechanism — a project declares a
  template, whose content resolves from the global source and merges as the lowest tier — is
  built once, in the milestone where the first artifact type can use it (permissions). Each
  later artifact type plugs into the mechanism as loadout absorbs it.
- Adoption order is set by prerequisite, not by artifact importance: permissions is ready now,
  instructions waits on project-scope instructions, skills and MCP wait on loadout owning
  those types at all.
- Cost: the scope is large enough that "not yet built" will be the answer for a long time on
  most of it. Milestones stay narrow and byte-identical-verified; the breadth is a direction,
  not a licence to widen any single milestone.
- ADR [0004](0004-loadout-is-render-only.md) still holds — breadth of *artifact types* is not
  breadth of *operations*. loadout renders; it does not mutate rules.
