# 0023 — Adoption de-links a symlinked deployment

**Status:** accepted (2026-10-11).

## Context

A dotfiles repo symlinked to `~/.claude`, or stow-style per-file links into one, is the second most
common starting point for adoption. `init` refused it as `external-symlink` and asked for a
`--mapping` it gave no example of. A `HOME` under macOS `/tmp` was refused the same way, because
discovery compared resolved targets with unresolved roots.

The `--mapping` path already showed what adoption does to such a deployment. Measured: the
journalled transaction replaces each destination link before writing, so loadout's outputs become
real files, every unmanaged entry becomes a link back into the original target, and the target is
not written. Keeping the links instead, and writing generated files through them, was rejected: it
needs a transaction redesign, and it would put generated files into the dotfiles repo.

## Decision

A symlinked global harness root, or per-file links under one, is adopted without `--mapping`, by
the de-linking the mapping path already performs. `init` names each link in its preview and, after
applying, says what it did: the link's target is unchanged, loadout's files are real files at the
harness path, everything else still links into the target, and the target's copies of the adopted
files are no longer read.

Only links at or below a harness root are deployment links. Paths above one are resolved, so a
symlinked prefix is never replaced.

## Consequences

- A link above a harness root is written through. `~/.config` linked into a dotfiles repo receives
  OpenCode's generated files there, which is what `sync` already did.
- Project scope keeps the `external-symlink` refusal: a project harness root linked outside the
  repository would otherwise copy external content into committed source. It takes `--mapping`.
- A symlink cycle is still refused.
- After de-linking, an entry added to the target later does not appear at the harness path, since
  the harness root is now a real directory. Neither does a file added to a directory that was
  mirrored entry by entry, such as `commands/`.
- `init resume` does not repeat the notice; the journal does not record it.
