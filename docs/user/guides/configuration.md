---
title: Configuring your agents
description: Learn where instructions, permissions, skills, tools, settings, hooks, plugins, and module files belong.
---

# Put each kind of configuration in its source

A **slice** is one kind of agent configuration. Instructions, permissions, skills, MCP servers,
and settings each have their own source shape.

Start with [global setup](global.md) or [project setup](projects.md), then add the slices you need.
The [support table](../reference/harnesses.md) shows which agents and scopes receive each one.

## Instructions

Global fragments live under a source's `instructions/` directory. Select their order in the
global manifest:

```toml
[claude]
instructions = ["workflow", "testing"]
```

Project fragments live in `loadout/instructions/`; their order belongs in
`loadout/config.toml`. See [composition](composition.md).

## Permissions

Put shared shell and MCP tool-approval rules in `permissions.toml`:

```toml
[shell]
allow = ["git status", "git diff"]
ask = ["git commit"]
deny = ["git push"]

[mcp]
allow = ["docs/search"]
ask = ["docs/update"]
```

MCP entries use `server/tool` or `server/*`. Check the
[portability notes](../reference/harnesses.md#permission-portability) when rules overlap.

The project version is `loadout/permissions.toml`. Personal project rules go in
`loadout/permissions.local.toml`.

`[shell] default = "ask"` specifies the fallback decision for OpenCode and Pi. Claude's
default mode is a native setting; Codex's approval policy is a separate setting.
A bare `"*"` permission entry is rejected. See the
[permissions syntax](../reference/configuration.md#permission-rules).

## Skills

Add a skill tree under the source:

```text
skills/review/
├── SKILL.md
└── references/
    └── conventions.md
```

For a project, use `loadout/skills/review/`. The directory itself declares the skill.
Global agent blocks can opt out with `skills = false`.

Loadout supports harness-specific content inside skill Markdown:

```markdown
::: claude
Use the Claude-specific workflow here.
:::

::: opencode
Use the OpenCode-specific workflow here.
:::
```

Keep supporting files in the same tree. They travel with the skill. Project skills are currently
rendered for Claude, OpenCode, and Pi; the Loadout project preset has no Codex skill output.

For the bundled configuration skill, see [global setup](global.md#configure-through-your-agent).

## MCP server definitions

Server definitions live in `mcp.toml`, distinct from tool-approval policy in
`permissions.toml`:

```toml title="mcp.toml"
[docs]
transport = "http"
url = "https://mcp.example.com"
auth_env_var = "DOCS_API_TOKEN"

[local-tools]
transport = "stdio"
command = "my-mcp-server"
args = ["--stdio"]
```

Use your actual server URL or command. `auth_env_var` is the environment variable's name,
not the secret value.

At project scope, the file is `loadout/mcp.toml`. Claude receives `.mcp.json` and OpenCode
receives its `mcp` configuration key. Pi's MCP adapter can read the shared `.mcp.json`
when Claude is also selected. A Pi-only project does not get a separate MCP output.

At global scope, Loadout writes the selected harness's server configuration, including Claude's
`.claude.json` and the owned server tables in Codex's `config.toml`.

## Native settings and defaults

JSON settings fragments belong under `settings/`. For example:

```json title="settings/claude.json"
{
  "permissions": {
    "defaultMode": "default"
  }
}
```

Select the fragment:

```toml
[claude]
settings = "claude"
```

Claude and OpenCode carry native settings alongside rendered slices. Codex top-level settings
use a separate, explicitly selected `defaults` slice:

```json title="defaults/codex.json"
{
  "model_reasoning_effort": "high"
}
```

```toml
[codex]
defaults = "codex"
```

The keys and values inside these fragments follow the harness's schema. Loadout records owned
key names beside fragments when needed so removing a previously managed key removes it from
the destination too. Keep those records with the source.

## Hooks

Select JSON hook fragments from `hooks/`:

```toml
[claude]
hooks = ["session"]

[codex]
hooks = ["session"]
```

A fragment uses event names and hook definitions:

```json title="hooks/session.json"
{
  "SessionStart": [
    {
      "hooks": [
        {
          "type": "command",
          "command": "printf ready"
        }
      ]
    }
  ]
}
```

Claude and Codex receive native hook configuration. OpenCode and Pi receive generated adapters
for supported command hooks. Event mapping and payload differences matter, so test hooks in
each agent you enable. Hook scripts can travel through module configuration.

## Plugins

A global `plugins/` fragment declares enablement and package references:

```json title="plugins/tools.json"
{
  "plugins": {
    "my-tools": {
      "marketplace": "my-marketplace",
      "source": "git:github.com/example/my-tools"
    }
  }
}
```

Replace the example identifiers with an installed plugin and its actual marketplace/source,
then select the fragment:

```toml
[claude]
plugins = "tools"

[pi]
plugins = "tools"
```

Claude and Codex render marketplace-based enablement. Pi renders package references.
Installing packages and registering Claude marketplaces remain part of your harness setup.

OpenCode plugin files go through module configuration rather than this enablement slice.

## Module configuration and scripts

Place a file at its required relative path under `module-config/<agent>/`:

```text
module-config/
├── claude/hooks/session.sh
├── opencode/plugins/my-plugin.ts
└── pi/extensions/my-extension/config.json
```

Loadout copies it beneath the corresponding global agent directory, preserving bytes and
executable permissions. The consuming module determines the filename.

The slice is automatic for Claude, OpenCode, and Pi; opt out with `module-config = false`.
Files must not collide with another slice's output.
