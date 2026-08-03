# Codex CLI

## Config files

| path | contents |
|---|---|
| `~/.codex/config.toml` | general config, profiles, MCP servers |
| `~/.codex/rules/*.rules` | command execution rules — **a directory, read whole** |

Codex is one of two harnesses that already gives permissions their own file. `loadout`
fully owns `codex/rules/permissions.rules`; nothing hand-maintained lives in it, so it
needs no base document.

`codex/rules` is deployed as a **directory symlink**.

## Resolution

Most-restrictive wins: `forbidden > prompt > allow`. Emission order carries no meaning.

`sandbox_mode = "danger-full-access"` **skips rule enforcement entirely** — no rule in
any `.rules` file is consulted. Anything relying on these rules for safety is void under
that setting.

Codex ships **two mutually exclusive permission schemas** — `default_permissions` /
`[permissions]` versus `sandbox_mode` — documented as non-composing, gated on version
0.138.0. Check which one is in force before debugging a rule that appears to be ignored.

## Pattern shape

The rules file is Starlark-ish, not JSON or TOML:

```
prefix_rule(pattern = ["git", "push"], decision = "forbidden")
```

The entry is split on whitespace into tokens; each token is JSON-quoted. Decisions map
`allow → allow`, `deny → forbidden`, `ask → prompt`.

**No globs or wildcards** — the docs are explicit that patterns are "literal strings or
unions of literals". Entries ending in `*` are skipped and listed in a trailing comment
block; they fall through to Codex's normal approval prompt, which is fail-closed.

## Matching is positional, and that is the wrapper-bypass

Codex prefix-matches token by token from argv position 0. It normalises exactly one
level — its own `/bin/zsh -lc` wrapper — but a nested explicit wrapper is not normalised.
The matcher then sees `["bash","-lc","touch forbidden"]`, which never matches a deny on
`["touch","forbidden"]`.

**Verified 2026-08-01:** `prefix_rule(pattern = ["env"], decision = "allow")` in the live
config made all 42 deny rules bypassable. `env touch forbidden` created the file. Removed
from `[shell] allow` the same day.

`find` (bare, and `-exec`), `git rebase` (`-x`) and `docker exec cc-workbench` remain in
the allowlist in the same class. See [README](README.md#the-wrapper-command-bypass).

## What loadout emits

| output | ownership |
|---|---|
| `codex/rules/permissions.rules` | fully owned, text |
| `codex/mcp-permissions.toml` | fully owned |

MCP decisions map `allow → approve`, `ask → prompt`, `deny → deny`.

`~/.codex/config.toml` itself is reconciled by table-ownership merge, handled separately
by `~/ac/codex/sync_config.py`.
