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
that is not vendored, the manifest source parser, and `registration`, the one root that may render
global scope, read once with why there is none, so a refusal names what decided it. `render_global` refuses any other root before it reads the manifest
(`emit.refuse_unregistered_global`, [0031](../decisions/0031-global-scope-renders-only-from-the-registered-source.md));
by default that root is the source the machine config registers, and init's scratch preview, its
isolated validation, the staged snapshot and the guard's committed tree name their own copy. The staged-render and migration-validation
workers pass their own instead of patching modules. The guard (`sync.unaccepted_outputs`),
`sync.classified_drift` and `notices.collect_notices` render with the default context, since
nothing renders them for another root; the guard hands its committed tree a copy that may render
it. That tree holds what `git_tree.render_inputs` names at HEAD, the same set `check --staged`
writes out, never the whole repository, and `collect_notices` takes the environment it reads. A template a resolver cannot find is
named with what that resolver offered, never with a fresh read of the machine's sources.
`written.py` stores the other half beside that config: what `sync` last wrote at each
destination, which is the third variant the modified-outside-loadout guard compares against and
the sidecar [0008](../decisions/0008-generated-files-carry-no-machine-state.md) deferred for
orphan removal — see [0019](../decisions/0019-sync-records-what-it-wrote.md). For a file loadout
writes whole it only widens acceptance: no record means the guard behaves exactly as it did before
one existed. For a
document loadout shares with its harness, the entry hashes each key loadout wrote on its own, and
the guard compares only those; a key an entry does not cover yet is newly managed, not an edit.
With no per-key entry the guard fails closed: each owned key on disk must match this render or
a committed or profile variant, or it is refused as `unknown`. A merged orphan's keys are judged
the same way before retirement strips them.
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
compares them with disk, through any link at the destination. A rendered text output that replaces a
file keeps that file's mode, so a chmod survives a rewrite; a new one gets `0o666 & ~umask`, or
0600 when it holds a secret the credential check recognises (`files.text_mode`). Copied and
frozen files carry their own mode. `notices.py` assembles the
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
| CLI | `__init__.py` and `__main__.py` (the entry points), `cli.py` (arguments, exit codes, `--json` errors), `errors.py` (`LoadoutError` and `UsageError`, exit 3 and 2), `commands.py` (the commands), `diffs.py` (the diffs they show), `credentials.py` (telling a secret from a value: init's warnings, owner-only modes, and whether a diff may be shown), `completions.py` |
| render | `emit.py`, `composition.py`, `documents.py` (merging JSON documents), `permissions/` (`rules.py`, `merge.py`, `renderers.py`), `hooks.py`, `adapters.py`, `plugins.py`, `servers.py`, `skills.py`, `subagents.py`, `module_config.py`, `output.py` (the output types and file digests) |
| sources | `harnesses.py` (the global preset), `manifest.py`, `sources.py`, `resolve.py`, `destinations.py`, `machine.py`, `project.py`, `skill_policy.py` |
| sync | `containment.py` (where loadout may read and write: `within`, `protected`, `first_link` and the project rules built on them), `sync.py` (guard, deploy, retirement, drift), `state.py` (`SyncState`, one root as a command finds it), `files.py` (writers, `check_all`), `atomic.py` (`replace_atomically`, the one way a file is replaced, `atomic_install` and `read_file`), `retirement.py`, `written.py` (the write record), `record.py` (owned key names), `surgery.py` and `toml_paths.py` (writing into shared documents), `notices.py`, `unmanaged.py`, `gitignore.py` |
| init | `init_workflow.py` (preview, apply, restore), `init_options.py` (what was asked for), `discovery.py` (inventory), `runtime_paths.py` (what a harness writes for itself, shared with `unmanaged.py`), `migration.py` (plan), `migration_models.py`, `migration_paths.py`, `migration_transaction.py` (apply), `migration_validation.py` (isolated reconstruction), `migration_images.py` (what a path holds, captured and installed), `migration_backup.py` (backup and restore), `migration_git.py`, `git_privacy.py`, `native_documents.py`, `extract.py` (reading native documents back) |
| git | `git_runner.py` (every git loadout starts, without a hook's repository variables), `git_tree.py` (a tree or the index as entries, what a render reads of it, and that written out: the link rules are the pure `select_inputs`), `staged.py` and `staged_render.py` (`check --staged`), `git_hooks.py` and `git_hook_commands.py`, `worktrees.py` (a linked worktree of the registered source) |
| templates | `templates.py` (resolution, vendored copies), `template_catalog.py`, `template_discover.py`, `template_vendor.py`, `bundled_templates.py` |
| bundled skill | `skill_installation.py`, `bundled_skill.py` |

Harness facts live in the presets: `harnesses.py:GLOBAL_PRESET` and `project.py:PROJECT_PRESET`
say where each slice of each harness goes, `KNOWN_HARNESSES` is the global preset's keys, and
`discovery.py` (roots, the runtime documents and keys a harness writes into them),
`runtime_paths.py` (the files and folders a harness writes for itself) and `migration.py`
(dormant routes) restate what adoption needs. `tests/test_harness_tables.py` holds the restatements it lists to the presets: the
per-harness tables, managed paths, runtime keys, which renderers carry a catch-all and where the
others keep one, and which read a whole folder. A new restatement needs its own test there.
