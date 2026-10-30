# What sync protects, retires and reports

`sync` writes generated files; `check` asks the same questions without writing. This page is how
both decide what a file on disk is. The drift kinds `check` reports, and the advice for each, are
listed in the [CLI reference](../user/reference/cli.md#render-and-check).

## Files modified outside loadout

Generated files look like ordinary files, so they get hand-edited, and the edit is then lost on
the next `sync`. Before writing, `sync` compares each file against every output loadout itself
could have produced: rendered from the committed source and from the working tree, under every
declared profile. A file matching none of them was written by something else, so `sync` names it
with the kind `check` would report, changes nothing, and exits 1. It prints the same diff `check`
does alongside the name: the `-` lines are the ones that exist only on disk, and for a key removed
outside loadout the `+` lines are what sync would write back, so a permission a harness granted
itself at runtime is reported verbatim rather than silently discarded. When the terminal shortens
that diff, the whole of it goes to `$XDG_STATE_HOME/loadout/sync-abort.diff` (mode 0600), which the
next such abort replaces and the next sync that completes removes; when that directory cannot be
written, the abort says so by leaving the line out and still exits 1. A file holding a value the
credential check recognises is never diffed, there, in `check`, in `template sync` or in the init
preview: one line names it and counts the lines that differ, so read the file directly
([0032](../decisions/0032-a-file-holding-a-secret-is-never-diffed.md)). Port the edit into the
source with the bundled `/loadout` skill, or pass `--force` to discard it.

Comparison is by parsed document for JSON targets, not by bytes, so a harness re-serialising its
own config in a different key order does not read as an edit. In a file loadout shares with its
harness, such as `~/.claude.json` or `~/.codex/config.toml`, only the keys loadout writes are
compared, each on its own: the harness may change the rest, while an edit to one of loadout's keys,
such as a server added to `mcpServers` with `claude mcp add`, stops sync and is reported by `check`
as `owned-key`, and deleting one, such as `codex mcp remove` of the last server, as
`owned-key-removed`. A key the source manages for the first time, beside keys sync has already
recorded writing there, is a source change: sync overwrites it and says so, as
`now managed: model in ~/.codex/config.toml (was "gpt-5")`. With no record of the file at all, on a
first sync or with the record deleted, a value one of loadout's keys already holds is overwritten
only when this render or the committed source accounts for it; anything else is refused as
`unknown`, while a key the file does not hold yet is simply added. A key listed under `$remove` is
never an edit, since the harness may write it back, not even when the file stops being rendered and
the key is stripped with the rest, and moving it from `$remove` to a value opts it in like any
newly managed key. When a slice stops rendering into a shared file, each of its keys still there is
judged the same way before sync strips it, so a server added by hand stops the retirement as
`owned-key` instead of going with it.

There is a third comparison beside those two renders: what `sync` recorded writing there last,
kept per root under `$XDG_CONFIG_HOME/loadout/written/`. Without it, editing a source twice before
committing would leave an output rendered from a state that is neither baseline, and `sync` would
refuse a file it had written itself. For a file loadout writes whole the record only ever widens
acceptance: delete it and the guard falls back to the two renders, warning and all. For a file it
shares with a harness, deleting it makes the guard stricter, as described above. `sync` deletes the
record of any root that no longer exists; a root it cannot examine, such as one a sandbox hides,
keeps its record. `check` never changes machine state.

The guard needs neither git nor git on PATH; `check --staged`, which reads the index, exits 3 and
names git when it cannot run it. Outside a repository, or before the first commit, the working
source and the record are the baseline: an unsynced source edit leaves the output matching what was
recorded, while a hand edit does not. On the very first `sync`, with neither a commit nor a record,
a file that does not match the working source is refused until you pass `--force`.

Two cases warn without anything being wrong. The first `sync` over config that predates loadout:
adoption is the one time loadout overwrites a file it has never written. And a destination shared
between machines through a symlinked config repo: one machine's record cannot vouch for the
other's write.

`check` asks the same question and says which answer applies, rather than sending you to `sync`
where `sync` is about to refuse.

## Outputs the source no longer produces

Remove a skill, a harness or the last MCP server from the source, and `sync` retires what it wrote
for them before: it deletes each file, takes a skill's folder with it, and drops the file's
`.gitignore` entry, printing `retired <path>`. In a file loadout shares with its harness, such as
`~/.codex/config.toml`, it removes only the keys it recorded writing and leaves the rest.

Only provably loadout's own, unchanged output is retired. The record above is the proof: a file it
does not list is never touched, however generated it looks. A file edited since loadout wrote it is
refused like any other hand edit, and `--force` retires it anyway. A skill folder holding a file
loadout never wrote keeps that file and the folder. `check` reports each pending retirement as
`orphaned`.

## Files loadout did not write, in folders it manages

Some folders are read whole: Codex reads every file in `~/.codex/rules/`, and every harness reads
every folder in its skills directory. A file there that loadout did not write takes effect beside
the source; Codex writing a new approval to `~/.codex/rules/default.rules` is the common case.
`check` reports it as `unmanaged` and exits 1; `sync` names it in a warning and carries on, since it
overwrites nothing there. Port it into the source with the bundled `/loadout` skill, then delete
it. A folder of files is reported once, as the folder. A link there is reported as
`unmanaged-link` with where it points, and never read: check the target, then delete the link. A
project folder reached through a link, such as `.claude/agents -> ../.git`, is reported the same
way and never scanned.

Not reported:

- a file another loadout source wrote;
- a dot entry such as `.DS_Store`, or Codex's own `.system` skills;
- what a harness writes there itself: Claude Code's `skills/synced`, its copy of your claude.ai
  account's skills, and Droid's two mission droids in `~/.factory/droids/`. A source skill or agent
  that would render onto one of those is refused with exit 3; rename it;
- an entry no harness reading the folder would load, such as a `README.md` beside the skills in a
  Claude, Codex or Droid skills folder (not Claude's when OpenCode is selected, since OpenCode reads
  that folder too and loads it), a folder holding no `SKILL.md`, or a Claude agent file without
  frontmatter. Init leaves those in place too.

The rule per harness is in
[config.md](config.md#what-a-harness-loads-from-a-skills-or-agents-folder).

## Permission files with no rules

A file that holds nothing but permission rules is written only when it has one, in both scopes:
Codex's `permissions.rules` needs a shell rule Codex can express, and Claude's
`mcp-permissions.json` needs an `[mcp]` rule. Once its last rule is gone, the next sync removes a
file loadout wrote, unless it was edited since. Pi's global permission file is always written,
since even empty it makes `bash` ask; its project file is written only with a rule or a stated
`[shell] default`. Keys inside shared files such as `settings.json` and `opencode.json` are always
written, empty when there are no rules. Sync does not create `~/.codex/config.toml` just to leave
it empty.
