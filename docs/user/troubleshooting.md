---
title: Troubleshooting
description: Resolve drift, protected edits, missing sources, template update conflicts, and agent setup notices.
---

# Troubleshooting

Start with the matching check command:

```sh
loadout check
loadout check --global
```

Run the first from the project root. Use the second for the machine-wide source.

## Files modified outside Loadout

Sync protects files whose content it cannot recognise as a Loadout-generated version.
It reports a diff and refuses to overwrite the edits.

1. Read the reported diff.
2. Move changes you want to retain into the appropriate Loadout source.
3. Run `loadout sync` again.
4. If the remaining output-only edits should be discarded, rerun sync with `--force`.

A file that holds a secret is never diffed. In place of its diff, sync prints one line naming
the file and counting the lines that differ, and its abort message says to read the file. Open
the file itself for step 1, then carry on with steps 2 to 4.

A harness granting a permission interactively can change a generated file. Record that grant
in `permissions.toml` or `permissions.local.toml` so it survives future syncs.

Loadout compares current output with the working source, committed source when available, and
what it last recorded writing. A destination shared between machines can still trigger a warning,
because one machine's record does not identify another machine's writes.

Outside a git repository or before a first commit, the working source and the record are the
baseline, so protection does not depend on git. On a first sync with no record yet, any file that
does not match the working source is refused. Review the named files when adopting an existing
setup, then pass `--force` once.

## Check reports drift

`check` labels each drifted file. `source-changed` and `missing` mean the source is ahead: run
the matching `loadout sync`, then check again.

`hand-edited` means the file was edited directly; follow the protected-edits steps above. This
includes a file the source no longer produces: sync refuses to retire it until you move the edit
into the source, delete the file yourself, or pass `--force`.
`orphaned` means loadout wrote the file and the source no longer produces it; the next `sync`
retires it.
`owned-key` means a key loadout writes in a file it shares with the harness, such as a server in
`~/.claude.json` added with `claude mcp add`, was edited there; port it into the source with
`/loadout`, or pass `--force` to discard it. `owned-key-removed` means such a key was removed
there: remove it from the source too, or pass `--force` to write it back.
`unknown` means loadout has neither a record of what it wrote nor a committed baseline, so it cannot
tell a source change from a hand edit: read the diff before syncing.
`unmanaged` is a file loadout did not write in a folder the harness reads whole, such as a skills
folder; port it and delete it. `unmanaged-link` is a link there; check where it points before
deleting it. `unparsable` is a shared file, global or a project's `.claude/settings.json`,
`.factory/settings.json` or `opencode.json`, that no longer parses, or is not even UTF-8: plain
`sync` refuses it with exit 3 and names it. Fix or delete it, then sync, or run `sync --force` to
replace it; one the source no longer renders has nothing to replace it with, so only fixing or
deleting it helps.

## Settings a harness saves itself

Some harness commands save a setting into a file loadout writes. Claude Code's `/model`, for
example, writes `model` into `~/.claude/settings.json`. Loadout owns that file, so the change is
drift like any other edit: `check` reports it and `sync` refuses to overwrite it.

Which keys count depends on the file. Claude Code's and Droid's `settings.json`, OpenCode's
`opencode.json` and Pi's `settings.json` are written whole, so any key the harness adds or changes
there is `hand-edited`, whether the source sets it or not. Codex's `config.toml` is shared: loadout
compares only the keys the source sets, reports a change to one as `owned-key`, and leaves every
other key to Codex.

There are two answers:

1. **Keep the setting in the source.** Port the new value into the fragment that sets it, such as
   the settings or Codex `defaults` fragment, or ask the `/loadout` skill to, then run
   `loadout sync`. Every machine gets the value, and the next change made in the harness is drift
   again.
2. **Hand the key to the harness.** Remove it from the source, if it is there, and name it in the
   agent block's `preserve`. Sync then copies the key from the file on disk into the file it
   writes, and `check` no longer reports changes to it:

   ```toml
   [claude]
   settings = "claude"
   preserve = ["model", "modelSettings"]
   ```

   `preserve` takes top-level keys only, so preserving `modelSettings` hands Claude Code the whole
   key, every model's entry in it. A key the source still sets, or one a slice writes, such as
   `permissions`, or `enabledPlugins` when `[claude]` declares `plugins`, is refused with
   `preserve names generated key(s)`. A preserved key is copied from this machine's file, so a
   fresh machine starts without it until the harness writes it there.

   Codex needs no `preserve`, and `[codex] preserve` is a configuration error. Remove the key
   from the `defaults` fragment: the next `sync` deletes it from `config.toml`, and from then on
   it is Codex's. While Codex's value differs from the one loadout wrote, that sync refuses;
   `sync --force` deletes the key anyway.

What each harness writes, by the version checked:

