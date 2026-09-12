---
title: Project setup
description: Share repository configuration while keeping personal permission rules local.
---

# Share configuration in a project

A project keeps the sources that everyone shares in `loadout/`. Each person renders their own
agent files, including their local permission choices.

## Initialise and choose agents

From the repository root:

```sh
loadout init --harness claude --harness codex
```

Use `loadout harness add` for subsequent additions:

```sh
loadout harness add pi
loadout sync
```

Both commands update the output paths in `.gitignore`. You can also target a repository
explicitly with `--root /path/to/project`.

## Shared and personal sources

| Source | Purpose | Commit it? |
| --- | --- | --- |
| `loadout/config.toml` | Agents, instruction order, templates | Yes |
| `loadout/permissions.toml` | Shared permission rules | Yes |
| `loadout/permissions.local.toml` | Your permission rules for this repository | No |
| `loadout/instructions/` | Shared instruction fragments | Yes |
| `loadout/skills/` | Project skill trees | Yes |
| `loadout/mcp.toml` | Project MCP server definitions | Yes |
| `loadout/templates/` | Vendored template sources | Yes |
| Generated agent files | Combined output for this checkout | No |

Personal project configuration currently applies to permissions. Instructions, skills, and
server definitions have shared project sources.

Everyone using the managed project configuration needs Loadout installed and must run
`loadout sync` after pulling source changes. Generated project files are gitignored because
they can contain personal rules.

## Compose instructions

```toml title="loadout/config.toml"
harnesses = ["claude", "codex"]
instructions = ["project", "testing"]
```

Create the matching files under `loadout/instructions/`. They are composed in the listed order.
Templates contribute their instructions before the project's fragments.

After adding an instruction order to a project that previously generated only permissions,
rerun `loadout init` with the same current harness list to add the new documents to `.gitignore`:

```sh
loadout init --harness claude --harness codex
loadout sync
```

All selected agents use one project instruction order. Claude receives `CLAUDE.md`; Codex,
OpenCode, and Pi share `AGENTS.md`.

## Add personal permissions

```toml title="loadout/permissions.local.toml"
[shell]
allow = ["just local-test"]
```

Then run `loadout sync`. For the same permission entry, deny beats ask, and ask beats allow
across the template, shared, and personal tiers. A personal allow cannot cancel a shared deny.

For overlapping patterns, the harness's matcher still matters. See
[permission portability](../reference/harnesses.md#permission-portability).

## Adopt existing files

Move authored instructions into fragments and record their order before generating the output.
Transfer existing rules into the appropriate permission source.

Loadout preserves foreign keys in project `.claude/settings.json` and `opencode.json`.
It still owns the subtrees it renders, so edit permission rules in the Loadout source.

A tracked `CLAUDE.md` or `AGENTS.md` needs to leave git tracking once it becomes a generated
file. Adding a path to `.gitignore` does not untrack an existing file. Review this migration
with the repository's maintainers.

## Global and project scope

The agents load global and project files according to their own rules. Loadout writes the two
scopes separately; it does not turn them into one universal inheritance chain.

Use [global setup](global.md) for the defaults you want everywhere. Use
[templates](templates.md) for shared context that only some projects need.
