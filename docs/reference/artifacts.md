# Native artifact routes

An artifact route connects an authored source to an exact harness destination. It carries its
consumer agents and category, so different harnesses can receive different instruction files or
skill trees. Native routes complement the existing structured slices.

## Opting in

A project can declare this in `loadout/config.toml`:

```toml
harnesses = ["claude", "codex", "pi"]
presets = false
artifacts = "artifacts.toml"
```

`presets` defaults to `true`, preserving the existing project outputs. Setting it to `false`
makes the artifact routes the complete output list. Legacy `instructions` declarations remain
rejected; template instructions require the explicit composition routes below. Every
artifact agent must occur in the project's `harnesses` list.

Global `loadout.toml` accepts the same `artifacts` reference. It can coexist with existing
targets, provided their destination paths do not overlap. An artifacts-only global manifest
can omit `[[source]]`; existing structured targets still require their normal source inputs.
Profiles inherit or replace the artifact reference using the existing `extends` mechanism.

The index path and every contributor's source path are relative to the directory containing
the owning project config or global manifest. Placing an index in `routes/artifacts.toml`
does not change the source root. The index contains `[[artifact]]` records; an empty index is
valid. Each record requires a nonempty `agents` list of supported harness names and either a
project-relative `output` or a global `destination`. Global destinations use the same
`${VAR:-fallback}` and `~` expansion as structured targets.

## Composing documents

```toml
[[artifact]]
agents = ["claude"]
output = ".claude/settings.json"
format = "json"
order = ["permissions", "hooks", "enabledPlugins", "model"]

[artifact.parts]
settings = {source = "settings/claude.json"}
permissions = {source = "permissions/claude.json", keys = ["permissions"]}
hooks = {source = "hooks/claude.json", keys = ["hooks"]}
plugins = {source = "plugins/claude.json", keys = ["enabledPlugins"]}
```

Document formats are `json` and `toml`. Every part is a complete object in that format;
`permissions/claude.json`, for example, contains `{"permissions": {...}}`. Category names are
`instructions`, `permissions`, `mcp-permissions`, `mcp`, `settings`, `hooks`, `plugins`, `skills`,
`module-config`, `support`, and `templates`.

Each part owns either its explicit `keys` or the top-level keys present in its document.
Declared ownership includes keys absent from an empty part. Two parts claiming one key fail,
and the error names both sources. A part with explicit keys also fails if it introduces a key
outside that list. There is no priority between native and portable contributors.

Values are literal. JSON `null`, `false`, empty objects, empty arrays, and array order survive;
`null` does not delete a key. Nested object insertion order is preserved. Duplicate JSON keys
and non-JSON constants such as `NaN` are rejected. TOML nested tables, arrays, and scalar values
survive parsing and composition. Composed TOML maps use inline tables and lists use inline
arrays, including arrays of maps, so a requested top-level order remains representable when
either comes before a scalar.

`order` places the named top-level keys first. Unnamed keys follow in contributor and source
insertion order; absent names do not create keys. JSON uses two-space indentation and a final
newline. TOML uses tomlkit. Native formatting and comments are not retained by document
composition; use a copy route when exact source bytes are required.

## Partial runtime documents

Set `partial = true` on a JSON or TOML document route when its destination also carries fields
the harness maintains:

```toml
[[artifact]]
agents = ["pi"]
destination = "${PI_CODING_AGENT_DIR:-~/.pi/agent}/settings.json"
format = "json"
partial = true
[artifact.parts]
settings = {source = "settings/pi.json"}
```

Only the contributors' top-level keys belong to this route. An authored Pi `defaultModel` can coexist
with its live `lastChangelogVersion`; Codex settings and `mcp_servers` can coexist with live
`projects` tables in `${CODEX_HOME:-~/.codex}/config.toml`. Claude global server registrations
use a JSON part owning `mcpServers` at `${CLAUDE_CONFIG_DIR:-~}/.claude.json`, a different
destination from Claude's settings directory.

Authored values reconstruct entirely from source. Sync reads foreign fields at apply planning
time and carries them forward. JSON preserves their values and nested order; writing an owned
change serializes the resulting object with two-space indentation. Native TOML updates use
tomlkit's syntax tree, retaining foreign comments, whitespace, quoted keys, multiline strings,
arrays and tables. Legacy TOML slices retain their existing surgery behavior.

Receipts retain previous key ownership, so dropping a source key removes its deployed key,
including the last key. Removing the whole route strips its owned keys and leaves the runtime
file in place. An optional missing source acts like an empty contributor. Empty partial sources
do not create absent runtime files unless `emit_empty = true` is explicit.

