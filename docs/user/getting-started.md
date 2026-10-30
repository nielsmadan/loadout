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
loadout init project --harness claude --harness codex
```

`init` lists what it will write and asks `Apply this migration? [y/N]`; answer `y`. Without a
terminal, add `--yes` once you have read the list with `--dry-run`.

This creates `loadout/config.toml` and `loadout/permissions.toml`, then renders only what already
has content and lists it in `.gitignore`. In an empty directory that is `.claude/settings.json`
alone: there are no instructions yet, and Codex gets no rules file while its rules would be empty.
Project setup works without a global configuration.

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

Write the shared permission file:

```toml title="loadout/permissions.toml"
[shell]
allow = ["git status", "git diff"]
ask = ["git commit"]
deny = ["git push"]
```

Both permission files are optional. A missing `permissions.toml` means the same as an empty one:
no shared rules. The next sync writes `.claude/settings.json` with empty lists, and retires a
Codex rules file it wrote earlier rather than writing it empty. The personal
`loadout/permissions.local.toml` can stay absent too.

## Render both agents' files

`init` already rendered the setup it found. Now that the source has changed, render again:

```sh
loadout sync
loadout check
```

The resulting files include:

| File | Consumer |
| --- | --- |
| `AGENTS.md` | Codex, and Claude Code through `CLAUDE.md` |
| `CLAUDE.md` | Claude Code |
| `.claude/settings.json` | Claude Code's permission rules |
| `.codex/rules/permissions.rules` | Codex's permission rules |

`AGENTS.md` holds the fragment. `CLAUDE.md` holds only Loadout's banner and an `@AGENTS.md`
line, which makes Claude Code read the same document. Permission syntax differs because the
agents read different formats. Open either agent in this directory to use its generated setup.

`loadout check` exits successfully when the generated files match their sources.

## Change the source once

Add a sentence to `loadout/instructions/project.md`, then run:

```sh
loadout sync
loadout check
```

`AGENTS.md` now contains the new sentence, and Claude Code reads it through `CLAUDE.md`. Keep
editing the source fragments, rather than the generated `CLAUDE.md` or `AGENTS.md`.

## Add another agent

Enable OpenCode in the same project by adding it to `harnesses` in `loadout/config.toml`:

```toml
harnesses = ["claude", "codex", "opencode"]
```

Then run `loadout sync`. OpenCode uses the existing `AGENTS.md` and receives its own
`opencode.json`, and `.gitignore` gains its generated paths.
For OpenCode skills, also follow the [setup note](reference/harnesses.md#opencode-skills).

## Use this in an existing repository

Run `loadout init project` there too. It adopts the instructions it finds: a `CLAUDE.md` or
`AGENTS.md` becomes `loadout/instructions/main.md`, and the original is generated from then on.
When Git tracks the original, init removes it from the index and adds it to `.gitignore`, so
`git status` shows a staged deletion for you to commit. Copies that disagree with each other are
left in place for you to merge. Later, if Git still tracks a generated path, `sync` prints a
warning and `check` a note, each with the `git rm -r --cached` command that untracks it and keeps
the file.

Keep existing permission grants in the shared or personal source file. Sync reports protected
files that contain edits it cannot attribute to Loadout.

The [project guide](guides/projects.md) explains what to commit and how adoption works for a team.
The [troubleshooting guide](troubleshooting.md#files-modified-outside-loadout) covers protected edits.

## Where to go next

- [Global setup](guides/global.md): reuse configuration across all your projects.
- [Templates](guides/templates.md): share the context needed by a particular kind of project.
- [Composition and inheritance](guides/composition.md): assemble sources and switch profiles.
