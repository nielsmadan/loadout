# Pi

## Config file

`pi/permissions.json`, consumed by the `@gotgenes/pi-permission-system` extension —
not by Pi itself. Schema is pinned in the emitted `$schema` key:

```
https://cdn.jsdelivr.net/npm/@gotgenes/pi-permission-system@23.0.0/schemas/permissions.schema.json
```

Pi is one of two harnesses whose permissions already live in their own file, so
`loadout` fully owns this output and it needs no base document. The pinned version is a
literal in the renderer — bumping the extension means bumping it there.

## Resolution

**Last matching rule wins**; the package docs say "put broad catch-alls first."

Emission order is load-bearing, and more so than on OpenCode: when a later category
writes a pattern that an earlier one already set, the renderer **deletes the key and
reinserts it** so it moves to the end of the map. Overwriting in place would leave the
key at its original position, where an intervening rule could still win.

Order is `allow`, `ask`, `deny` — deny last.

## Document shape

```json
{
  "$schema": "...",
  "permission": {
    "*": "allow",
    "bash": { "*": "ask", ... },
    "mcp":  { "*": "ask", ... }
  }
}
```

Both `bash` and `mcp` maps are seeded with `{"*": "ask"}` as their first entry.

## Pattern shape

Pi matches with **minimatch**, where `foo *` never matches a bare `foo`. So a plain
prefix must be emitted in both forms — `<entry>` and `<entry> *`. Entries ending in `*`
are kept literal.

### MCP targets are derived, not `server/tool`

Pi never sees the `server/tool` form. It derives the target from the call's own input:

| call | target |
|---|---|
| tool call | `<server>_<tool>` and `<server>:<tool>` |
| list / search | `mcp_server_<server>` |
| connect | `mcp_connect_<server>` |

The last two are emitted **only for a server-wide entry** (`server/*`). A rule scoped to
a single tool therefore cannot decide whether the whole server may be listed — that is
deliberate, not an oversight.

## Known bug in the live system

`~/ac/permissions/manage.py` contains a **second, independent copy** of this render
logic for the project-local scope, and it emits only the `<entry> *` form
(`manage.py:512`) — omitting the bare form that `sync.py` correctly emits alongside it.

The OpenCode renderer 17 lines above it (`manage.py:494-495`) emits both forms
correctly, so this is an oversight in one copy rather than a deliberate difference.

Effect: a permission granted at project-local scope does not match the bare invocation of
its own command. Fail-closed, so it prompts rather than over-permitting — an annoyance,
not a hole.

This duplication is the concrete reason `loadout` exists. It survives milestone 3, which
ports only the global renderers; killing it is milestone 4.
