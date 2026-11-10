---
title: Harness support
description: What this version of Loadout renders for Claude Code, Codex, Cursor CLI, Factory Droid, OpenCode, and Pi, including portability limits.
---

# Harness support

Loadout targets Claude Code, Codex, Cursor CLI, Factory Droid, OpenCode, and Pi. These tables describe **what Loadout
renders**, not the complete capability of each harness.

They are checked against this project's
[global preset](https://github.com/nielsmadan/loadout/blob/main/src/loadout/harnesses.py) and
[project preset](https://github.com/nielsmadan/loadout/blob/main/src/loadout/project.py).
An absent output is an implementation limit of Loadout.

## Global scope

| Configuration | Claude Code | Codex | Cursor CLI | Factory Droid | OpenCode | Pi |
| --- | --- | --- | --- | --- | --- | --- |
| Instructions | Yes | Yes | The rule `~/.cursor/rules/loadout.mdc` | Yes | Yes | Yes |
| Shell permissions | Yes | Yes | `permissions` in `cli-config.json` | Yes | Yes | Permission extension |
| MCP tool policy | Yes | Yes, with server config | Yes, in the permission lists | Not rendered | Yes | Permission extension |
| MCP server definitions | Yes | Yes | Yes | Yes | Yes | Yes |
| Skills | Yes | Yes | Yes | Yes | Yes | Yes |
| Agents | `~/.claude/agents/` | `~/.codex/agents/`, as TOML | `~/.cursor/agents/` | `~/.factory/droids/` | `~/.config/opencode/agents/` | `~/.pi/agent/agents/`, read by `pi-subagents` |
| Native settings | JSON settings | Defaults slice | Named keys in `cli-config.json` | JSON settings | JSON settings | JSON settings with plugins selected |
| Hooks | Native document | Native document | Native document | Native document | Command-hook adapter | Command-hook adapter |
| Plugin declarations | Marketplace enablement | Enablement and registrations | Not rendered | Enablement and registrations | Plugin files via module-config | Package references |
| Module files | Yes | Not rendered | Yes | Yes | Yes | Yes |

Pi needs the permission extension, pi-permission-system, to apply the generated permissions.
Loadout renders configuration; it does not install that extension.

Loadout renders Pi's MCP servers and policy for Pi's built-in MCP. `pi-mcp-adapter` is not
supported in 1.0: when installed it replaces the built-in, so Pi does not read the servers
loadout writes, and the policy does not govern the adapter's tool.

Claude's global MCP servers are written into the owned `mcpServers` key in `.claude.json`.
Codex's server definitions and policy share the owned `mcp_servers` tables in `config.toml`.
Unrelated state in those co-owned files is preserved.

## Project scope

| Configuration | Claude Code | Codex | Cursor CLI | Factory Droid | OpenCode | Pi |
| --- | --- | --- | --- | --- | --- | --- |
| Instructions | `CLAUDE.md` | `AGENTS.md` | `AGENTS.md` | `AGENTS.md` | `AGENTS.md` | `AGENTS.md` |
| Shell permissions | Yes | Yes | `.cursor/cli.json` | Yes | Yes | Permission extension |
| MCP tool policy | Yes | Not rendered | Yes, in `.cursor/cli.json` | Not rendered | Yes | Permission extension |
| MCP definitions | `.mcp.json` | Not rendered | `.cursor/mcp.json` | `.factory/mcp.json` | `opencode.json` | `.pi/mcp.json`, read once the project is trusted |
| Skills | `.claude/skills/` | `.agents/skills/` | `.cursor/skills/`, or shared `.agents/skills/` | `.factory/skills/` | `.opencode/skills/`, or shared `.agents/skills/` | `.pi/skills/`, or shared `.agents/skills/` |
| Agents | `.claude/agents/` | `.codex/agents/`, as TOML | `.cursor/agents/` | `.factory/droids/` | `.opencode/agents/` | `.pi/agents/`, read by `pi-subagents` |
| Hooks | Not rendered | Not rendered | `.cursor/hooks.json` | `.factory/hooks.json` | Not rendered | Not rendered |
| Plugin declarations | Not rendered | Not rendered | Not rendered | `.factory/settings.json` | Not rendered | Not rendered |

A project skill is written once to `.agents/skills/`, which Codex, Cursor, OpenCode and Pi all
read, when Codex is selected and every one of those harnesses selected for the skill gets the
same bytes. Otherwise Cursor, OpenCode and Pi get their own folders.

`loadout/settings/claude.json`, `droid.json` and `opencode.json` supply the native settings keys
of `.claude/settings.json`, `.factory/settings.json` and `opencode.json` that no slice writes.
Project scope has no output for Codex defaults, Pi settings or module files. Project kits
contribute permissions, instructions, skills, MCP definitions, and Droid's hooks and plugins.

## Stealth scope

A [stealth source](../guides/stealth.md) renders instructions, MCP servers, permissions and skills for
all six harnesses into repositories that have not adopted loadout, and nothing else. Codex gets MCP
servers there, which project scope does not render. The files are in the
[destination table](configuration.md#destinations).

## Permission portability

| Behavior | Claude Code | Codex | Cursor CLI | Factory Droid | OpenCode | Pi |
| --- | --- | --- | --- | --- | --- | --- |
| Native decision order | Deny, ask, allow | Most restrictive | Deny over allow, no ask list | Native lists | Last match | Last match |
| Command matching | Patterns | Literal prefixes | Word prefixes and globs | Literal list entries | Globs | Globs |
| Loadout trailing shell globs | Rendered | Skipped | Rendered; a deny glob past the first word widened, reported | Skipped and reported | Rendered | Rendered |
| Loadout exact shell entries (`=`) | Rendered | Allow and ask omitted, deny widened, reported | Rendered as `cmd:args` | Allow and ask omitted, deny widened, reported | Rendered | Rendered |
| Loadout shell catch-all | Native settings instead | Separate approval setting | `approvalMode` instead | Session autonomy instead | Rendered | Rendered |

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

OpenCode also scans `.agents/skills/`, where a project's Codex skills land. When a project skill's
Codex and OpenCode variants differ, also set:

```sh
export OPENCODE_DISABLE_EXTERNAL_SKILLS=1
```

`check` reports a differing project skill while this variable is unset. Restricting the skill's
`agents` so that only one of the two gets it also clears the report.

## Cursor CLI

Cursor's global permissions live in `cli-config.json`, a file the CLI also fills with its own
defaults and state. Loadout owns its `permissions` key, plus the keys a `settings` fragment names
(`approvalMode`, `sandbox.mode`, the default model), and leaves the rest alone; see
[Cursor settings](configuration.md#cursor-settings). Cursor has no ask list: an `ask` falls to
Cursor's `approvalMode`, and an allow that would cover an ask is left out so the command still
prompts. `sync` reports both.

Cursor has no global instructions file. Loadout writes the rule `~/.cursor/rules/loadout.mdc`,
which Cursor applies to every workspace under the home folder, because it reads `.cursor/rules/`
in each folder above the workspace.

By default Cursor also reads Claude's and Codex's skills, agents and hooks, and a project's
`CLAUDE.md`. Loadout writes Cursor's own copies, so with Claude selected too Cursor can see a skill
twice. Hooks are written the way Cursor itself translates Claude's, so Cursor drops the imported
duplicate. To stop the import, turn off **Include Third-Party Plugins, Skills, and Other Configs**
in Cursor's settings.

## Global destination roots

| Harness | Default directory | Relocation variable |
| --- | --- | --- |
| Claude Code | `~/.claude/` | `CLAUDE_CONFIG_DIR` |
| Codex | `~/.codex/` | `CODEX_HOME` |
| Factory Droid | `~/.factory/` | `FACTORY_HOME_OVERRIDE` |
| OpenCode | `~/.config/opencode/` | `XDG_CONFIG_HOME` |
| Cursor CLI | `~/.cursor/` | `CURSOR_CONFIG_DIR`, for `cli-config.json` only |
| Pi | `~/.pi/agent/` | `PI_CODING_AGENT_DIR` |

Claude's global MCP file defaults to `~/.claude.json`. With CLAUDE_CONFIG_DIR set, Loadout
places `.claude.json` under that directory, alongside the relocated configuration.

For OpenCode, XDG_CONFIG_HOME is the parent configuration directory. Loadout appends
`opencode/`. `OPENCODE_CONFIG_DIR` is a different mechanism and does not relocate these
Loadout destinations.

For Cursor, `CURSOR_CONFIG_DIR` moves only `cli-config.json`; unset, Cursor uses
`$XDG_CONFIG_HOME/cursor/` when XDG_CONFIG_HOME is set, and Loadout follows it. Cursor keeps
`mcp.json`, `hooks.json`, `skills/` and `agents/` in `~/.cursor/` regardless, and so does Loadout.

For Droid, `FACTORY_HOME_OVERRIDE` is the parent home directory. Loadout appends `.factory/`.
