# Antigravity (`agy`)

## Config file

`antigravity/settings.json` → `permissions.allow` / `deny` / `ask`.

Today this file contains **only** the `permissions` key, so nothing is lost by
regenerating it whole. That is a property of the current file, not a guarantee from
Antigravity — if a hand-maintained key is ever added, this becomes a base-document case
like Claude's.

Alone among the five, Antigravity offers **no environment variable that relocates its config
directory**: `~/.gemini/antigravity-cli/settings.json` is built from `$HOME` and nothing else.
Verified 2026-08-09 against `agy` 1.1.11 by scanning every `AGY_*` / `ANTIGRAVITY_*` /
`GEMINI_*` name in the binary. See [Relocating the config directory](README.md#relocating-the-config-directory).

## Resolution

"Strictly evaluated in priority order: Deny > Ask > Allow." Emission order carries no
meaning.

## Pattern shape

```
command(<entry>)
mcp(<entry>)
```

Per the `/cli-features` docs. The docs only ever show **literal command strings** — no
wildcard syntax is documented — so entries ending in `*` are skipped exactly as they are
for Codex, and fall through to runtime approval.

Matching is positional, so Antigravity is in the same wrapper-bypass class as Codex. See
[README](README.md#the-wrapper-command-bypass).

## Gotcha: headless mode ignores the allowlist entirely

In headless / print mode, current `agy` **soft-denies every file read and command**. The
`permissions.allow` list, workspace registration, and trusted-folder status are honored
only interactively.

Verified 2026-07: a `read_file` on a file inside a trusted and registered workspace was
soft-denied. The only way to get output is `--dangerously-skip-permissions` (or
`toolPermission: always-proceed`), which auto-approves **all** tools including writes —
there is no read-only-with-exploration path.

This is why `agy` was dropped from the `/second-opinion` skill rather than granted
blanket write access, and why `permissions.toml` carries no `agy` allow-rule. Re-verify
against a newer build before re-adding it.
