---
title: Overlays
description: Render your own agent configuration into repositories that have not adopted loadout, without touching anything the team tracks.
---

# Overlays: personal configuration for repositories you do not control

A project source is for a repository whose team has adopted loadout. An **overlay** is for the
other case: you work in clones of team repositories that have not adopted it, and you want your
own instructions, MCP servers, permissions and skills in each of them, for any of the five
agents, without committing anything.

An overlay is a folder outside those repositories. Its `loadout.toml` has an `[overlay]` table
that lists the repositories it renders into. You register the folder once, and `sync` writes the
files into every matching clone, hides them from git, and records what it wrote so it can
retire them later.

A project source, the global source and an overlay do different jobs:

| Use | When |
| --- | --- |
| A [project](projects.md) | The repository has adopted loadout; everyone working in it shares the source |
| The [global source](global.md) | The configuration belongs to your machine, whatever repository you are in |
| An overlay | The configuration is yours, belongs to a set of repositories, and the repositories are not yours to change |

## Write an overlay

```toml title="~/agent-config/work/loadout.toml"
[overlay]
targets = ["~/work/app*", "~/work/api"]

[[source]]
name = "work"
path = "."

[all]
instructions = ["work"]

[claude]
settings = "local"

[codex]
[opencode]
[pi]
```

```text
~/agent-config/work/
  loadout.toml
  instructions/work.md        personal instructions for these repositories
  settings/local.json         Claude's settings fragment, which `settings = "local"` names
  permissions.toml            optional permission rules
  mcp.toml                    optional MCP server definitions
  skills/                     optional skills, one folder each
```

`targets` are absolute or `~/` patterns, expanded again on every run, so a clone added later is
picked up by the next `sync`. Each match must be the top of a git checkout. A match that is not
a checkout, a submodule, a linked worktree, a folder inside a checkout without its own `.git`, or
a repository that has adopted loadout (it holds `loadout/config.toml`) is skipped with a message;
a match that is the home folder, holds a harness home or the overlay folder, sits inside the
overlay folder, or has its own `.git` without being its checkout's top is refused.

