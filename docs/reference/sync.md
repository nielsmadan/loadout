# What sync protects, retires and reports

`sync` writes generated files; `check` asks the same questions without writing. This page is how
both decide what a file on disk is. The drift kinds `check` reports, and the advice for each, are
listed in the [CLI reference](../user/reference/cli.md#render-and-check).

## An interrupted sync

Sync is not transactional: it writes the outputs one by one, records them, then retires orphans,
and an interrupt stops it wherever it is. Each step leaves a state the next sync finishes:

- **Writing.** Each file is replaced atomically, and Ctrl-C waits until a temporary file's name
  is held, so its cleanup always runs. The record is written only after every output, so it still
  describes the previous sync; a file written since equals what the source renders, which the
  guard accepts.
- **Recording.** The record is replaced atomically: the previous one or the new one.
- **Retiring.** The record still names every orphan until retirement ends. An orphan already
  deleted is skipped, and the skill folder it emptied is pruned then.

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

## Overlays

An [overlay](../user/guides/overlays.md) renders into repositories that have not adopted loadout
([0045](../decisions/0045-an-overlay-source-renders-into-repositories-that-have-not-adopted-loadout.md)).
It shares the guard, the drift kinds and the diffs with everything above, and keeps its own
record, claims and ignore lines. Code: `overlay_*.py`, `claude_json.py`, `exclude.py`,
`json_paths.py`.

### The overlay record

One record per overlay folder, at `$XDG_CONFIG_HOME/loadout/overlay-written/<key>.json`, where
`<key>` is the first 16 hex digits of the SHA-256 of the folder's resolved path. It is not stored
in `written/`, so `prune_vanished` never sees it and a deleted target does not take its record
with it. It holds, as JSON (`version` 2):

- the `HOME` the targets were matched under;
- the overlay-folder files it wrote (OpenCode's instructions file);
- per target: the git common directory, the device (`st_dev`) of the root, the name of its
  `info/exclude` block, each whole file it wrote with its digest, each shared document with
  the digests of its owned keys and the entries of its owned rules, with the keys and rules still
  pending, and the `~/.claude.json` leaves, per file, with their digests and the digests still
  pending.

The decoder rejects an unknown key, so a new field means a `version` bump. A record with a newer
`version` is `newer`: every command that needs it refuses (exit 3, `was written by a newer
loadout; upgrade loadout`), `--force` included. An unreadable record is refused with exit 3 until
`--force` treats it as empty. Another overlay's unreadable or newer record blocks `sync` and
`overlay add` for the others, because their claims cannot be checked; the message names the file
and offers `loadout overlay remove NAME --force`. `overlay remove` of a different overlay still
works. A record is decoded again before it replaces the file, written with `fsync`, and not
replaced if it would not read back as written. A record whose stored folder no longer hashes to
its file name (the folder was moved or renamed) is still found, by the stored folder or by the file
name, and is written under the new name by the next write. The answer to the global-ignore offer
is not in `overlay-written/`, where every `*.json` is read as a record: it is
`$XDG_CONFIG_HOME/loadout/overlay-global-ignore.json`. Nothing in a generated file names the record
([0008](../decisions/0008-generated-files-carry-no-machine-state.md)).

Two exceptions to 0008 are bounded: `CLAUDE.local.md` may start with `@AGENTS.md`, and OpenCode's
`opencode.json` names a file in the overlay folder by absolute path
([0049](../decisions/0049-the-conditional-agents-md-line-and-opencode-instructions-by-absolute-path.md)).

### The guard and claims

A whole file is judged as above: against the record, and against the render. A file with no
record at an overlay destination is `unknown`, refused until `--force`. A shared document is judged
key by key and rule by rule ([configuration](../user/reference/configuration.md#owned-documents)):
`owned-key` and `owned-key-removed` are guard findings (exit 1, `--force` overwrites); a key the
overlay would write that holds a different value it did not write is `user-defined`, refused with
exit 3 and not overridden by `--force`. Diffs go through `diffs.diff_lines`, so a file holding
a secret is withheld ([0032](../decisions/0032-a-file-holding-a-secret-is-never-diffed.md)). A
guard finding on any output holds back the whole target: nothing is written to it, `check` still
reports its drift and unmanaged files, and the `~/.claude.json` servers are not compared.

Each path has one owner. A path that another overlay's record, or a project or the global source
in `written/`, holds is refused as `claimed`; a skill folder is claimed whole, so an overlay
never writes a file into a folder another owner records anything in. A path the team tracks is
refused as `tracked`: a unit is a file, or a skill folder, and a unit is tracked when git lists
it or anything below it. The check runs again for each target just before it writes and deletes,
and **no flag overrides it**, `--force` and `overlay remove --force` included. The git facts are
asked for the same units the plan collapses paths to (`overlay_plan.plan_units`), and a path also
counts as tracked when its own path below the root is listed, so a different spelling of a unit
cannot skip the check. Tracked names are compared case-insensitively, as a case-insensitive disk
reads them. A recorded output
that the team starts tracking is never retired: whenever the overlay would retire it (under
`overlay remove`, for a target no longer matched, or for an output the source stopped rendering),
it is left in place, dropped from the record and noted with exit 0, so the record lets the
repository go. A unit that a committed `.gitignore` re-includes (`!/CLAUDE.local.md`) is refused
as `unignored` while planning, so `check` reports what `sync` refuses.

### Scoped retirement

A run retires only what its scope covers. Syncing from inside a target handles that target alone.
A full run (`--overlay`, the overlay folder, `--global`) also retires the recorded targets its
patterns no longer match. A recorded target that is errored, refused, skipped or unreachable is
left wholly untouched: its files, documents, `~/.claude.json` leaves, record entry and exclude
lines. Only a target that is positively gone, or healthy and no longer matched, is retired. A
recorded target that no longer matches and cannot be examined is reported as skipped (exit 0).

- **Gone is decided by device.** A target is gone when `lstat` of its root raises
  `FileNotFoundError` and the nearest existing ancestor has the `st_dev` the record holds for the
  root. A different device, or any other error, makes it unreachable: an unmounted volume, an empty
  mount point or an unreadable folder is never read as removal. A deleted parent tree is still gone.
- **A pattern whose fixed parent cannot be listed** makes its recorded targets unreachable, with a
  note.
- **HOME must be the same.** When the record's `HOME` differs from the run's, nothing is retired
  (exit 3, naming the `HOME` to run with). When the patterns match no folder at all while the
  record names targets, nothing is retired unless `--force`.
- **A git failure is not absence.** `git` reporting that a folder with a `.git` entry is not a
  repository (dubious ownership, an unreadable git directory) is `git-failed`, never `not a
  checkout`. A folder with no `.git` entry is a note.
- **The `~/.claude.json` leaves outlive their target.** They are stripped when the target is
  gone, because the record holds them under the config file's path as Claude derives it. After a
  `CLAUDE_CONFIG_DIR` change the old file's leaves are retired too, but only under the record's
  `HOME`: a run under another `HOME` keeps them. A recorded path that resolves, or is the same
  file, as the current one is the current one, and its leaves are re-recorded under that spelling.
- **`overlay remove` retires everything** from the record alone, and the overlay-folder file only
  after every recorded target was retired or dropped.
- **A target that has adopted loadout is retired.** A recorded target that now holds
  `loadout/config.toml` is retired like one the patterns stopped matching, in a scoped run too,
  with the note `adopted loadout; the overlay retired its files here`; an unrecorded one is
  skipped. In a run that also renders the project, the overlay part runs first, so the project
  never adopts the overlay's keys as foreign ones
  ([0052](../decisions/0052-a-target-that-adopts-loadout-is-retired-by-the-overlay.md)).

### Order and crashes

For each target, in this order:

1. The record is saved with the entries about to be written, so a crash leaves a record that
   claims too much, never too little. A whole file keeps its old entry until the final record.
   A shared document keeps its old keys and rules, and the keys and rules about to be written
   are pending, as are the leaves, with digests. The overlay-folder file is recorded as soon as
   it is written, before any target.
2. The overlay's block in `info/exclude` is written with every path it is about to write.
3. Sync asks git (`check-ignore --no-index`) about each unit it will write. A unit that would
   still show is dropped with a refusal (exit 3).
4. Files are written, then deleted; shared documents are replaced only if their bytes are those
   that were judged, and a whole file only if it still passes the guard (unless `--force`); a
   mismatch is re-planned.
5. The block is shortened, after the deletes, and the final record is saved.

So an interrupted run can leave a path ignored that does not exist, never one that exists and is
shown, and the next run finishes it: a file equal to its render is accepted, a pending leaf, key
or rule equal to its value is adopted, a pending one that is absent is created rather than judged
removed, and an orphan already deleted is skipped. A pending leaf or key that holds any other
value was never written by loadout: one still rendered is refused as the user's, and one being
retired is left as it is, even with `--force`, and dropped from the record. Leaves and
shared-document keys are judged by one set of rules (`json_paths.wanted_verdict` and
`dropped_verdict`).

The global ignore block is recomputed from every overlay record after each run, and left as it
is while any record cannot be read or is newer, since that record's paths would otherwise drop
out of it.

### Locks

- One `flock` per overlay folder for the whole run, in
  `$XDG_STATE_HOME/loadout/overlay-locks/overlay-<key>.lock`.
- One `flock` per file edited in a read-modify-write: each `info/exclude`, the global ignore file,
  `overlays.toml` and `~/.claude.json`, keyed by the file's resolved path, with the PID written in.
  Git reads `info/exclude` and takes no lock on it (it writes the file only when a repository is
  created from its template), so there is no git lock to follow
  ([0047](../decisions/0047-writing-info-exclude-inside-git-despite-containment-protected.md)).
- For `~/.claude.json`, also Claude Code's own: the directory `<path>.lock`, made with `mkdir`
  at the path Claude derives, without resolving links
  ([0048](../decisions/0048-nested-json-ownership-claude-json-leaves-and-owned-keys-in-shared-documents.md)).
  The bytes are compared again just before the rename, inside both locks.

Nesting runs one way: the overlay-folder lock, then a file's lock, then Claude's directory.
`flock` is not reentrant across descriptors, so a function that takes a file's lock itself is
called without an outer one: `exclude.write_global_block` takes the global ignore file's lock and
computes the personal units inside it from a callback that is given the file's current user lines,
so that read-modify-write is one. `claude_json_locks` is keyed by the file's resolved path for
`flock` and by the unresolved path for Claude's directory. It removes that directory only if its
inode is still the one it made, since Claude takes over a lock older than ten seconds.

A wait ends after 30 seconds (15 for Claude's lock) with an error naming the holder where known.
Neither lock is ever broken, and no message tells the user to delete one.

### Known limitations

- **An owned rule edited on disk reads as one removed plus a saved approval.** Rules are compared
  as strings, so an edited string cannot be told from an approval Claude or Droid saved. `sync
  --force` writes the original back and carries the edited text; delete that line by hand. The
  fix would need ownership by position, which a list does not give.
- **Pi's `/mcp` "Disable in this project" writes into the overlay's `.pi/mcp.json`**
  ([pi.md](pi.md#server-definitions)), which the overlay owns whole, so it shows as `hand-edited`.
  `check` says so; make the change in the overlay source.
- **The Droid user-level merge is not established.** The overlay's `.factory/mcp.json` is written
  whole, and a server name that `~/.factory/mcp.json` already defines is refused. Not
  established: no probe of how Droid merges a project `mcp.json` with the user's was run, and the
  earlier probe round (Droid 0.251.0) covered `settings.json` and `settings.local.json` only.
  Droid 1.1.0 ships no docs folder next to its binary. Its bundled code, read with `strings`,
  loads each folder's `mcp.json` through `loadMcp` and merges two layers' `mcpServers` by server
  name, the later layer winning. Which layer a project's `.factory/mcp.json` is, and whether the
  user's file is another, was not read, and nothing was run.
- **The crash edge of one digest per whole file.** A whole file, and the overlay-folder file, keep
  one digest. A crash after such a file is written and before its record is saved, followed by an
  edit of the source before the next run, reads as an edit outside loadout (exit 1; `--force`
  clears it). Shared documents and leaves carry pending digests, so they recover.
- **Floats in `~/.claude.json`.** Parts of the file the overlay does not own are rewritten with
  Python's number spelling, so Node's `0.0000123` can come back as `1.23e-05`. The value is the
  same. The global scope has the same limit.
- **A key restored with `--force` moves to the end of its object.** When an owned key such as
  `model` was deleted from `.claude/settings.local.json` (or another shared document) and
  `sync --force` writes it back, it goes after the keys the user kept, not at its old position.
  The record stores which keys loadout owns, not where they sat, and the values are the same.
- **Adding and then removing the `info/exclude` block restores the file byte for byte only when
  the file ends with exactly one newline.** More trailing blank lines lose one, and a file with
  no final newline gains one.
- **Retirement leaves empty folders behind.** Removing an overlay, or retiring its outputs,
  deletes the files it wrote but not a folder such as `.codex/`, `.factory/` or `.pi/` that the
  first write created. The record does not say which folders loadout created, and an empty folder
  shows nothing in `git status`.
- **A branch switch can replace an overlay file.** If a local branch tracks one of its paths,
  checking it out replaces the overlay's copy and drops edits made to it; `check` names the
  branch. The next `sync` writes the copy again, unless the team now tracks the path, which it
  refuses.
