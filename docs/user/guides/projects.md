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
loadout init project --harness claude --harness codex
```

For subsequent additions, add the name to `harnesses` in `loadout/config.toml`:

```toml
harnesses = ["claude", "codex", "pi"]
```

`loadout sync` then writes the new harness's outputs and extends `.gitignore` to cover them.
You can also target a repository explicitly with `--root /path/to/project`. The init options and
what init prints are in the [CLI reference](../reference/cli.md#initialise).

## Shared and personal sources

| Source | Purpose | Commit it? |
| --- | --- | --- |
| `loadout/config.toml` | Agents, instruction order, skill policy, templates | Yes |
| `loadout/permissions.toml` | Shared permission rules | Yes |
| `loadout/permissions.local.toml` | Your permission rules for this repository | No: add it to `.gitignore` or `.git/info/exclude` yourself |
| `loadout/instructions/` | Shared instruction fragments | Yes |
| `loadout/skills/` | Project skill trees | Yes |
| `loadout/skills/<name>.local/` | Your private replacement for one skill | No |
| `loadout/agents/` | Project subagents, one Markdown file each | Yes |
| `loadout/mcp.toml` | Project MCP server definitions | Yes |
| `loadout/settings/<harness>.json` | The rest of a shared settings file | Yes |
| `loadout/templates/` | Vendored template sources | Yes |
| Generated agent files | Combined output for this checkout | No |

Personal project configuration covers permissions and private skill replacements. Loadout never
adds `permissions.local.toml` to `.gitignore`, so keeping it out of Git is up to you; check that a
`.local` skill you create is ignored too. Every key of these files is in the
[configuration reference](../reference/configuration.md#project-configuration).

Everyone using the managed project configuration needs Loadout installed and must run
`loadout sync` after pulling source changes. Generated project files are gitignored because
they can contain personal rules.

## Compose instructions

```toml title="loadout/config.toml"
harnesses = ["claude", "codex"]
instructions = ["project", "testing"]
```

Create the matching files under `loadout/instructions/`. They are composed in the listed order.
Templates contribute their instructions after the project's fragments, so the document opens with
the project's own prose. A heading two fragments both bring is named in a `check` and `sync` note.

After adding an instruction order to a project that previously generated only permissions, run
`loadout sync`: it writes the new documents and adds them to `.gitignore`.

```sh
loadout sync
```

All selected agents use one project instruction order, because four harnesses share `AGENTS.md`.
With Claude and any other harness, the content goes to `AGENTS.md` and `CLAUDE.md` holds only the
banner and `@AGENTS.md`: Claude reads `AGENTS.md` only when no `CLAUDE.md` is on the path, and its
docs promise the import never makes it read `AGENTS.md` twice. A Claude-only project writes the
content to `CLAUDE.md`; a project without Claude writes only `AGENTS.md`. A `CLAUDE.md` that is a
symlink to `AGENTS.md` stays one, and is the same document. With no instructions or template prose,
neither document is generated.

## Add personal permissions

```toml title="loadout/permissions.local.toml"
[shell]
allow = ["just local-test"]
```

Add the file to `.gitignore`, or to `.git/info/exclude` for this clone alone; loadout does neither.
Then run `loadout sync`. For the same permission entry, deny beats ask, and ask beats allow
across the template, shared, and personal tiers. A personal allow cannot cancel a shared deny.

For overlapping patterns, the harness's matcher still matters. See
[permission portability](../reference/harnesses.md#permission-portability).

## Adopt existing files

`loadout init project` reads the configuration the selected harnesses already have and moves it
into `loadout/`. A `CLAUDE.md` or `AGENTS.md` becomes the fragment
`loadout/instructions/main.md`, named in `instructions`, and is generated from then on. When Git
tracks the adopted file, init adds it to `.gitignore` and removes it from the index, keeping the
file on disk; `git status` shows the removal as a staged deletion, and init's closing line names
each file. Init commits nothing.

Adding a path to `.gitignore` does not untrack it, and `sync` never changes the index. A path sync
writes that Git still tracks is named in a `WARNING` with the `git rm -r --cached -- <path>` that
untracks it, and `check` reports the same as a note rather than drift, since the file is as
rendered.

Of the eleven documents a project can generate, three are also the harness's own settings files:
`.claude/settings.json`, `.factory/settings.json` and `opencode.json`. Loadout writes its own keys
into them (permissions, MCP servers, plugins) and keeps the rest; see
[shared settings files](#shared-settings-files). Edit permission rules in the Loadout source.

What init does with a symlinked harness folder, copies that are identical across harnesses,
`.claude/settings.local.json` and the `.agents/skills` folder is in the
[migration reference](https://github.com/nielsmadan/loadout/blob/main/docs/reference/migration.md).

## What sync generates

Loadout derives every output path from its project preset. Each harness can generate:

| Harness | Generates |
| --- | --- |
| `claude` | `.claude/settings.json`, `.claude/mcp-permissions.json`, `.mcp.json`, `CLAUDE.md`, `.claude/skills/`, `.claude/agents/` |
| `codex` | `.codex/rules/permissions.rules`, `AGENTS.md`, `.agents/skills/`, `.codex/agents/` (`<name>.toml`) |
| `droid` | `.factory/settings.json`, `.factory/hooks.json`, `.factory/mcp.json`, `AGENTS.md`, `.factory/skills/`, `.factory/droids/` |
| `opencode` | `opencode.json`, `AGENTS.md`, `.opencode/skills/`, `.opencode/agents/` |
| `pi` | `.pi/extensions/pi-permission-system/config.json`, `AGENTS.md`, `.pi/skills/`, `.pi/agents/` |

A missing `permissions.toml` means the same as an empty one. The harnesses you declare are what make
loadout own their permission keys, not the file's existence, so without it `sync` writes each key
empty and clears whatever rules were there. Deleting the file clears your rules on the next sync,
just as emptying it does. Rules loadout did not write are refused first; see
[what sync protects](https://github.com/nielsmadan/loadout/blob/main/docs/reference/sync.md#files-modified-outside-loadout).
Which permission files are written with no rules in them is in the same
[reference](https://github.com/nielsmadan/loadout/blob/main/docs/reference/sync.md#permission-files-with-no-rules).

### Skills

Codex's copy of a skill goes to `.agents/skills/`. When Codex is selected and its copy is
byte-identical to OpenCode's and Pi's, whichever of those two are selected, the one copy in
`.agents/skills/` serves them all; otherwise OpenCode's goes to `.opencode/skills/` and Pi's to
`.pi/skills/`.

OpenCode needs `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1` in your shell, or it also scans
`.claude/skills/` and picks between the two copies of each skill at random. `loadout check` says so
when neither that variable nor `OPENCODE_DISABLE_CLAUDE_CODE` is set; it is advisory, never an exit
code. When Codex and OpenCode need different variants of the same skill, also set
`OPENCODE_DISABLE_EXTERNAL_SKILLS=1` so OpenCode does not scan Codex's `.agents/skills` copy. See
[OpenCode skills](../reference/harnesses.md#opencode-skills).

### MCP servers

`loadout/mcp.toml` reaches Claude (`.mcp.json`), Droid (`.factory/mcp.json`), and OpenCode
(`opencode.json`'s `mcp` key). Pi has no project destination: `.mcp.json` already serves it, since
`pi-mcp-adapter` reads it directly. Codex has none yet: whether a server survives Codex's
project-config filter is unverified. A template contributes its own `mcp.toml` beneath the project.

### Shared settings files

`.claude/settings.json`, `.factory/settings.json` and `opencode.json` are shared: loadout writes its
own keys into them, and `loadout/settings/claude.json`, `droid.json` or `opencode.json` holds
everything else, such as Claude's `env` or OpenCode's `theme`. `sync` renders the file from both,
so a clone gets the same file, and an edit to it outside loadout is reported like any other.
Without a settings file, the keys loadout does not write are kept from the file on disk.

`init` fills these from the files it adopts. A file Git tracks is already shared with the team, so
its keys go into committed source. An untracked or ignored one may hold something personal, so its
keys go into a settings file that is itself gitignored and stays on this machine.

## Links and .gitignore

Project-scope sync writes only to real paths inside the project, never under `.git` or `loadout/`,
and edits `.gitignore` only when it is a regular file rather than a symlink. A symlink committed to
the repository can route outputs anywhere: `.claude -> ../../.claude` into a home directory, or
`.claude/skills/x -> ../../.git/hooks` into a hook the next checkout runs. So no link may sit
between the project root and an output, and the only output that may itself be a link is
`CLAUDE.md -> AGENTS.md` when the same sync writes both. Any other link from one output to another,
such as `.claude/settings.json -> ../AGENTS.md`, would have sync overwrite one with the other. Sync
refuses before writing anything and names each link and where it resolves.

The same holds for what it reads: inputs come from `loadout/` alone, and `loadout/` must be a real
folder. A link in it that leads anywhere else is refused rather than copied into the repository's
outputs: a skill's supporting file pointing at a key in `~/.ssh`, into `.git`, which holds
credentials such as a token in `.git/config`, or at a gitignored `.env` beside it. A template's own
files are read only as real files, never through a link. Links that stay inside `loadout/` are
followed; folders never read, such as a skill's `.venv` or `node_modules`, are skipped, so a skill
or template may not itself be named like one. A link the file system cannot resolve, such as a pair
that lengthens the path at every hop, is refused by name. Both checks run before sync or check
compares anything, a file sync wrote and no longer renders included, so no diff of an outside file
is shown.

`.gitignore` lists exactly what sync wrote: with no `mcp.toml` there is no `.mcp.json` entry, so one
written by `claude mcp add` stays committable, and a skill is listed once, by its own folder
(`/.claude/skills/review/`), never the skills directory, so a skill installed there by hand stays
visible to git. Sync removes a `.gitignore` entry only when it retires that output, and leaves
every other line alone. Re-running init does not reinterpret an existing source; edit it and use
`check` or `sync`.

## Global and project scope

The agents load global and project files according to their own rules. Loadout writes the two
scopes separately; it does not turn them into one universal inheritance chain.

Use [global setup](global.md) for the defaults you want everywhere. Use
[templates](templates.md) for shared context that only some projects need.
