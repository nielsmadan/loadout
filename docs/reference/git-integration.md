# Optional Git integration

`loadout check --staged --root <source-root> [--profile <name>]` validates the Git index in a
temporary tree. The root holds `loadout/config.toml`, `loadout.toml`, or both. It can be nested
inside an enclosing repository. `--global` selects the registered source/profile for a manual
check; installed hooks record a repository-relative source and explicit profile instead.

The validator reads the index Git supplied, including `GIT_INDEX_FILE` for alternate/scoped
commits. Missing, corrupt and unmerged indexes fail without falling back to another index. An
unborn repository works once its source config is staged. A root with no source config in either
HEAD or the index fails; removal of a previously configured source remains validatable.

Staged validation renders source, not deployed outputs. Source-only staging does not require
staging ignored generated files. It rejects staged additions or modifications to paths owned by
either HEAD or the staged declarations, including tree descendants. Deleting a declaration does
not remove that protection during the same commit. Unchanged tracked outputs and removal of
generated entries from the index are accepted. Edit and stage the source instead; unstage an
output edit, or use `git rm --cached -- <path>` to keep its local copy while retiring its index
entry. The command reports paths rather than generated content.

Ownership follows the selected profile in both trees. Inactive instruction and permission
targets do not resolve their destination variables or protect their outputs for that check.
HEAD supplies output declarations only: old source dependencies are neither resolved nor
validated, so a staged migration from an absolute or external source to a relative vendored
source can pass. HEAD config and artifact declarations still come from its isolated tree and
continue to protect retired outputs.

The entire index and HEAD trees are materialized from Git blobs, so partially staged content and
staged deletions are independent of the working tree. This currently reads all repository blobs;
large unrelated assets therefore increase validation time and temporary disk/memory use.
Renderers run in a fresh process with isolated global destinations and a read boundary around
the snapshot. Repository scripts and skill programs are read as data and never executed.
The [2026-09-06 corpus run](../tests/init-corpus/runs/2026-09-06-migration.md) measured a
11.796-second staged check on Sentry's 20,842 tracked input paths; this is a local observation,
not a size-independent latency guarantee. The [many-file comparison](../tests/init-corpus/runs/2026-09-06-performance-comparison.md)
records a separate controlled fixture and all staged-check timings.

All required source/config/support files for the staged render must be staged. Global `[[source]]`
entries can point to relative sibling directories within the same repository. Absolute/external sources, escaping
source symlinks and submodule dependencies fail with a vendoring remedy. Optional private
bindings may be absent; their working copies are never imported. Required private dependencies
need an optional binding or a public replacement, not secret files added to Git.

Every selected template must be vendored and staged, including bundled `frontend` and `backend`.
A bare name can resolve to a machine override during normal sync, so silently substituting the
package copy would validate a different producer. Run the exact `loadout template vendor <name>
--root <source-root>` command reported by the check, then stage the template tree and config.
Normal template resolution is unchanged; init already vendors selected starters.

## Installing hooks

```sh
loadout init --project --git-hooks check --dry-run
loadout init --project --git-hooks check --yes
loadout git-hooks install --root . --dry-run
loadout git-hooks install --root . --regenerate
```

Hooks are opt-in. Init accepts `--git-hooks none|check|regenerate`; omission means none. `check`
selects pre-commit; `regenerate` also selects post-checkout and post-merge. Selection, exact paths,
source, profile, installation decisions and integration commands appear in the migration preview.
Existing global init uses the active profile when machine registration names that same source;
otherwise it records `default`. Standalone installation accepts `--profile <name>` explicitly.

Installation uses `git rev-parse --git-path hooks`. It creates only absent hooks in a repository-
local effective directory, including local custom `core.hooksPath` directories. It preserves
occupied hooks, symlinked directories and shared/external directories. A linked worktree's default
common hook directory is shared and is preserved from both the main checkout and linked worktrees.
Locality is checked against every registered worktree's effective hook directory, including
absolute custom paths beneath the main checkout. A missing registered worktree prevents proving
exclusive ownership, so its hooks are preserved until the worktree registration is reconciled.
A local worktree-specific hooks path can be installed even when its directory does not yet exist.
Existing exact managed bytes for the same source/profile are recognized as managed when local.
A hook for another source, event or profile is occupied and stays untouched.

Preserved hooks get exact integration commands. Set `repo=$(git rev-parse --show-toplevel)` in the
existing hook, then add the printed command at the appropriate point in its flow. Pass the Git
event's arguments through, and propagate a pre-commit failure. Loadout does not replace a hook
manager or install a dispatcher over an existing script.

Installed scripts invoke `loadout` on PATH and contain no interpreter or checkout-specific
absolute paths. Install the package on each machine and explicitly run `loadout git-hooks install`
in a new clone. Hook files are local installation state and are not added to the index by init.
Init writes selected hooks after its baseline commit and migrated outputs, so its original
checkpoint cannot run a newly selected hook against half-migrated source. Hook files participate
in guarded resume/recovery. Git-created metadata and a successful baseline remain in place;
empty local hook directories can remain after recovery.
The effective path and sharing are rechecked before standalone installation and migration
apply/resume/recovery. A path that becomes shared is preserved and requires a new integration
decision before those hook operations can continue.

## Regeneration after Git events

Post-checkout (branch changes only) and post-merge call normal guarded sync for the recorded
source/profile. Native outputs use their previous private deployment receipt. External edits
cause refusal; regeneration never supplies `--force`. After a failure, the diagnostic explicitly
states that Git already completed and gives the sync command to retry after reconciling the
reported conflict. A post-checkout failure can return nonzero after changing HEAD; Git merge can
return zero despite a post-merge failure. Inspect the diagnostic and resulting Git state rather
than treating either status as a rollback.
