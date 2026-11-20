# 0058 — Devin CLI is a supported harness

**Status:** accepted (2026-11-20, ruled by the user).

## Context

Cognition's terminal agent, `devin`, was measured against the five tests
[0012](0012-antigravity-is-dropped-until-it-matures.md) set for adding a harness. The evidence is
the 3000.14.30821 binary and its shipped docs, run in a throwaway container, first without an
account and then signed in with a free one. It is recorded in [devin.md](../reference/devin.md):

- **Its permissions file is enforcing.** Signed-in runs showed `allow`, `ask` and `deny` in
  `config.json` deciding what ran, a user deny binding even in `dangerous` mode, and an ask
  binding there too.
- **It has a global skills path,** `skills/` in its config directory, and it reads `.agents/skills`
  at both scopes.
- **It has a config-directory variable,** `XDG_CONFIG_HOME`, to which it appends `devin`, as
  OpenCode does.
- **Command rules are word prefixes or exact commands,** not globs, as for Codex and Droid. Path
  rules take globs. The exact form, `ExecExact(…)`, is undocumented, but a signed-in run showed it
  matches only the whole command.
- **Plugin enablement is not established locally.** A user's plugins live in their Devin account;
  a project names required plugins by source in `.devin/config.json`.

It fails only the plugins test, so loadout targets it without a plugins slice.

Two runtime findings contradict Devin's docs. Exact rules work although no page mentions them. A
project deny or ask is overridden by a matching user-level allow and by user `dangerous` mode,
although the precedence page says "A denial at a higher level cannot be overridden by an allow at a
lower level."

Devin also reads, by default, much of what loadout writes for other harnesses: Claude's
`~/.claude/CLAUDE.md`, its hooks and skills, and the Codex and Cursor skills folders.

## Decision

**Devin is a seventh harness, `devin`, with its own copies wherever it has a place for them:**

- `permissions`, `permission_mode` and `agent.model` as owned keys of the shared `config.json`,
  using the settings fragments Cursor's `cli-config.json` uses
  ([0056](0056-cursor-gets-a-home-folder-rule-and-owned-settings-keys.md)), and `permissions` in
  `.devin/config.json` per project, keeping the file's other keys;
- `AGENTS.md` in the config directory, and the shared `AGENTS.md` per project;
- `mcp_config.json` at both scopes;
- `skills/` and `agents/` at both scopes;
- the `hooks` key of `config.json` globally, and `.devin/hooks.v1.json` per project;
- a module-config root for hook scripts.

Exact command entries render as `ExecExact(…)`. Prefix entries render as `Exec(…)`, and the
catch-all as `Exec(*)`.

There is no plugins slice. Agents carry no effort: Devin's agent loader rejects the key.

## Consequences

- A project deny or ask that a global allow reaches is reported at sync, because Devin will not
  apply it. The project file is still written, since it does bind for anyone without that allow.
- With third-party imports on, Devin sees Claude's instructions, skills and hooks beside its own.
  Loadout does not turn the imports off; a user can, through `read_config_from` in a settings
  fragment, and the reference says what that costs.
- `ExecExact` is undocumented. If a later Devin drops it, exact entries stop matching and those
  commands fall back to asking, which is narrower, not wider.
