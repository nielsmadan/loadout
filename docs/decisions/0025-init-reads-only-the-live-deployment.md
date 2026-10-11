# 0025 — Init reads only the live deployment

**Status:** accepted (2026-10-11). Supersedes the `--mapping` references in
[0023](0023-adoption-de-links-a-symlinked-deployment.md) and
[0024](0024-discovery-reads-an-allowlist.md).

## Context

`init --mapping` let a user point discovery at any tree and say which harness root it deployed to.
Once discovery became an allowlist (0024), it was the only remedy for five kinds of refusal:

- a project harness root linked outside the repository;
- a dotfiles layout in the selected directory (`claude/`, `.claude/`, …);
- installers, `Makefile`s and unsupported harness trees there;
- dependencies that were missing, outside the roots, dynamic, globs or directories;
- adoption from a dotfiles source tree instead of the live deployment.

`--select-source` chose between two originals for one destination. Only a mapping could produce
that conflict, because every automatic root maps a path onto itself.

## Decision

The user ruled: remove all of it. `--mapping`, `--select-source` and the interactive resolution prompt
are gone, along with the `source-mapping`, `dependency-mapping`, `unsupported-harness`,
`deployment-activation` and `source-conflict` refusals. `RootMapping` remains as the internal
description of each automatic root.

- The selected directory is never scanned for harness layouts; it is a destination, not evidence.
- Adoption always reads the live deployment at each harness's known paths.
- A dependency that cannot be adopted is left in place. The adopted document keeps referencing the
  original path, which keeps working because loadout does not move it.
- A socket, FIFO or other non-regular file at a read path is left in place, as is a file whose
  path names no harness that was selected.
- A `.tmpl` at a read path is adopted as written. `opencode.jsonc` still refuses, under its own code
  `unadoptable-format`, because loadout writes `opencode.json` and cannot carry the comments.
- Every remaining refusal names the file to change, not an option to pass.

## Consequences

- A project harness root linked outside the repository is refused with no override until one is
  added (S-24).
- A dotfiles repository deployed by copy is adopted from its live copy. The repository is not read,
  so content that differs from what is deployed is not adopted.
- Six corpus cases now install their tree at the fake HOME before init, rather than mapping it.
- Two mapping-free collisions refuse without a selection to resolve them: one skill name in two
  harnesses with different content (`source-composition`), and project `CLAUDE.md` and `AGENTS.md`
  with different content.
