---
title: Getting started
description: Install Loadout and use one set of project instructions and permissions with Claude Code and Codex.
---

# One setup, two agents

This walkthrough creates a small project configuration for Claude Code and Codex. You will
write the instructions once, render both agents' files, and make a change that reaches both.

## Install

Install [uv](https://docs.astral.sh/uv/getting-started/installation/), then install Loadout
directly from its repository:

```sh
uv tool install --python 3.13 git+https://github.com/nielsmadan/loadout.git
loadout --help
```

Loadout requires Python 3.13 or newer. uv can provision Python for the tool environment.
If the shell cannot find `loadout`, run `uv tool update-shell` and open a new terminal.

For an editable development installation, clone the repository and run `just install-editable` there.

## Create a project configuration

Start in a new directory to try the workflow:

```sh
mkdir loadout-demo
cd loadout-demo
loadout init --harness claude --harness codex
```

This creates `loadout/config.toml`, shared and personal permission files, and entries in
`.gitignore`. Project setup works without a global configuration.

Replace the freshly created `loadout/config.toml` with:

```toml title="loadout/config.toml"
harnesses = ["claude", "codex"]
instructions = ["project"]
```

Create the instruction directory:

```sh
mkdir -p loadout/instructions
```

Create a fragment:

```markdown title="loadout/instructions/project.md"
# Working in this project

Keep changes focused on the requested task.
Run the project's tests before reporting a change as complete.
Explain any checks you could not run.
```

Replace the shared permission file with:

```toml title="loadout/permissions.toml"
[shell]
allow = ["git status", "git diff"]
ask = ["git commit"]
deny = ["git push"]
```

The personal `loadout/permissions.local.toml` can stay empty.

## Render both agents' files

Run `init` again with the same harnesses to extend `.gitignore` for the instruction documents,
then render:

```sh
loadout init --harness claude --harness codex
loadout sync
loadout check
```

The resulting files include:

| File | Consumer |
| --- | --- |
| `CLAUDE.md` | Claude Code |
| `AGENTS.md` | Codex |
| `.claude/settings.json` | Claude Code's permission rules |
| `.codex/rules/permissions.rules` | Codex's permission rules |

The two instruction documents contain the same fragment. Permission syntax differs because the
agents read different formats. Open either agent in this directory to use its generated setup.

`loadout check` exits successfully when the generated files match their sources.

## Change the source once

Add a sentence to `loadout/instructions/project.md`, then run:

```sh
loadout sync
loadout check
```

Both instruction documents now contain the new sentence. Keep editing the source fragments,
rather than the generated `CLAUDE.md` or `AGENTS.md`.

## Add another agent

Enable OpenCode in the same project:

```sh
loadout harness add opencode
loadout sync
```

OpenCode uses the existing `AGENTS.md` and receives its own `opencode.json`.
For OpenCode skills, also follow the [setup note](reference/harnesses.md#opencode-skills).

## Use this in an existing repository

Move existing instructions into `loadout/instructions/` before enabling instruction generation.
Keep existing permission grants in the shared or personal source file. Sync reports protected
files that contain edits it cannot attribute to Loadout.

The [project guide](guides/projects.md) explains what to commit and how adoption works for a team.
The [troubleshooting guide](troubleshooting.md#files-modified-outside-loadout) covers protected edits.

## Where to go next

- [Global setup](guides/global.md): reuse configuration across all your projects.
- [Templates](guides/templates.md): share the context needed by a particular kind of project.
- [Composition and inheritance](guides/composition.md): assemble sources and switch profiles.
