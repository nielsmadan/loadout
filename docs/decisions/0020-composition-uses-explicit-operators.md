# 0020 — Composition uses explicit operators

**Status:** accepted (2026-09-10).

## Context

Profiles merged agent fields but replaced complete legacy target tables. The README's order-only
child therefore discarded its output path. Native parts could assemble disjoint categories but
could not express shared and personal inputs within one category. Global skills and module files
could reject collisions but could not express a deliberate replacement.

## Decision

Profile inheritance merges configuration fields consistently across agent blocks and legacy
targets. Supplied field values replace completely, including lists and maps. Top-level `remove`
uses TOML key paths and applies before child declarations; removing and redeclaring a target
expresses whole-target replacement. `[all]` continues providing defaults after profile resolution.

Native parts may declare ordered `sources` with optionality per input. The operator is explicit:
`deep` reuses document merging, `concat` composes instruction text, and portable permission
renderers select rule merging. Singular `source` remains literal or verbatim. Copy/tree routes
stay singular. Different category owners still cannot claim the same key.

Ownership includes every input key even when later deleted. Explicit keys constrain every layer;
all source paths remain protected during deployment and retirement, including absent optional
inputs. Receipts keep their existing representation.

Global sources may declare exact skill names or harness-qualified module paths in `overrides`.
Only declared replacements win; a declaration requires both contenders when the collection is
consumed. Skills replace as complete trees and module files supply complete bytes and modes.
Existing fragment qualification and ordered MCP server replacement remain their selection APIs.

## Consequences

- Existing literal native documents and renderer fixtures keep their bytes. Choosing composition
  explicitly introduces its normalization and merge semantics.
- Legacy child targets that discarded fields through omission must use `remove` for that effect.
- Shared native content can be reused without copying it into personal or profile variants.
  Global profiles still select complete indexes; template/project inheritance is not introduced.
- Existing per-slice permission strictness and harness-specific ordering remain intact.
- Legacy slices keep their current lifecycle. General orphan removal is separate from selecting
  a replacement item; native artifact receipts continue handling their own retirement.
