# 0057 — One sync reaches every project, and a vendored edit can be pushed back

**Status:** accepted (2026-11-16, ruled by the user). Supersedes in part
[0014](0014-a-vendored-template-is-source-not-output.md): "Nothing regenerates it except an
explicit `loadout template sync`" no longer holds.

## Context

A kit change reached a project only when someone ran `loadout sync` in that project, and a
vendored copy only through `loadout kit sync` there. The user's model was different: change a
kit in the kit repository, sync once, and every project that uses it is up to date; change a
vendored copy inside a project, sync, and the change goes back to the kit and from there to
the other projects. Neither worked. loadout kept no list of the projects using a kit, and
nothing wrote from a project back into a kit source.

[0014](0014-a-vendored-template-is-source-not-output.md) made a vendored copy source, and
[docs/reference/kits.md](../reference/kits.md) left the refused case of `kit sync`
deliberately manual, naming three-way merge as the upgrade path.

## Decision

**A sync at the global source also updates every project this machine has synced that lists
a kit.** `sync --global`, or `sync` with the registered global source as its root, runs the
global source, then the stealth sources, then each such project: a vendored copy whose source
moved and that has no edits of its own is updated, as `kit sync` would update it, and then
the project is synced. `check --global` previews the same projects.

**The index is the per-root sync records** (`~/.config/loadout/written/`, [0019]), not a new
registry. Every project already synced on this machine is found the day this ships, where a
new registry would start empty. Each clone and linked worktree is its own project.

**A project `sync` in a terminal offers to push a vendored kit's edits to its source.** It
shows the diff, asks per kit, writes the copy's files into the source, records the hash, then
syncs the project and updates the other projects using any kit the push changed, catalog kits
sharing a changed part included.

**A push is a fast-forward and nothing else.** The source must still hold what the copy was
vendored from, which the recorded hash proves; when both sides moved, the push is held with a
diff, and nothing is merged. The copy is validated first, its links refused, and the plan
checked again after the answer, so a source edited while the prompt waits is not overwritten.
A kit vendored from the bundled copy is never pushed.

**Git hooks never push or reach other projects.** They call `cmd_sync` directly, below the
selection layer that does both.

## Consequences

- One `sync --global` writes tracked files in every project using a kit, `loadout/kits/` and
  `loadout/config.toml` included for vendored kits. The output names each project, and the
  changes are left uncommitted.
- A project's exit code feeds the global run's, so one failing project makes it exit 3; the
  others still run.
- A copy deliberately kept different from its kit is offered for a push on every interactive
  sync. Answering no keeps it local. This was accepted over remembering the answer.
- A project this machine never synced, or whose record could not be written or was pruned,
  is not reached.
- A catalog update that changes a part shared with another vendored kit in the same project is
  applied without `kit sync`'s `--yes` prompt: the run already says to update everything, and
  an edited sibling still refuses it.
- A push from a project does not re-sync stealth sources that link the kit. Their targets take
  it on the next stealth sync, as [0055](0055-a-stealth-source-links-kits-by-name.md) already
  says of any linked-kit change.
- Three-way merge stays unbuilt. The push covers the common case, edits on one side only; when
  both sides moved, the user still reconciles by hand.

[0019]: 0019-sync-records-what-it-wrote.md
