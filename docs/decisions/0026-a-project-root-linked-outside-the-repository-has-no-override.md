# 0026 — A project root linked outside the repository has no override

**Status:** accepted (2026-10-11). Supersedes the consequence in
[0025](0025-init-reads-only-the-live-deployment.md) that expected an override to be added.

## Context

In project scope, `init` refuses a harness root that links outside the repository, such as
`.claude` pointing into a shared directory, as `external-symlink`. Until 0025 the only way past it
was `--mapping`. An override flag, `--allow-external-links`, was proposed to replace it.

## Decision

The user ruled: no override. Adopting the link would copy shared or private content into committed
project source and break the sharing the link exists for. The refusal names the link, its target,
and the two remedies that keep that sharing intact or end it deliberately:

- share the configuration as a loadout template;
- replace the link with a real copy (`cp -RL`), then run init again.

## Consequences

- No flag lets init adopt through a project link that leaves the repository.
- A global harness root that is a link is still adopted by de-linking it
  ([0023](0023-adoption-de-links-a-symlinked-deployment.md)), and a link above a global harness
  root, such as `~/.config`, is written through.
