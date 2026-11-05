# 0049 — The conditional `@AGENTS.md` line, and OpenCode's `instructions` naming the overlay folder by absolute path

**Status:** accepted (2026-11-03, overlay run 1). A bounded exception to
[0008](0008-generated-files-carry-no-machine-state.md): output that depends on the target's files.

## Context

Claude Code reads a repository's `AGENTS.md` only when no `CLAUDE.md` or `CLAUDE.local.md` is
found in the working directory or above it. An overlay's `CLAUDE.local.md` would therefore make
Claude stop reading a team's `AGENTS.md`. Probes on Claude Code 2.1.314 showed that a first line
`@AGENTS.md` in `CLAUDE.local.md` loads the file exactly once, also under
`claude-md-and-agents-md`, and that the line is skipped silently when `AGENTS.md` is absent.

OpenCode's `instructions` key is a union across config layers and names files to include. It
takes an absolute path as written, and a file outside the repository loads (OpenCode 2.2.5). A
project `opencode.json` has nowhere to carry the instruction text itself.

## Decision

- **`CLAUDE.local.md` starts with `@AGENTS.md` when the target has an `AGENTS.md` and no
  `CLAUDE.md`** at the target or above it. A `CLAUDE.md` at the root, in `.claude/`, or in an
  ancestor, or a `CLAUDE.local.md` above the root, counts as present; Claude's own user memory file
  (`$CLAUDE_CONFIG_DIR/CLAUDE.md`, else `~/.claude/CLAUDE.md`) does not, because it exists on most
  machines and would disable the import for every target under the home directory. With a team
  `CLAUDE.md`, Claude was not reading `AGENTS.md`, and the import would load more than the team
  does.
- **The condition is evaluated for each target on each run.** Two targets of one overlay can
  render different bytes. This is the exception to 0008: the content depends on the target's
  files as well as on the source. It is bounded to this one line. The line is inert when the
  condition later stops holding, so a stale one costs a line of context.
- **OpenCode's overlay instructions are rendered once into the overlay folder**, at
  `<overlay>/.loadout/opencode-instructions.md`, and each target's `opencode.json` names that
  file by absolute path under `instructions`. The path is where the source lives, not state
  loadout accumulates, but it means the same overlay registered at two locations renders two
  different `opencode.json` files. The file is recorded in the overlay's record as a shared file
  and retired only by a run that has applied or retired every recorded target.

## Consequences

- `check` compares each target against its own render, so two targets rendering different bytes
  is not drift. A target whose condition flips (the team commits a `CLAUDE.md`) reads as
  `source-changed` until the next `sync`, though the overlay source did not change.