| Harness | Action | Key | File | `check` reports |
| --- | --- | --- | --- | --- |
| Claude Code 2.1.312 | `/model` | `model` | `~/.claude/settings.json` | `hand-edited` |
| | `/effort`, or the effort in `/model` | `modelSettings` (`<model>.effortLevel`) | same | `hand-edited` |
| | `/plugin` install, enable or disable at user scope | `enabledPlugins` | same | `hand-edited` |
| | `/plugin marketplace add` | `extraKnownMarketplaces` | same | `hand-edited` |
| | `/theme`, `/config` | `theme` and the other keys the panel sets | same | `hand-edited` |
| | Making auto mode the default | `permissions` (`defaultMode`) | same | `hand-edited`; `permissions` cannot be preserved, so set `defaultMode` under `permissions` in the settings fragment |
| | Starting after an upgrade that retires a model name | `model` | same | `hand-edited` |
| | `claude mcp add --scope user` | `mcpServers` | `~/.claude.json` | `owned-key` |
| Codex 0.165.0 | `/model` | `model`, `model_reasoning_effort` | `~/.codex/config.toml` | `owned-key` when `defaults` sets them, `owned-key-removed` when Codex deletes one; nothing otherwise |
| | Approving an MCP tool for good | `mcp_servers.<server>.tools.<tool>.approval_mode` | same | `owned-key` for a server the source defines |
| | Installing, enabling or disabling a plugin | `plugins."<name>@<marketplace>"` | same | `owned-key` when `[codex]` declares `plugins` |
| | Service tier, theme, status line, trusting a project | `service_tier`, `tui`, `projects` | same | nothing, unless `defaults` sets them |
| Droid 0.251.0 | `/model`, and the reasoning-effort and autonomy pickers | `sessionDefaultSettings` | `~/.factory/settings.json` | `hand-edited` |
| | `/settings` | `theme` and the other keys it sets | same | `hand-edited` |
| | Installing or enabling a plugin, adding a marketplace | `enabledPlugins`, `extraKnownMarketplaces` | same | `hand-edited` |
| | Trusting a folder | `trustedFolders` | same | nothing; loadout preserves it |
| OpenCode 2.2.0 | Picking a model in the TUI | recent and favourite models | `~/.local/state/opencode/model.json` | nothing; loadout does not write it |
| | TUI settings | | `~/.config/opencode/cli.json` | nothing; loadout does not write it |
| | The desktop app's shell setting | `shell` | `~/.config/opencode/opencode.json` | `hand-edited` |
| | `opencode plugin add`, `opencode mcp add --global` | `plugins`, `mcp` | same | `hand-edited` |
| Pi 1.4.0 | `/model`, then Ctrl+S | `defaultProvider`, `defaultModel` | `~/.pi/agent/settings.json` | `hand-edited` |
| | `/thinking`, then Ctrl+S | `defaultThinkingLevel` | same | `hand-edited` |
| | `/settings`, `/scoped-models` | `theme`, `modelThinkingLevels`, `enabledModels` and others | same | `hand-edited` |
| | `pi install`, `pi config` | `packages` | same | `hand-edited`; `packages` belongs to the `plugins` slice and cannot be preserved |
| | Starting after an upgrade | `lastChangelogVersion` | same | nothing; loadout preserves it |
| | pi-permission-system's `/permission-system` | `yoloMode`, `debugLog`, `permissionReviewLog` | `~/.pi/agent/extensions/pi-permission-system/config.json` | `hand-edited`; the source has no place for these keys, so preserve them |

Loadout writes Pi's `settings.json` only when `[pi]` declares `plugins`; without it, none of the
Pi rows on that file is drift.

At project scope, Claude Code saves a "don't ask again" permission to `.claude/settings.local.json`,
which loadout does not write; record it in `permissions.local.toml` to keep it with the source. A
plugin enabled at project scope goes into `.claude/settings.json` or `.factory/settings.json`.
Claude Code's `enabledPlugins` there stays as the harness wrote it until the project has a
`loadout/settings/claude.json`; from then on that file holds every key no slice writes, and a key
the harness adds is `hand-edited`. Droid's project `enabledPlugins` belongs to the `plugins` slice,
so a plugin enabled there is `hand-edited`: declare it in `loadout/plugins.json`.

## Sync or check refuses a global setup

A folder holding a `loadout.toml` renders only if it is this machine's registered global source.
Anywhere else, `sync` and `check` exit 3 before reading it, and say why: this machine has no
registered source, so run `loadout init global <folder>` to register this one; or another source
is registered, so add `--registration replace` to register this one instead. A machine config
that does not load is reported as its own error: fix the key it names, or, when it cannot be
read, the file's or its folder's permissions.

## Sync refuses a skill or agent name

```text
loadout: Claude Code writes /home/you/.claude/skills/synced itself; rename the skill 'synced' so the two do not overwrite each other
```

A few names belong to a harness, which writes them into a folder Loadout writes too: Claude Code's
`synced` skill, and Droid's `scrutiny-feature-reviewer` and `user-testing-flow-validator` droids.
A global skill or agent with one of those names would overwrite the harness's file, or be
overwritten by it, so `sync` and `check` exit 3 before writing anything. Rename the skill or agent
in the source and sync again.

## Sync notes a repeated heading

```text
note: claude.instructions: the instruction document repeats the heading "## Testing", from instructions/workflow.md and instructions/claude.md; keep one section, with /loadout
```

