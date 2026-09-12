---
title: Harness support
description: What this version of Loadout renders for Claude Code, Codex, OpenCode, and Pi, including portability limits.
---

# Harness support

Loadout targets Claude Code, Codex, OpenCode, and Pi. These tables describe **what Loadout
renders**, not the complete capability of each agent.

They are checked against this project's
[global preset](https://github.com/nielsmadan/loadout/blob/main/src/loadout/agents.py) and
[project preset](https://github.com/nielsmadan/loadout/blob/main/src/loadout/project.py).
An absent output is an implementation limit of Loadout.

## Global scope

| Configuration | Claude Code | Codex | OpenCode | Pi |
| --- | --- | --- | --- | --- |
| Instructions | Yes | Yes | Yes | Yes |
| Shell permissions | Yes | Yes | Yes | Permission extension |
| MCP tool policy | Yes | Yes, with server config | Yes | Permission extension |
| MCP server definitions | Yes | Yes | Yes | MCP adapter |
| Skills | Yes | Yes | Yes | Yes |
| Native settings | JSON settings | Defaults slice | JSON settings | JSON settings with plugins selected |
| Hooks | Native document | Native document | Command-hook adapter | Command-hook adapter |
| Plugin declarations | Marketplace enablement | Enablement and registrations | Plugin files via module-config | Package references |
| Module files | Yes | Not rendered | Yes | Yes |

Pi needs the corresponding permission extension and MCP adapter to consume those generated
files. Loadout renders configuration; it does not install those integrations.

Claude's global MCP servers are written into the owned `mcpServers` key in `.claude.json`.
Codex's server definitions and policy share the owned `mcp_servers` tables in `config.toml`.
Unrelated state in those co-owned files is preserved.

## Project scope

| Configuration | Claude Code | Codex | OpenCode | Pi |
| --- | --- | --- | --- | --- |
| Instructions | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` | `AGENTS.md` |
| Shell permissions | Yes | Yes | Yes | Permission extension |
| MCP tool policy | Yes | Not rendered | Yes | Permission extension |
| MCP definitions | `.mcp.json` | Not rendered | `opencode.json` | Reads shared `.mcp.json` through adapter |
| Skills | `.claude/skills/` | Not rendered | `.opencode/skills/` | `.pi/skills/` |

The Pi MCP case requires the shared file to exist, for example because Claude is also enabled.
A Pi-only project has no MCP-definition output.

This Loadout version has no project outputs for native settings/defaults, hooks, plugin
declarations, or module files. Project templates contribute permissions, instructions, skills,
and MCP definitions to the supported targets.

## Permission portability

| Behavior | Claude Code | Codex | OpenCode | Pi |
| --- | --- | --- | --- | --- |
| Native decision order | Deny, ask, allow | Most restrictive | Last match | Last match |
| Command matching | Patterns | Literal prefixes | Globs | Globs |
| Loadout trailing shell globs | Rendered | Skipped | Rendered | Rendered |
| Loadout shell catch-all | Native settings instead | Separate approval setting | Rendered | Rendered |

Loadout expands bare commands where a harness needs a second rule for arguments and emits
rules in the order required by last-match matchers. It cannot make every matcher equivalent.

Allowing a command that launches other commands, such as a shell interpreter, can make narrower
deny rules ineffective. Review broad permissions using the harness's actual enforcement model.

The [maintainer's matching research](https://github.com/nielsmadan/loadout/blob/main/docs/reference/README.md)
records the version-specific evidence and detailed examples.

## OpenCode skills

OpenCode also discovers Claude skill directories by default. When Loadout renders different
skill content for each harness, disable that overlapping discovery in your shell:

```sh
export OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1
```

Add the setting to your shell configuration and start a new session. Loadout's `check` command
reports when neither this variable nor the broader `OPENCODE_DISABLE_CLAUDE_CODE` is enabled.

## Global destination roots

| Harness | Default directory | Relocation variable |
| --- | --- | --- |
| Claude Code | `~/.claude/` | `CLAUDE_CONFIG_DIR` |
| Codex | `~/.codex/` | `CODEX_HOME` |
| OpenCode | `~/.config/opencode/` | `XDG_CONFIG_HOME` |
| Pi | `~/.pi/agent/` | `PI_CODING_AGENT_DIR` |

Claude's global MCP file defaults to `~/.claude.json`. With CLAUDE_CONFIG_DIR set, Loadout
places `.claude.json` under that directory, alongside the relocated configuration.

For OpenCode, XDG_CONFIG_HOME is the parent configuration directory. Loadout appends
`opencode/`. `OPENCODE_CONFIG_DIR` is a different mechanism and does not relocate these
Loadout destinations.
