# 0054 — Cursor CLI is a supported harness

**Status:** accepted (2026-11-08).

## Context

Cursor's terminal agent (`agent`, formerly `cursor-agent`) was measured against the five tests
[0012](0012-antigravity-is-dropped-until-it-matures.md) set for re-adding a harness. The evidence
is the 2026.11.05 bundle, read statically, plus Cursor's documentation, both recorded in
[cursor.md](../reference/cursor.md):

- **Its permissions file is enforcing.** `permissions.allow` and `permissions.deny` decide what
  runs. Deny binds even under `--force`, which is "Force allow commands unless explicitly
  denied".
- **It has a global skills path,** `~/.cursor/skills/`, and it reads `~/.agents/skills/` too.
- **It has a config-directory variable,** `CURSOR_CONFIG_DIR`. It moves only `cli-config.json`:
  every other global file stays in `~/.cursor`, because Cursor itself leaves it there.
- **Globs work.** Picomatch-style, where `*` does not cross `/`.
- **Plugin enablement is not established.** Plugins are installed by the app, and no file holds an
  enablement list.

It fails only the plugins test, so loadout targets it without a plugins slice. A container run of
the CLI, with no account, showed that it rewrites `cli-config.json` with about twenty defaults on
first start. It keeps loadout's key where it was.

Cursor also reads, by default, much of what loadout already writes for Claude and Codex. That
covers Claude's project `CLAUDE.md`, the `.claude` and `.codex` skills and agents folders, and
Claude's hooks in `settings.json`.

## Decision

**Cursor is a sixth harness, `cursor`, and it gets its own copies wherever it has a place for
them:**

- the `permissions` key of `cli-config.json`, globally, and `.cursor/cli.json` per project;
- `mcp.json` and `hooks.json`;
- `skills/` and `agents/`;
- a module-config root for hook scripts.

Project instructions are the shared `AGENTS.md`, because Cursor reads it whatever its settings,
so a rules file of its own would load the same text twice. There is no global instructions slice:
Cursor's rules loader reads only the workspace and its parent directories. There is no plugins
slice.

Hooks are translated the way Cursor translates Claude's own. That is also how its duplicate check
drops the copy it imports from `settings.json`: the check compares command strings.

A stealth source gives Cursor a rule of its own, `.cursor/rules/<name>.mdc` with `alwaysApply:
true`, beside a whole `.cursor/cli.json`, `.cursor/mcp.json` and `.cursor/skills/`. The rule is
how Cursor gets instructions there without the team's `AGENTS.md`, and it is still read when
`.git/info/exclude` hides it, because Cursor lists rules with an include glob, which overrides
ignore rules.

## Consequences

- Cursor has no ask list. An ask is left to `approvalMode`. An allow that would reach an ask is
  dropped and a notice says so, so the rendered policy is never wider than the source.
- A deny glob past the first word is widened to its word prefix, because Cursor's `*` stops at
  `/`.
- With third-party imports on, Cursor sees a skill rendered for Claude and for Cursor twice. How it
  resolves the pair was not established, so loadout does not claim either outcome.
- Nothing has been observed at runtime with an account. The behavioural claims in cursor.md stay
  marked unverified until someone runs an authenticated round trip.
