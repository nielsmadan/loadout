# Architecture

`cli.py` → `commands.py` → `emit.py` → `composition.py` (instructions) and
`permissions/` (`rules.py` parses, `renderers.py` renders, keyed by name in `RENDERERS`).
`manifest.py` parses `loadout.toml`; `sources.py` and `resolve.py` resolve fragments.
`resolve.py:_slice_root` caches resolved source roots by `Source` value. This is safe for the
one-process-per-command CLI, but a long-lived embedding must clear or bypass that cache after
replacing a source directory or symlink; otherwise a stale root can surface as a misleading
escape error.
`extract.py` runs `permissions/renderers.py` backwards — one extractor per `RENDERERS` key,
plus `merge_extractions`, which reports harness divergence rather than unioning it.
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
`machine.py` resolves `$XDG_CONFIG_HOME/loadout/` — where machine state is *stored*: the config
`--global` reads, what `--global` resolves the root and profile from, and the harness defaults a
fresh project init uses. It is not the only machine state that is *read*:
`manifest.py:resolve_destination` expands `${VAR}` in a destination against the environment, per
[0011](../decisions/0011-a-destination-follows-a-relocated-harness.md).
`written.py` stores the other half beside that config: what `sync` last wrote at each
destination, which is the third variant the modified-outside-loadout guard compares against and
the sidecar [0008](../decisions/0008-generated-files-carry-no-machine-state.md) deferred for
orphan removal — see [0019](../decisions/0019-sync-records-what-it-wrote.md). It only widens
acceptance: no record means the guard behaves exactly as it did before one existed.
`retirement.py` consumes it: a recorded destination the run no longer renders is deleted, or has
its recorded keys stripped, once `commands.unaccepted_outputs` (the guard's classifier) accepts
it — see [0022](../decisions/0022-sync-retires-what-it-no-longer-renders.md). With no record,
nothing is retired. Do not
confuse it with `record.py`, which records owned key *names* beside a fragment in the source
repo and is committed.
`project.py` (harness preset) is project scope; `scaffold.py` holds both scopes' scaffolding
(`init project`, `init global`).
`templates.py` resolves a template *name* — the project's vendored copy first, then the
`templates/` directory of every source the machine's global manifest declares — and owns the tree
hash `template sync` compares a vendored copy against. A template is a source, so it merges
through the slice's own operator and adds no merge rule.
`emit.py:render_project` renders it; `render_all` unions it with `render_global`'s global-scope
output, so `sync`/`check` regenerate whichever scopes are present.