The drift guard compares the relative order of owned keys and their typed values, including
nested order and TOML dates, times, infinities and NaN. Sync applies authored `order` changes;
manual owned-order changes must match the previous deployment or current desired source.
Foreign changes and formatting-only rewrites do not count as owned edits.
Adding ownership over an existing foreign key requires that key to match the authored value,
or explicit `--force` adoption. Changing a route between whole-file and partial ownership, or
between document formats, requires reconciling its receipt first; force cannot erase the
previously foreign region by changing the ownership policy.

## Existing permission renderers

A JSON contributor can read portable permission rules through a compatible existing renderer:

```toml
[artifact.parts]
permissions = {source = "permissions/shared.toml", renderer = "claude-project", keys = ["permissions"]}
settings = {source = "settings/claude.json"}
```

The JSON adapters are `claude`, `claude-project`, `opencode`, `pi`, `pi-project`, and
`claude-mcp-permissions`. The latter three own their entire output document and must be its
only contributor. Rules text uses an opaque record:

```toml
[[artifact]]
agents = ["codex"]
output = ".codex/rules/permissions.rules"
format = "text"
category = "permissions"
source = "permissions/shared.toml"
renderer = "codex-project"
```

Text adapters are `codex`, `codex-project`, and `codex-mcp-permissions`. Their output is the
existing renderer's exact text, including its banner. Unsupported renderer/format combinations
fail during index parsing. Switching a native permission part to portable rules is an explicit
source edit; verify the resulting permissions before making that switch.

## Files and trees

```toml
[[artifact]]
agents = ["claude"]
output = "CLAUDE.md"
format = "copy"
category = "instructions"
source = "instructions/claude.md"

[[artifact]]
agents = ["pi"]
output = ".pi/skills/probe"
format = "tree"
category = "skills"
source = "skills/pi/probe"
```

`copy` produces the existing `Copied` output, preserving bytes and file mode. A `text` record
without a renderer also copies its source. `tree` copies every regular file recursively,
including native `SKILL.md`, scripts, hidden files and binary assets. Only scaffold `.gitkeep`
files are excluded. Added files are discovered on the next render; per-agent membership and
nested paths come from the routes. Shared consumers use one record with several agents.

Opaque modes can also be authored explicitly: `mode = 416` on a `copy` record means octal
`0640`; `modes = { "scripts/run.sh" = 488 }` on a tree means `0750` for that relative file.
Integers range from 0 through 4095 (`07777`); booleans, escaped/protected paths and normalized
duplicate keys are rejected. Migration records original copy modes and tree file modes here,
so reconstruction from Git retains full modes even though Git stores only the executable bit.
Declared modes take precedence over source `chmod`. Files without a declaration, including new
tree entries, use their source mode. Rename a mode entry with its file when the override should
follow it; entries for absent files do not create output or prevent retirement.

## Template instruction routes

Native projects (`presets = false`) may opt a required copy/text instruction route into their
declared template tiers:

```toml
[[artifact]]
agents = ["claude"]
output = "CLAUDE.md"
format = "copy"
category = "instructions"
source = "instructions/native/claude/CLAUDE.md"
template_instructions = true
```

The flag defaults to false. It requires project scope, a required `instructions` source, and
`copy` or `text` without a renderer. It cannot be used with legacy project presets. Every
configured agent needs an opted-in route when template prose is selected. Fresh project init
marks only its top-level `CLAUDE.md`/`AGENTS.md` routes; nested files and trees stay independent.

