---
title: Global setup
description: Keep machine-wide agent configuration in a shared source and render it for your chosen agents.
---

# Configure your agents across projects

Global configuration holds instructions, permissions, skills, and tools you want available
across projects. Project-specific context belongs in [project configuration](projects.md)
or a [template](templates.md).

## Create a source

Choose a directory for your configuration:

```sh
loadout init --global --source ~/agent-config
```

For a new directory, Loadout creates a nested `loadout/` directory:

```text
~/agent-config/loadout/
├── loadout.toml
├── permissions.toml
└── instructions/
```

It also writes the machine configuration at `~/.config/loadout/config.toml`, or
`$XDG_CONFIG_HOME/loadout/config.toml` when XDG_CONFIG_HOME is set. That file points at
`~/agent-config/loadout`.

If the directory you pass already contains `loadout.toml`, Loadout adopts it directly.
If both the direct and nested locations contain manifests, it refuses to choose between them.
An existing machine configuration is protected from reinitialisation.

## Choose your agents

Replace the new source's starter manifest with:

```toml title="~/agent-config/loadout/loadout.toml"
[[source]]
name = "personal"
path = "."

[all]
instructions = ["workflow"]

[claude]
[codex]
```

Create `~/agent-config/loadout/instructions/workflow.md`:

```markdown
# How I work

Keep changes focused.
Explain trade-offs when there is more than one reasonable approach.
Verify your work before reporting completion.
```

Edit the source's `permissions.toml`:

```toml title="~/agent-config/loadout/permissions.toml"
[shell]
allow = ["git status", "git diff"]
ask = ["git commit"]
deny = ["git push"]
```

`[all]` supplies defaults to the agents you declare. Adding `[opencode]` or `[pi]`
opts that agent in too. It does not enable an undeclared agent.

## Sync and check

```sh
loadout sync --global
loadout check --global
```

Loadout writes to each selected agent's global configuration directory. The destinations follow
the harness environment variables listed in the [support reference](../reference/harnesses.md).

If you are adopting an existing setup, first move the configuration you want to retain into the
source. Sync may refuse an output containing changes it cannot recognise. Review its diff and
follow [protected edits](../troubleshooting.md#files-modified-outside-loadout).

## Keep the source portable

Version-control the configuration source using your normal git workflow. On another machine,
clone it and point Loadout at the directory containing `loadout.toml`:

```sh
loadout init --global --source ~/agent-config/loadout
loadout sync --global
```

The machine configuration itself is local state. It may select a profile as well as the source:

```toml title="~/.config/loadout/config.toml"
source = "~/agent-config/loadout"
profile = "focused"
```

Create `focused.toml` beside `loadout.toml` before selecting it. See
[profiles](composition.md#profiles).

## Inspect a fragment

```sh
loadout explain workflow --root ~/agent-config/loadout
```

This reports the source and consumers of the fragment. If multiple sources offer the same name,
qualify it as `personal/workflow`.

## Configure through your agent

Loadout ships a version-matched skill:

```sh
loadout skill install
loadout skill status
```

Installation shows the destination and asks for confirmation. Add `--yes` for non-interactive
installation, or `--source NAME` when several sources offer skills. It vendors the skill into
your global source and syncs the selected profile.

Restart a running agent session so it discovers the installed skill. Invoke `loadout` through
that agent's skill syntax and describe the configuration change you want.

The skill's `--personal`, `--project`, and `--global` scope vocabulary belongs to the skill.
The CLI's project commands use `--root`; they do not accept `--personal` or `--project`.