The rest of the manifest is a [global manifest](../reference/configuration.md#global-manifest):
`[[source]]` entries, `[all]`, and one table per agent. An overlay renders four things:
instructions, MCP servers, permissions with the settings fragment, and skills. Naming anything
else, such as `hooks`, `plugins` or `agents`, is a configuration error that says why. The
[configuration reference](../reference/configuration.md#overlay-sources) lists them, with the
file each one writes.

The overlay's own sources supply exactly the MCP servers it renders, so `mcp = ["name"]` is
refused; `mcp = false` turns the slice off for an agent.

## Register, preview, sync

```sh
loadout overlay add ~/agent-config/work
loadout check --overlay work
loadout sync --overlay work
```

`overlay add` checks the manifest and records the folder in `~/.config/loadout/overlays.toml`.
Registering is what allows the folder to write: an overlay folder that is not registered is
refused. `check` lists what `sync` would write as `missing`, and the notes it adds, such as a
project the agent does not trust yet. `sync` writes the files and prints one line per change.
With `instructions/work.md` and a `settings/local.json` that sets `model`, the first sync of
`~/work/app1` prints:

```text
overlay work: ~/agent-config/work: wrote .loadout/opencode-instructions.md
overlay work: ~/work/app1: wrote .claude/settings.local.json
overlay work: ~/work/app1: wrote .codex/config.toml
overlay work: ~/work/app1: wrote .pi/APPEND_SYSTEM.md
overlay work: ~/work/app1: wrote CLAUDE.local.md
overlay work: ~/work/app1: wrote opencode.json
overlay work: ~/work/app1: 5 written, 0 unchanged, 0 retired
```

The first line is the file OpenCode's `opencode.json` names, which the overlay keeps in its own
folder. With an `mcp.toml`, a line such as `overlay work: ~/.claude.json: 1 server written, 0
retired` follows.

From then on, edit the overlay folder, never the generated files. A file that differs from what
`sync` wrote is `hand-edited`, and `sync` refuses to overwrite it until you port the edit into the
overlay or pass `--force`, exactly as for other loadout outputs.

You can also run `loadout sync` or `loadout check` without options. Inside a target repository
they handle that repository for every overlay that targets it, and in a registered overlay
folder (its top, not a subfolder) they handle all of its targets. [Selection](../reference/cli.md#overlays) lists every case.

## What stays out of git

An overlay never writes a path the team tracks: that is refused, with the path named, and the rest
of the repository's outputs are still written. On a case-insensitive file system, such as macOS's
default, a path the team tracks under another case (`Opencode.json`) counts as tracked. To keep the rest out of `git status`, sync adds a
marked block to the repository's `.git/info/exclude` before it writes a file, and removes the
line only after the file is gone. Nothing in the repository's tracked files, and no committed
`.gitignore`, changes. A path that a committed `.gitignore` re-includes, such as
`!/CLAUDE.local.md`, would still show, so `check` and `sync` both refuse it (exit 3) and write the
rest. After writing the block, sync asks git again whether each path is ignored, and refuses a
path that would still show.

Three of the outputs, `CLAUDE.local.md`, `.claude/settings.local.json` and
`.factory/settings.local.json`, are the same in every repository. The first time a terminal sync
writes one, it offers to add them to your global git ignore file (`core.excludesFile` in your global
git config, never a repository's own, else `~/.config/git/ignore`) in a marked block. Your answer is kept and the offer is not repeated. A
pattern you wrote in that file yourself is never touched.

## Files that already exist

- **Shared files** (`.claude/settings.local.json`, `.factory/settings.local.json`, the root
  `opencode.json`) usually exist already. The overlay owns only the keys and rules it renders;
  everything else in the file is left as it is. A permission that Claude saved with "don't ask
  again" is kept, after the overlay's own rules, and `check` lists it so you can add it to the
  overlay's `permissions.toml` or delete it.
- **Every other output** is a file the overlay owns whole. A file already at that path that
  loadout never wrote is refused, and `sync --force` replaces it. The exclude block then hides
  the replaced content from git, so take a copy first if you need it.
- **`~/.claude.json`** holds Claude's MCP servers for a repository, at
  `projects.<repository>.mcpServers`. The overlay writes and retires its own servers there and
  touches nothing else in the file. A server of the same name that you added with `claude mcp add`
  is refused; remove it first. A symlinked `HOME` or `CLAUDE_CONFIG_DIR` that reaches the same
  file is the same file. After `CLAUDE_CONFIG_DIR` changes, the servers are moved to the new file;
  a run under a different `HOME` from the one the overlay first synced under writes the servers
  into that `HOME`'s file and leaves the original file's servers alone.

## Removing

```sh
loadout overlay remove work
```

This deletes what the overlay wrote in every target, takes its lines out of each exclude file,
removes its servers from `~/.claude.json`, and unregisters the folder. Folders that held its
files, such as `.claude/` or `.codex/`, stay in the target even when empty. It works from the
record, so it also works after the overlay folder or a target is gone. Files edited since sync wrote
them are refused; `--force` discards the edits, and also forgets a target that cannot be
reached, such as a repository on an unmounted volume. Without a terminal it needs `--yes`.

A repository that stops matching `targets`, or is deleted, is retired on the next full `sync`
in the same way. Nothing is retired from a target loadout cannot examine: an unreadable folder, a
failed git call or an unmounted volume is reported and left alone. A file the overlay wrote that
the team has since started tracking is never deleted: it is left in place, dropped from the
record, and noted.

## When a repository adopts loadout

`loadout init project` is refused in a repository an overlay has written into, because it would
sweep the overlay's personal files into the team's source. Release the repository first: narrow
`targets` so they no longer match it, then run `loadout sync --overlay work`, which retires what
the overlay wrote there. A plain `loadout sync` inside the repository does not reach the overlay
once it no longer matches. After that, `init project` runs as usual.

A repository can also adopt loadout without you, when the team commits a `loadout/` folder and
you pull it. The next `loadout sync` in it runs the overlay first, which retires its files there
with the note `adopted loadout; the overlay retired its files here`, and then syncs the project.
From then on the overlay skips the repository; use the project's
[personal files](../reference/configuration.md#personal-files) for configuration of your own.

## Limits

- The agent must trust the project before it reads what an overlay wrote. `check` says so for
  Codex and Pi when their trust files show it is missing.
- Switching a repository to a branch that tracks one of the overlay's paths replaces the overlay's
  copy with the team's. `check` names the branch.
- `follow_worktrees` and `git_hook` are reserved keys for a later release; setting either to
  `true` is refused.
- In parts of `~/.claude.json` the overlay does not own, a very small or very large number can be
  re-spelled when the file is rewritten (`1.23e-05` for `0.0000123`). The value does not change.

How the record, the locks and retirement work is described in
[what sync protects, retires and reports](https://github.com/nielsmadan/loadout/blob/main/docs/reference/sync.md#overlays).
