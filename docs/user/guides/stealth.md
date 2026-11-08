---
title: Stealth sources
description: Your own agent config in repos you don't control, kept out of git.
---

# Stealth sources

Your own agent config in repos you don't control, kept out of git.

A project source is for a repository whose team has adopted loadout. A **stealth source** is for
the other case: you work in clones of team repositories that have not adopted it, and you want
your own instructions, MCP servers, permissions and skills in each of them, for any of the five
harnesses, without committing anything. This is loadout's stealth mode.

A stealth source is a folder outside those repositories. Its `loadout.toml` has a `[stealth]` table
that lists the repositories it renders into. You register the folder once, and `sync` writes the
files into every matching clone, hides them from git, and records what it wrote so it can
retire them later.

A project source, the global source and a stealth source do different jobs:

| Use | When |
| --- | --- |
| A [project](projects.md) | The repository has adopted loadout; everyone working in it shares the source |
| The [global source](global.md) | The configuration belongs to your machine, whatever repository you are in |
| A stealth source | The configuration is yours, belongs to a set of repositories, and the repositories are not yours to change |

## Write a stealth source

Start one with `stealth init`, which writes the folder's `loadout.toml` and registers it:

```sh
loadout stealth init ~/agent-config/work --targets "~/work/app*" --targets "~/work/api"
```

