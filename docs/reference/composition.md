# Composition and inheritance

Configuration inheritance chooses what to render. Artifact composition combines the chosen
inputs. Their operators differ because lists of fragment names, lists of hooks and permission
rules describe different operations.

| mechanism | omission | explicit list/map value | removal or collision |
|---|---|---|---|
| profile `extends`, agent fields, legacy targets and `[all]` defaults | inherit | replace the complete field; `[]` is empty | profile `remove` deletes inherited TOML key paths before child fields apply |
| instruction fragment order | select no unnamed content | concatenate selected prose in order | `substitute` replaces a fragment by name |
| settings/hooks/plugins/defaults fragments; native `sources` with `merge = "deep"` | keep previous value | maps merge recursively; arrays append, including duplicates | JSON `null` deletes; later scalar/type wins |
| portable permission sources | no vote | union in tier order | identical rules use deny > ask > allow; catch-all uses strictest stated value |
| native part with singular `source` | no undeclared field | values remain literal, including null and empty arrays | different parts claiming a key fail |
| native instruction `sources` with `merge = "concat"` | optional missing input contributes nothing | UTF-8 prose joins with blank lines | invalid UTF-8 fails with its input path |
| global named fragments | search participating sources | source qualification selects exactly one item | duplicate unqualified names fail |
| global skills/module files | collect offered items | whole item replacement through `source.overrides` | undeclared duplicate names/paths fail |
| skill frontmatter harness block | inherit shared keys | harness value replaces the shared value | all harness blocks are stripped after selection |
| MCP server definitions | keep other servers | later source replaces the whole same-named server | source order determines the winner |
| project template skills/MCP | keep other entries | later template, then project, replaces the whole named entry | project declarations supply the highest tier |

Permission strictness applies to identical portable rule entries. Pattern overlap still follows
the harness matcher, and `opencode.extra` keeps last-tier precedence. Instruction ordering arranges
prose; loadout does not interpret contradictory sentences.

## Inheritance boundaries

`extends` accepts one parent profile and chains within the same source root. A parent names a
sibling file: empty names, dot names and path separators are rejected. Profile symlinks must
resolve within that directory; internal aliases use canonical identities for cycle detection
and dependency protection. Legacy targets
inherit individual fields, just like agent blocks. A `substitute` map is a field value, so a child
map replaces it; remove one inherited map member through a TOML key path when needed. Removing a
whole target before redeclaring it expresses whole-target replacement.

After profiles are resolved, `[all]` supplies defaults to declared agents. Removing an agent's
own field exposes that shared default. Existing `false` slice values disable automatic slices.
There is no template or project `extends`: verified in `template_catalog.py:_load_catalog` and
`project.py:load_project_config`, whose accepted key sets reject it. Catalogs reuse selected parts.
`emit.py:render_all` renders global and project scopes separately and rejects output collisions.

Native profiles select a complete artifact index. Different indexes can share content inputs:

```toml
extends = "default"
artifacts = "routes/autonomous.toml"
```

The selected index can compose the ordinary settings document with one autonomous overlay.
The route metadata remains explicit; the shared content exists once.

## Ownership and lifecycle

Layers inside one native part share ownership. Across parts, ownership remains exclusive. A
layer deleting a key does not surrender that part's claim: every input key remains reserved.
Explicit `keys` constrain each input individually. This rule also governs partial runtime
documents, so removal reaches the deployed document without touching foreign fields.

Native receipts manage retirement. Legacy skill/module selection changes the generated item
set; it retains the existing legacy lifecycle, including manual cleanup of previously generated
files that leave that set. The override operator does not introduce general orphan removal.

Bundled-skill commands use the same declared skill winner as rendering. Uninstall removes the
owned copy and its `loadout` override entry together, preserving other overrides and the manifest's
comments and mode. The earlier skill becomes active and sync deploys it. Inherited source lists
are changed in their declaring manifest, so every profile inheriting that list sees the removal.

Source protection includes every inherited machine manifest used to resolve a declared template.
Deployment retirement retains dependencies from both present scopes, including a scope with no
artifacts or receipts of its own.

See [native routes](artifacts.md), [templates](templates.md), and
[ADR 0020](../decisions/0020-composition-uses-explicit-operators.md).
