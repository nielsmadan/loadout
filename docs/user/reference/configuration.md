---
title: Configuration files
description: The global manifest, project configuration, permissions format, and fragment layout.
---

# Configuration files

Loadout uses TOML for source selection and permission rules, Markdown for instructions, and
JSON for native configuration fragments.

## Global manifest

A global source contains `loadout.toml`:

```toml
[[source]]
name = "personal"
path = "."

[all]
instructions = ["workflow"]

[claude]
settings = "claude"

[codex]
defaults = "codex"

[opencode]
[pi]
```

This example also requires `instructions/workflow.md`, `settings/claude.json`, and
`defaults/codex.json` in the source. Remove a named selection if you do not need it.

### Source entries

| Field | Meaning |
| --- | --- |
| `name` | Name used to qualify fragments and templates |
| `path` | Source directory; relative to the manifest, or an absolute/home-relative path |
| `use` | Optional list of slices this source contributes |

Source order matters where the slice uses ordered merging. Permission conflicts use
deny/ask/allow priority instead. See [composition](../guides/composition.md).

### Agent blocks

| Key | Value |
| --- | --- |
| `instructions` | Ordered list of Markdown fragment names |
| `settings` | JSON settings fragment name or ordered list |
| `defaults` | Codex defaults fragment name or ordered list |
| `hooks` | Hook fragment name or ordered list |
| `plugins` | Plugin fragment name or ordered list, for supporting agents |
| `substitute` | Map from selected instruction name to replacement name |
| `preserve` | Foreign top-level keys to carry forward where the target supports them |

`[all]` provides defaults to declared agents. Each agent's explicit key replaces that
default. A key that an agent does not support is rejected when named directly in its block.

Permissions, MCP policy/definitions, skills, and module files are automatic for agents that
offer those slices. Set the relevant key to `false` to opt out. For example:

```toml
[codex]
skills = false
mcp = false
```

`permissions = []` selects no permission rules for that target but still renders the target.
It is different from disabling the slice with `false`.

### Fragment layout

```text
source/
├── loadout.toml
├── focused.toml
├── permissions.toml
├── mcp.toml
├── instructions/
├── settings/
├── defaults/
├── hooks/
├── plugins/
├── skills/
├── module-config/
└── templates/
```

Only create the directories your configuration uses. Profiles live beside `loadout.toml`;
slice directories belong under the selected source paths.

## Machine configuration

```toml title="~/.config/loadout/config.toml"
source = "~/agent-config/loadout"
profile = "default"
```

When XDG_CONFIG_HOME is set, the location is `$XDG_CONFIG_HOME/loadout/config.toml`.
`source` is required; `profile` is optional. Other keys are rejected.

## Project configuration

```toml title="loadout/config.toml"
harnesses = ["claude", "codex"]
instructions = ["project", "testing"]
templates = ["python"]
```

| Field | Meaning |
| --- | --- |
| `harnesses` | Non-empty list of supported harness names |
| `instructions` | Ordered project instruction fragment names |
| `templates` | Ordered template names |
| `[template.NAME].vendored` | Content hash maintained by template vendoring/sync |

The project file has no source-path list or profile inheritance. Shared sources reach projects
through templates.

Project permissions live in `loadout/permissions.toml` and
`loadout/permissions.local.toml`. See [project setup](../guides/projects.md).

## Permission rules

```toml
[shell]
default = "ask"
allow = ["git status", "git diff"]
ask = ["git commit"]
deny = ["git push"]

[mcp]
allow = ["docs/search"]
ask = ["docs/update"]
deny = ["docs/delete"]
```

- Shell entries are command strings. The renderer adapts their representation to each harness.
- MCP entries use `server/tool` or `server/*`.
- `allow`, `ask`, and `deny` are lists; omitted lists are empty.
- The strictest decision wins when the same entry appears in several tiers.
- An omitted shell default casts no vote in composition. An explicitly stated default does.
- `[shell] default` is rendered for OpenCode and Pi. Other agents' defaults use native settings.
- A bare `"*"` shell entry is invalid. Use the `default` key for a catch-all.

Trailing shell globs can be represented by Claude, OpenCode, and Pi. Loadout skips them for
Codex's literal prefix rules. Different matching behavior remains visible in the
[harness reference](harnesses.md#permission-portability).

### Harness-specific entries

Native additions can be carried alongside portable rules:

```toml
[claude.extra]
allow = ["Read(//tmp/**)"]

[opencode.extra]
webfetch = "allow"
```

These entries are specific to their harness. Keep portable rules in the shared shell/MCP
sections whenever those sections express the behavior you need.

## Explicit output targets

Agent blocks use built-in destinations. The older explicit-target form is also accepted:

```toml
[[source]]
name = "personal"
path = "."

[instructions.claude]
output = "generated/CLAUDE.md"
destinations = ["${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"]
order = ["workflow"]

[permissions.claude]
output = "generated/settings.json"
render = "claude"
```

Outputs are relative to the manifest root and cannot escape it. Destinations resolve to absolute
machine paths. `${VAR}` requires a non-empty environment variable; `${VAR:-fallback}`
uses the fallback for an unset or empty variable.

Permission targets can specify a JSON `base` file or `settings` fragments, but not both.
A base must be authored input, never another generated output.

Use agent blocks for ordinary setups. The repository's
[full manifest reference](https://github.com/nielsmadan/loadout#loadouttoml) covers the explicit
form in greater detail.