Two fragments selected into one instruction document each bring the same Markdown heading, so
the agent reads that section twice. Merge the two sections into one fragment, or ask the
`/loadout` skill to. The note is advisory: sync still writes, and `check`'s exit code is
unaffected. See [composition](guides/composition.md#combine-sources).

## A skill named README, or a skill that stopped updating

A skill is `skills/<name>/SKILL.md`. Older versions also read every `skills/*.md` file as a skill,
so a `skills/README.md` rendered as a skill named `README`. Loadout no longer renders those files,
and the next `sync` retires the folders it wrote for them, such as `~/.claude/skills/README/`,
unless you edited them. A skill you kept as `skills/<name>.md` must move to
`skills/<name>/SKILL.md`; `sync` and `check` name any loose file they skip.

## No global configuration

Run:

```sh
mkdir -p ~/agent-config
loadout init global ~/agent-config
```

`init global` needs the directory to exist; it refuses a missing one with `selected directory
must exist`. Then configure the created source as described in [global setup](guides/global.md).
A project-only setup does not require global configuration.

If a machine configuration already exists, inspect its `source` path first.
`loadout init global --registration replace` is for deliberately replacing that machine selection.

## No targets declared

A manifest must declare at least one `[<agent>]` block. `init global` writes one for each harness
it adopted; if they were removed, add one back as in the
[agent-block example](guides/global.md#choose-your-agents).

## A fragment cannot be found

Check the name against the selected source's directory:

- `instructions = ["workflow"]` needs `instructions/workflow.md`.
- `settings = "claude"` needs `settings/claude.json`.
- A relative source path is resolved from the manifest directory; absolute and `~/` paths are
  used as they are.
- A source's `use` list may exclude the requested slice.

If several sources offer the same name, loadout does not pick one: it refuses the unqualified
name and lists the candidates it found. Qualify it with the source name, such as
`migrated/workflow` for the source `init global` wrote, to choose. A command for
inspecting resolution directly is not available in 1.0.

## A template cannot be found

Loadout first checks the project's vendored copy in `loadout/templates/`, then the `templates/`
folder of each source declared by the machine's global manifest, where a template is either a
`<name>/` directory or a `<name>.toml` catalog manifest, then the bundled starters (`frontend` and
`backend`), which ship with loadout and also resolve on a machine with no global configuration.

Run `loadout template list` and inspect those sources. Clone or update an external source using
your normal git workflow. Loadout does not fetch it automatically.

If two sources offer the same name, use a qualified name such as `company/python`.

## A vendored template will not update

`loadout template sync NAME` refuses to overwrite a copy that has changed since it was
vendored, or whose original hash cannot be verified. It prints a diff for you to reconcile.

Keep project-specific changes in project fragments rather than editing the shared template
copy where possible. See [template updates](guides/templates.md#update-a-vendored-template).

A modified vendored source is not itself generated-file drift. `check` reports it without
failing solely for that reason.

## A removed skill or file is still present

When the source stops producing a file Loadout wrote, such as a removed skill or a disabled
output, `check` reports it as `orphaned` and the next `sync` deletes it, printing
`retired <path>`. A file you edited since Loadout wrote it is refused instead, as described under
[check reports drift](#check-reports-drift). A file Loadout never wrote is left alone; in a folder
the harness reads whole, such as a skills folder, `check` reports it as `unmanaged` so you can
port it into the source and delete it.

The bundled `loadout integrate skill uninstall` command has its own ownership-aware cleanup for
the skill it installed.

## An agent misses part of the configuration

Check the [support table](reference/harnesses.md). Some slices are global-only, and some depend
on a harness extension or adapter.

For OpenCode skills, check
[`OPENCODE_DISABLE_CLAUDE_CODE_SKILLS`](reference/harnesses.md#opencode-skills).
After installing new skills or changing agent startup configuration, restart the agent session.

## Init could not convert a setup

When init fails part way it puts every file it changed back and exits 1. Your setup is as it was
before, and init kept no backup. When it cannot plan the conversion, it exits 2 without changing
anything. Either way it recommends the bundled `/loadout` skill, which can do the conversion with
you. At a terminal it offers to copy the skill into your current setup. Without one, it prints the
command instead, for example `loadout integrate skill copy --harness claude`. Then start the skill
in that harness and ask it to set up loadout. A different `loadout` skill already in one of those
folders is refused rather than overwritten; remove it, or use the copy that is there.

## An earlier init was stopped part way

An init killed before it finished leaves its changes in place and a backup in
`$XDG_STATE_HOME/loadout/backups/`, and every later init stops there until it is put back. Run
`loadout init project` or `loadout init global` at a terminal and answer yes: it puts back every
path that init changed, losing any edit made to those paths since, and then exits. Run init again
to adopt. Without a terminal, init exits 2 and says so. A backup from another version of loadout
cannot be put back; init names its directory, which you delete.

## Unexpected internal error

Exit code `4` includes a traceback. Report it in the
[issue tracker](https://github.com/nielsmadan/loadout/issues) with the command, Loadout revision,
and a minimal configuration that reproduces it. Remove secrets and private source content from
the report.
