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
makes the artifact routes the complete output list. Legacy `instructions` and `templates`
declarations are rejected in that mode because their inputs would otherwise be ignored. Every
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

Artifact rendering never reads destination content. It feeds the same `str`/`Copied` writer
boundary as existing outputs. `project_outputs` includes explicit routes for ignore generation;
the parsed artifact model exposes source paths, destination roots, category and agent membership.
`load_artifacts` and `render_artifacts` are reusable for reconstruction in a fresh source and
destination root; `compose_document` accepts already-loaded literal objects.

`tests/test_artifacts.py` covers literal values and ordering, dormant activation, renderer
fidelity, per-agent trees and mode preservation, membership, source roots and collision/symlink
rejection. Existing whole-document fixture tests continue to cover legacy output.
