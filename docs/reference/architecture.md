# Architecture

`cli.py` → `commands.py` → `emit.py` → `composition.py` (instructions) and
`permissions/` (`rules.py` parses, `renderers.py` renders, keyed by name in `RENDERERS`).
`manifest.py` parses `loadout.toml`; `sources.py` and `resolve.py` resolve fragments.
`resolve.py:_slice_root` caches resolved source roots by `Source` value. This is safe for the
one-process-per-command CLI, but a long-lived embedding must clear or bypass that cache after
replacing a source directory or symlink; otherwise a stale root can surface as a misleading
escape error.
`extract.py` runs `permissions/renderers.py` backwards — one extractor per `RENDERERS` key. Init
unions the extracted harnesses' permissions through `permissions/merge.py`.
The native slices render from their own fragments rather than from rules and live beside it:
`hooks.py` (with `adapters.py`) and `plugins.py`, both registered in `RENDERERS` like everything
else, so `emit.py` composes them into a shared file without knowing what they are.
`module_config.py` is the exception that proves the shape: it has no renderer and no `RENDERERS`
entry, because a module's own config is copied byte-for-byte to an authored relative path — the
harness fixes the directory and each module picks its filename, so there is nothing to render
and nothing to derive. See [module-config](module-config.md).
`surgery.py` writes into a destination loadout does *not* own — `~/.codex/config.toml` — by
stripping the keys a slice declares and leaving every other byte alone. It never parses and
reserialises: comments, another tool's managed block and a multi-line string are not in the
parsed model, so the file is safe because nothing rewrites those bytes, not because a writer was
careful. `record.py` holds the other half for a slice whose key names are the user's rather than
a set loadout could enumerate: the union of what was written last time and what is written now
is what gets stripped, which is the only reason *removing* a key removes it. See
[0017](../decisions/0017-ownership-may-be-declared-instead-of-derived.md).
`machine.py` resolves `$XDG_CONFIG_HOME/loadout/` (`config_dir`) — where machine state is
*stored*: the config `--global` reads, what `--global` resolves the root and profile from, and the
harness defaults a fresh project init uses. `state_dir`, `$XDG_STATE_HOME/loadout/`, holds what
loadout keeps for itself: init's backups and sync's complete diff. It is not the only machine state that is *read*:
`destinations.py:resolve_destination` expands `${VAR}` in a destination against the environment, per
[0011](../decisions/0011-a-destination-follows-a-relocated-harness.md).
A render takes an `emit.RenderContext`: the destination resolver, the resolver for a template
that is not vendored, and the manifest source parser. The staged-render and migration-validation
workers pass their own instead of patching modules. What renders again on a render's behalf takes
the same context: the guard's profile and committed variants (`sync.unaccepted_outputs`),
`sync.classified_drift`, and `notices.collect_notices`, which also takes the environment it reads.
`written.py` stores the other half beside that config: what `sync` last wrote at each
destination, which is the third variant the modified-outside-loadout guard compares against and
the sidecar [0008](../decisions/0008-generated-files-carry-no-machine-state.md) deferred for
orphan removal — see [0019](../decisions/0019-sync-records-what-it-wrote.md). It only widens
acceptance: no record means the guard behaves exactly as it did before one existed. For a
document loadout shares with its harness, the entry hashes each key loadout wrote on its own, and
the guard compares only those; a key no entry covers yet is newly managed, not an edit.
`retirement.py` consumes it: a recorded destination the run no longer renders is deleted, or has
its recorded keys stripped, once `sync.unaccepted_outputs` (the guard's classifier) accepts
it — see [0022](../decisions/0022-sync-retires-what-it-no-longer-renders.md). With no record,
nothing is retired. Do not
confuse it with `record.py`, which records owned key *names* beside a fragment in the source
repo and is committed.
`project.py` (harness preset) is project scope. `init` for both scopes is `init_workflow.py`,
which runs `discovery.py`'s inventory through `migration*.py`'s plan, backup and transaction;
`credentials.py` decides which values in what it adopts look like secrets, for the warning.
`gitignore.py` holds the one escaping rule and the line edits both init and sync use.
`commands.py` is the CLI's commands; what `sync` decides and does (the guard, writing, retiring)
is `sync.py`, and returns values for the command to report. `files.py` writes outputs and
compares them with disk, through any link at the destination. `notices.py` assembles the
advisory notices a render reports.

Two comparators answer different questions. `files.check_all` is raw: an output differs when its
bytes do, which is what `check` reports as drift. `sync.compare_with_disk` normalises, the way the
guard does: a banner or a JSON re-serialisation alone is `normalised`, not `update`, which is what
init's preview and its first render are judged by. A copied file is judged by one rule in both,
`files.copy_matches`: its bytes and its mode.
`templates.py` resolves a template *name* — the project's vendored copy first, then the
`templates/` directory of every source the machine's global manifest declares — and owns the tree
hash `template sync` compares a vendored copy against. A template is a source, so it merges
through the slice's own operator and adds no merge rule.
`emit.py:render_project` renders it; `render_all` unions it with `render_global`'s global-scope
output, so `sync`/`check` regenerate whichever scopes are present.

## Module map

| area | modules |
|---|---|
| CLI | `cli.py` (arguments, exit codes, `--json` errors), `commands.py` (the commands), `diffs.py` (the diffs they show), `completions.py` |
| render | `emit.py`, `composition.py`, `permissions/` (`rules.py`, `merge.py`, `renderers.py`), `hooks.py`, `adapters.py`, `plugins.py`, `servers.py`, `skills.py`, `subagents.py`, `module_config.py`, `output.py` (the output types) |
| sources | `harnesses.py` (the global preset), `manifest.py`, `sources.py`, `resolve.py`, `destinations.py`, `machine.py`, `project.py`, `skill_policy.py` |
| sync | `sync.py` (guard, deploy, retirement, drift), `files.py` (writers, `check_all`), `retirement.py`, `written.py` (the write record), `record.py` (owned key names), `surgery.py` and `toml_paths.py` (writing into shared documents), `notices.py`, `unmanaged.py`, `gitignore.py` |
| init | `init_workflow.py` (preview, apply, restore), `discovery.py` (inventory), `credentials.py` (secret warnings), `migration.py` (plan), `migration_models.py`, `migration_paths.py`, `migration_transaction.py` (apply), `migration_validation.py` (isolated reconstruction), `migration_images.py` (what a path holds, captured and installed), `migration_backup.py` (backup and restore), `migration_git.py`, `git_privacy.py`, `native_documents.py`, `extract.py` (reading native documents back) |
| git | `staged.py` and `staged_render.py` (`check --staged`), `git_hooks.py` and `git_hook_commands.py` |
| templates | `templates.py` (resolution, vendored copies), `template_catalog.py`, `template_discover.py`, `template_vendor.py`, `bundled_templates.py` |
| bundled skill | `skill_installation.py`, `bundled_skill.py` |

Harness facts live in the presets: `harnesses.py:GLOBAL_PRESET` and `project.py:PROJECT_PRESET`
say where each slice of each harness goes, `KNOWN_HARNESSES` is the global preset's keys, and
`discovery.py` (roots, runtime paths) and `migration.py` (dormant routes) restate what adoption
needs. `tests/test_harness_tables.py` holds every restatement to the presets.