Rendering prepends template instruction tiers in declared order and preserves the original body
bytes and declared mode, falling back to the source mode. A prefix activates a dormant empty instruction source. Removing the
prefix restores ordinary empty-source behavior. Other populated template categories are refused
before source mutation; edit their native category sources through existing routes. See
[templates](templates.md#bundled-starters-and-native-projects).

`Copied` retains the existing output type; content consumers use `read_bytes()` to include its
frozen prefix and `file_mode()` to honor an explicit mode or fall back to the source. The renderer's
`render_artifacts(..., instruction_prefix=...)` parameter accepts already-resolved template bytes;
it never resolves templates or reads destinations itself. `render_project` resolves and validates
the selected tiers before passing this input.

## Empty slots and path safety

Use `{}` for an empty JSON part, an empty TOML document for an empty TOML part, an empty file
for a dormant copy route, or an empty directory for a tree. An empty composed document and an
empty standalone file produce no output by default. `emit_empty = true` preserves an originally
present empty file. Explicit empty values such as `{"hooks": {}}` are entries and produce an
output. Empty trees produce no files; `.gitkeep` can keep their source directories in Git.

Empty permission rules produce no output by default, while their renderer still reserves its
owned keys. Any explicit rule or catch-all activates the renderer. Set `emit_empty = true` to
request its normal empty-rules output.

Declared sources must exist. `optional = true` on a contributor or opaque record explicitly
permits a missing source. Ownership is still reserved when optional parts declare `keys`.
Source paths stay inside the source root and project outputs stay inside the project root.
Source symlinks, destination symlinks, and symlinked ancestors within those roots are rejected.
Global destinations also reject symlinked ancestors. Duplicate routes, ancestor path collisions,
and source/output overlap fail before writing, even for dormant routes. The index and owning
config cannot be overwritten by an artifact output. Legacy source and config dependencies are
protected within and across scopes, including inherited profile files, base documents, and
resolved templates with the configuration that locates them.

`.git` and `.loadout-state` are protected metadata. Routes cannot name them as sources or
destinations, and a tree containing either is rejected before copying its files.

Artifact rendering never reads destination content. It feeds the same `str`/`Copied` writer
boundary as existing outputs; partial documents use the existing `Merged` output with an
explicit format and native-syntax policy. `project_outputs` includes explicit routes for ignore generation;
the parsed artifact model exposes source paths, destination roots, category and agent membership.
`load_artifacts` and `render_artifacts` are reusable for reconstruction in a fresh source and
destination root; `compose_document` accepts already-loaded literal objects.

`tests/test_artifacts.py` covers literal values and ordering, dormant activation, renderer
fidelity, per-agent trees and mode preservation, membership, source roots and collision/symlink
rejection. Existing whole-document fixture tests continue to cover legacy output.

## Deployment lifecycle

Every explicit artifact uses a local receipt at `.loadout-state/global.json` beside the global
manifest or `loadout/.loadout-state/project.json` beside the project config. Receipts are
versioned JSON with logical routes, their last deployment roots, full file modes, SHA-256
fingerprints and partial key ownership. They carry no source or runtime values. Nothing is
stamped into generated documents. The directory is mode 0700, receipts are mode 0600, and its
own `.gitignore` contains `*`. Tracked, symlinked, public or malformed receipt state is rejected.

Sync accepts a destination matching its previous deployment or the current desired source.
This allows edits, branch checkouts and profile switches without requiring the source state to
have been committed. Whole native documents use mode 0600; copies use their declared mode or
retain all source mode bits when no override exists.
Partial documents retain the adopted mode and guard subsequent changes to it.

With no receipt, an absent destination can be created, and a matching existing file can be
adopted. A partial document can also claim absent keys while leaving existing foreign keys
alone. An occupied conflicting path blocks sync, including outside a Git repository. Force
may replace explicitly configured outputs or their owned keys; it never follows symlinks,
changes ownership policy or deletes a modified retirement.

Deleted and renamed files, empty trees, removed routes and removed artifact references retire
only their previous unchanged ownership. Unowned neighboring files survive; directories are
left in place. Check reports stale files and stale ownership receipts. Keep the owning config
until retirement completes, so Loadout can still locate its scope's receipt.

A receipt also records the source checkout and resolved deployment root. Moving the checkout
or changing a destination's environment expansion detaches the old deployment. Sync reports
its path and retains it for explicit cleanup, without probing or deleting files at the old
root. Detached records remain in the receipt so subsequent runs still report them.

Sync freezes generated and copied bytes and modes before its first write, checks destination
preimages again, and records pending fingerprints before installing files. If interrupted,
rerunning sync accepts both the previous and pending deployment. Each file and receipt is
installed atomically; the whole sync is not a rollback transaction. Receipts contain hashes,
not backups: restoring retired output requires its authored source.
Retirement checks the union of previous and pending ownership against a complete accepted
deployment, so an interrupted ownership expansion cannot authorize removing a later foreign edit.

`deployment.prepare_deployment(scopes, outputs)` returns an immutable `DeploymentPlan` with
frozen `FileChange` preimages/postimages, prior receipts, conflicts, drift and detached paths.
`emit.artifact_deployment_scopes(root, profile)` discovers the scope metadata;
`deployment.apply_deployment(plan)` rechecks preimages and performs the guarded writes.
This boundary is reusable by callers planning deployment without mutating files.

Lifecycle and partial-document behavior are exercised through public sync/check commands in
`tests/test_deployment.py`.
