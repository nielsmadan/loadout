# Claude Code

## Config files

Read in precedence order, highest first:

| scope | path |
|---|---|
| managed | `/Library/Application Support/ClaudeCode/managed-settings.json` (macOS), plus `managed-settings.d/*.json` |
| CLI args | temporary session overrides |
| local | `.claude/settings.local.json` |
| project | `.claude/settings.json` |
| user | `~/.claude/settings.json` |

**One canonical file per scope. No imports, no includes, no splitting.** The
`managed-settings.d/` drop-in directory is the sole exception and is managed-scope only.

MCP server configuration and per-project state live in `~/.claude.json`, not in
`settings.json`. Project-scoped MCP servers live in `.mcp.json`.

## Resolution

`deny → ask → allow`, and **rule specificity does not change the order**. Two
consequences that matter:

- A broad deny cannot carry allowlist exceptions. `Bash(aws *)` in deny blocks
  `aws s3 ls` even when that exact string is in allow.
- "If a tool is denied at any level, no other level can allow it."

**Permission rules merge across scopes rather than override.** This is the one setting
that does not follow the normal precedence hierarchy — allow and deny from every scope
are simultaneously in force. It is also why permissions cannot be moved to a different
scope to separate them from hand-maintained settings: they would still merge, but the
hand-maintained keys would not.

## Pattern shape

```
Bash(<entry>:*)     plain command prefix
Bash(<entry>)       entry ending in `*` — Claude's matcher handles the glob
mcp__<server>__<tool>
```

**Verified 2026-08-01 against Claude Code 2.1.220**, sandboxed `HOME`, with
`Bash(touch alpha:*)` as the only allow rule:

| probe | result |
|---|---|
| `touch alpha` (bare) | ran, no denial |
| `touch alpha beta` | ran, no denial |
| `touch zulu` (control) | denied, explicit `permission_denials` record |

So `:*` matches both the bare command and the command with arguments. This syntax is
Claude-specific — do not carry it into another harness's output.

## What loadout emits

| output | ownership |
|---|---|
| `claude/settings.json` | `permissions.allow` / `deny` / `ask` only |
| `claude/settings.autonomous.json` | same document, all three lists emptied |
| `claude/mcp-permissions.json` | fully owned — `PermissionRequest` hook policy |

`claude/settings.json` carries **16 top-level keys**, of which exactly one
(`permissions`) is generated. The other 15 are hand-maintained: `$schema`,
`attribution`, `autoMemoryEnabled`, `awaySummaryEnabled`, `cleanupPeriodDays`,
`effortLevel`, `enabledPlugins`, `env`, `hooks`, `model`, `sandbox`,
`skillListingBudgetFraction`, `skipAutoPermissionPrompt`, `skipWorkflowUsageWarning`,
`statusLine`. Inside `permissions`, `defaultMode` is also hand-maintained.

It is the most-churned file in the repo, and the reason `loadout` needs an explicit
base-document input rather than reading its own previous output.

The autonomous variant empties `allow`, `deny` and `ask` so the auto-mode classifier
judges every call — clearing `deny` is what lets `git push` through, since deny overrides
every mode. It also drops `env.CLAUDE_AFK_TIMEOUT_MS`: headless runs have no human to
answer `AskUserQuestion`, and the 60s "proceed with best judgment" fall-through is what
keeps them moving.

## Deployment

`~/.claude/settings.json` is a **real file reconciled by merge, never a symlink** —
Claude Code writes to it at runtime (`enabledPlugins`, `extraKnownMarketplaces`, `env`
model upgrades, `/fast`, `/config` toggles).

It currently writes *through* a single symlink via one hardcoded exception
(`allowSymlink: source === "userSettings"`) in a helper whose default posture is
`O_NOFOLLOW`. Version 2.1.220 also ships an indirection gate requiring "a regular
non-symlink file with link count 1", so a two-hop chain silently breaks — orphaning the
real source while reporting success (anthropics/claude-code#78162).

`os.replace` onto a symlink replaces the symlink, not its target.

`CLAUDE.md`, `hooks/` and `skills/` are symlinks; only `settings.json` is merged.

## Gotcha: byte comparison against the live file always fails

Generated and live `claude/settings.json` are semantically equal but byte-different,
because `deep_merge` preserves the live file's insertion order for shared keys. A
byte-level `check --live` would report Claude as drifted permanently. Drift comparison
must be semantic, per owned subtree.