It lists the checkouts the patterns match now, and those it would skip with the reason, then
asks before registering, since a registered folder writes into every match (pass `--yes` where
there is no terminal). It declares the harnesses given with `--harness`, or else the `harnesses`
in your [machine config](../reference/configuration.md#machine-configuration), or else the harnesses it
finds configured. It writes nothing else and does not sync.
With Claude and Codex configured, it writes:

```toml
[stealth]
targets = ["~/work/app*", "~/work/api"]

[[source]]
name = "work"
path = "."

[claude]
[codex]
```

Then add what you want rendered. A full stealth source looks like this:

```toml title="~/agent-config/work/loadout.toml"
[stealth]
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
a match that is the home folder, holds a harness home or the stealth folder, sits inside the
stealth folder, or has its own `.git` without being its checkout's top is refused.

The rest of the manifest is a [global manifest](../reference/configuration.md#global-manifest):
`[[source]]` entries, `[all]`, and one table per harness. A stealth source renders four things:
instructions, MCP servers, permissions with the settings fragment, and skills. Naming anything
else, such as `hooks`, `plugins` or `agents`, is a configuration error that says why. The
[configuration reference](../reference/configuration.md#stealth-sources) lists them, with the
file each one writes.

The stealth source's own `[[source]]` entries supply exactly the MCP servers it renders, so `mcp = ["name"]` is
refused; `mcp = false` turns the slice off for a harness.

## Register, preview, sync

```sh
loadout stealth add ~/agent-config/work
loadout check --stealth work
loadout sync --stealth work
```

`stealth init` has already registered its folder. For a folder whose `loadout.toml` you wrote
yourself, `stealth add` checks the manifest and records the folder in
`~/.config/loadout/stealth.toml`.
Registering is what allows the folder to write: a stealth folder that is not registered is
refused. `check` lists what `sync` would write as `missing`, and the notes it adds, such as a
project the harness does not trust yet. `sync` writes the files and prints one line per change.
With `instructions/work.md` and a `settings/local.json` that sets `model`, the first sync of
`~/work/app1` prints:

```text
stealth work: ~/agent-config/work: wrote .loadout/opencode-instructions.md
stealth work: ~/work/app1: wrote .claude/settings.local.json
stealth work: ~/work/app1: wrote .codex/config.toml
stealth work: ~/work/app1: wrote .pi/APPEND_SYSTEM.md
stealth work: ~/work/app1: wrote CLAUDE.local.md
stealth work: ~/work/app1: wrote opencode.json
stealth work: ~/work/app1: 5 written, 0 unchanged, 0 retired
```

The first line is the file OpenCode's `opencode.json` names, which the stealth source keeps in its own
folder. With an `mcp.toml`, a line such as `stealth work: ~/.claude.json: 1 server written, 0
retired` follows.

From then on, edit the stealth folder, never the generated files. A file that differs from what
`sync` wrote is `hand-edited`, and `sync` refuses to overwrite it until you port the edit into the
stealth source or pass `--force`, exactly as for other loadout outputs.

You can also run `loadout sync` or `loadout check` without options. Inside a target repository
they handle that repository for every stealth source that targets it, and in a registered stealth source
folder (its top, not a subfolder) they handle all of its targets. [Selection](../reference/cli.md#stealth-sources) lists every case.

## What stays out of git

A stealth source never writes a path the team tracks: that is refused, with the path named, and the rest
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
  `opencode.json`) usually exist already. The stealth source owns only the keys and rules it renders;
  everything else in the file is left as it is. A permission that Claude saved with "don't ask
  again" is kept, after the stealth source's own rules, and `check` lists it so you can add it to the
  stealth source's `permissions.toml` or delete it.
- **Every other output** is a file the stealth source owns whole. A file already at that path that
  loadout never wrote is refused, and `sync --force` replaces it. The exclude block then hides
  the replaced content from git, so take a copy first if you need it.
- **`~/.claude.json`** holds Claude's MCP servers for a repository, at
  `projects.<repository>.mcpServers`. The stealth source writes and retires its own servers there and
  touches nothing else in the file. A server of the same name that you added with `claude mcp add`
  is refused; remove it first. A symlinked `HOME` or `CLAUDE_CONFIG_DIR` that reaches the same
  file is the same file. After `CLAUDE_CONFIG_DIR` changes, the servers are moved to the new file;
  a run under a different `HOME` from the one the stealth source first synced under writes the servers
  into that `HOME`'s file and leaves the original file's servers alone.

## Removing

```sh
loadout stealth remove work
```

This deletes what the stealth source wrote in every target, takes its lines out of each exclude file,
removes its servers from `~/.claude.json`, and unregisters the folder. Folders that held its
files, such as `.claude/` or `.codex/`, stay in the target even when empty. It works from the
record, so it also works after the stealth folder or a target is gone. Files edited since sync wrote
them are refused; `--force` discards the edits, and also forgets a target that cannot be
reached, such as a repository on an unmounted volume. Without a terminal it needs `--yes`.

A repository that stops matching `targets`, or is deleted, is retired on the next full `sync`
in the same way. Nothing is retired from a target loadout cannot examine: an unreadable folder, a
failed git call or an unmounted volume is reported and left alone. A file the stealth source wrote that
the team has since started tracking is never deleted: it is left in place, dropped from the
record, and noted.

## When a repository adopts loadout

`loadout init project` is refused in a repository a stealth source has written into, because it would
sweep the stealth source's personal files into the team's source. Release the repository first: narrow
`targets` so they no longer match it, then run `loadout sync --stealth work`, which retires what
the stealth source wrote there. A plain `loadout sync` inside the repository does not reach the stealth source
once it no longer matches. After that, `init project` runs as usual.

A repository can also adopt loadout without you, when the team commits a `loadout/` folder and
you pull it. The next `loadout sync` in it runs the stealth source first, which retires its files there
with the note `adopted loadout; the stealth source retired its files here`, and then syncs the project.
From then on the stealth source skips the repository; use the project's
[personal files](../reference/configuration.md#personal-files) for configuration of your own.

## Limits

- The harness must trust the project before it reads what a stealth source wrote. `check` says so for
  Codex and Pi when their trust files show it is missing.
- Switching a repository to a branch that tracks one of the stealth source's paths replaces the stealth source's
  copy with the team's. `check` names the branch.
- `follow_worktrees` and `git_hook` are reserved keys for a later release; setting either to
  `true` is refused.
- In parts of `~/.claude.json` the stealth source does not own, a very small or very large number can be
  re-spelled when the file is rewritten (`1.23e-05` for `0.0000123`). The value does not change.

How the record, the locks and retirement work is described in
[what sync protects, retires and reports](https://github.com/nielsmadan/loadout/blob/main/docs/reference/sync.md#stealth-sources).

## Upgrading from 0.10.0

In loadout 0.10.0 a stealth source was called an overlay: `loadout overlay`, `--overlay` and an
`[overlay]` table. This version does not migrate what 0.10.0 recorded for an overlay, so remove
each one with 0.10.0 before you upgrade:

```sh
loadout overlay list
loadout overlay remove NAME --yes
```

That retires everything the overlay wrote, including its exclude lines and its `~/.claude.json`
servers. After upgrading, rename `[overlay]` to `[stealth]` in the folder's `loadout.toml`,
register it with `loadout stealth add`, and sync it with `loadout sync --stealth NAME`. The first
sync offers the global git ignore block again, since 0.10.0's answer is not read.

If you already upgraded, this version refuses until the overlay is gone. While
`~/.config/loadout/overlays.toml` lists an overlay, `stealth init`, `stealth add`, `stealth remove` and every
stealth part of `sync` and `check` exit 3; in a target whose `.git/info/exclude` still has a
`# >>> loadout overlay NAME` block, `sync`, `check` and `init project` exit 3 for that target.
`loadout stealth list` still prints its list and exits 0, then warns with the same message. Each
message gives the command to run, which uses 0.10.0 without installing it:

```sh
uvx --python 3.13 --from git+https://github.com/nielsmadan/loadout.git@v0.10.0 loadout overlay remove NAME --yes
```

To clean up by hand instead, delete `~/.config/loadout/overlays.toml` and, in each target, the
`# >>> loadout overlay NAME` block in `.git/info/exclude`; those two are what this version checks.
Also delete the files that block lists, the `# >>> loadout overlays` block in your global git
ignore file, the overlay's servers in `~/.claude.json`, and `~/.config/loadout/overlay-written/`,
`~/.config/loadout/overlay-global-ignore.json` and `~/.local/state/loadout/overlay-locks/`.
