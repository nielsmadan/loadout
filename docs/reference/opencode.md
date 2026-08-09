# OpenCode

## Config files

Configuration files are **merged together, not replaced**, across these locations:

| location | |
|---|---|
| remote | `.well-known/opencode` endpoint |
| global | `~/.config/opencode/opencode.json` |
| custom | path in `OPENCODE_CONFIG` |
| project | `opencode.json` in project root |
| inline | `OPENCODE_CONFIG_CONTENT` |
| managed | `/Library/Application Support/opencode/` |

There is no import or include directive, and no way to split keys within one location.
A separate permissions file is possible only by using a *different location* — e.g.
`OPENCODE_CONFIG` pointing at a generated file — at the cost of an env var on every
invocation.

The global location is `(XDG_CONFIG_HOME ?? ~/.config) / "opencode"`, so **`XDG_CONFIG_HOME`
is the variable that moves the file loadout writes** — not `OPENCODE_CONFIG_DIR`, which adds
a further `.opencode`-shaped directory that shadows the global config rather than replacing
it. Verified 2026-08-09 against opencode 1.18.15's `globalConfigPath`. See
[Relocating the config directory](README.md#relocating-the-config-directory).

## Resolution

**Last matching rule wins.** Emission order is load-bearing: denies must be emitted after
the allows they refine. The renderer emits `allow`, then `deny`, then `ask`, over a map
seeded with `{"*": "ask"}` as the first entry.

## Pattern shape

`permission.bash` is a map of pattern → decision:

```json
"bash": { "*": "ask", "pwd": "allow", "pwd *": "allow", "git push": "deny" }
```

**A plain prefix must be emitted in both forms** — `<entry>` and `<entry> *` — because
`pwd *` does not match a bare `pwd`. Entries ending in `*` are kept literal; OpenCode's
matcher handles the glob.

MCP entries become top-level keys under `permission`, not nested:

```json
"permission": { "jina_*": "allow" }
```

from a source entry of `jina/*` — the `/` becomes `_`.

## What loadout emits

`opencode/opencode.json` → the `permission` key only.

The rest of the file (`$schema`, `model`, `provider`) is hand-maintained, so this is a
base-document case like Claude's.

**This file has two owners.** `permissions/sync.py` writes `permission`;
`mcp/sync.py` writes `mcp`. Neither may clobber the other's key. This is why the real
output count across the repo is 12 distinct paths, not 13.

`[opencode.extra]` in `permissions.toml` supplies additional `permission.<key>` toggles
verbatim — currently `skill`, `webfetch`, `websearch`, `codesearch`.

## Gotcha: v2 renames nearly every key

OpenCode v2 renames most configuration keys, including
`mcp.{server}.enabled` → `mcp.servers.{server}.disabled` — a **silent boolean
inversion**. A config that appears to migrate cleanly will have every server's
enabled state flipped.

## Gotcha: compound bash commands take the least-permitted verdict

OpenCode evaluates each part of a `;` / `&&` / `|` chain separately and takes the
least-permitted result. In a headless session there is no TTY to answer an `ask` prompt,
so the whole call is auto-rejected and the run terminates before producing output. The
classic trigger is a benign `echo ---` separator inside an otherwise-allowed read chain.
