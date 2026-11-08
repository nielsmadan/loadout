# Kit catalog regression flow

Run from the repository root:

```sh
.venv/bin/pytest -q tests/test_kit_catalog.py tests/test_kits.py tests/test_kit_cli.py tests/test_bundled_kits.py tests/test_git_integration.py
.venv/bin/python tests/regenerate_expected.py --check
just check
```

Tests use isolated homes, machine configuration and temporary repositories. They do not
change the developer's installed skills or global source. The staged tests run real Git
operations inside those temporary repositories.

## Input and output

The `catalog` fixture creates a global source with two manifests:

```text
loadout/kits/
  nextjs.toml                 skills, instructions, MCP and permissions
  react-native.toml           shared skills, instructions and MCP
  skills/review-typescript/SKILL.md
  instructions/typescript.md
  instructions/nextjs.md
  mcp/github.toml
  permissions/node.toml
```

Both manifests reference `review-typescript`, `typescript` and `github`. Only `nextjs`
references `nextjs` instructions and `node` permissions. Vendoring both produces this same
layout in the project: one shared skill tree, not a copy nested under each kit.
`loadout/config.toml` records both names and each manifest's content hash.

The native-project tests build their source from `plan_migration`, both with all four harnesses
configured and each harness alone. Normal `write_all` produces instruction files, native MCP and permission
documents, and skills through the project slices. The shared non-Claude skill
collection is `.agents/skills`; Claude uses `.claude/skills`.

## Cases exercised

- Link, vendor and render; shared instructions appear once, while later references keep
  their precedence for MCP definitions and permission extras.
- Remove machine configuration; vendored and staged-only rendering still work.
- Compare global output before a catalog exists, after adding it, and after modifying selected
  and unselected parts; the active global skill remains unchanged throughout.
- Update a shared part; list all affected kits, decline without writes, then accept
  and refresh both provenance hashes.
- Modify the other affected manifest locally, or add a skill file during confirmation;
  refuse the update without overwriting those changes.
- Inject a write failure after additions, overwrites and deletion of an executable support
  file; compare the complete relative path, byte and mode snapshot plus configuration.
- Remove a skill's supporting file upstream; retire it for both consumers. Remove only
  one kit's reference; retain the part needed by the other kit.
- Set project-specific MCP, permissions and skills; prove their overrides win while
  unrelated kit contributions remain present.
- Edit a generated skill, then remove its kit contribution; normal sync refuses
  retirement until the generated file is restored to its last-written bytes.
- Reject escaped paths, symlinks, ambiguous formats and conflicting existing shared parts.
- Vendor qualified names and update commented TOML lists without invalidating the config.
- Reject unsupported native permissions.
- Check each harness's exact shell allow/ask/deny and MCP tool-policy output, plus MCP definitions,
  instructions and skill/support content. A second sync writes nothing and check reports no drift.
- Check Codex policy without definitions, quoted server names, native policy restrictions and
  unrelated server/tool settings, then remove the kit and recover native policy.
- Reject order-changing native permission conversion; preserve explicit native ask/deny
  defaults while letting unstated portable defaults inherit the kit.
- Remove optional and required Codex permission sources; only the optional case composes.
- Set modes for kit `SKILL.md` and support files, detect outside chmod, and render from Git.
- Count shared-part validations and native document reads; each happens once per render, while
  changing an instruction between renders is picked up immediately.
- Refuse unrepresentable native Codex policy before selection. Keep the standalone portable
  artifact renderer's single-source behavior unchanged when no kit contributes rules.

## Results, 2026-09-08

After the review fixes, the catalog-specific suite passes all 53 tests. The focused
artifact/catalog run passes 128 tests. `just check` passes lint, formatting, strict typing
and all 1,820 tests (11 minutes 7 seconds). Existing expected-output fixtures remain byte-identical.
The catalog starter test executes discovery, planning, preparation,
application and staged-only verification; it does not stop at a successful preview.
