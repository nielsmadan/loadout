# Optional Git integration

`loadout check --staged --root <source-root> [--profile <name>]` validates the Git index in a
temporary tree. The root holds `loadout/config.toml`, `loadout.toml`, or both. It can be nested
inside an enclosing repository. `--global` selects the registered source/profile for a manual
check; installed hooks record a repository-relative source and resolve the profile when they run.

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

Generated ownership records such as `defaults/codex.owned` remain committed source-side
metadata. Every record requested by an active staged producer must be staged and match that
producer, including an empty record. Deleting its record alone or editing it by hand cannot
change deletion ownership; regenerate and stage the record with its source. Legitimate record
updates and removal of a retired producer's record are accepted rather than treated as edits
to deployed output files.

Ownership follows the selected profile in both trees. Inactive instruction and permission
targets do not resolve their destination variables or protect their outputs for that check.
HEAD supplies output declarations only: old source dependencies are neither resolved nor
validated, so a staged migration from an absolute or external source to a relative vendored
source can pass. HEAD config and artifact declarations still come from its isolated tree and
continue to protect retired outputs.

The index and HEAD trees are materialized from Git blobs, so partially staged content and
staged deletions are independent of the working tree. Only what a render can read is written out,
streamed from one `cat-file` run: a project's `loadout/` folder and the files at its root, and,
when the root also holds a staged manifest, a global source's manifest and profiles and, of each
source they declare, the files in its folder and the folders of the artifact types its `use` names,
and each permission target's `base` file.
A link inside the repository on the way to any of these, such as `instructions -> vendor/instr`
or a source `path` that is a link, brings its target in the same way, and so does a link reached
through one. A source at the repository root therefore does not write the repository out. Large unrelated assets elsewhere in the
repository are listed by path, which is all the staged-output comparison needs, and never read.
Renderers run in a fresh process with isolated global destinations and a read boundary around
the snapshot. Repository scripts and skill programs are read as data and never executed.
The [2026-09-06 corpus run](../tests/init-corpus/runs/2026-09-06-migration.md) measured a
11.796-second staged check on Sentry's 20,842 tracked input paths; this is a local observation,
not a size-independent latency guarantee. The [many-file comparison](../tests/init-corpus/runs/2026-09-06-performance-comparison.md)
records a separate controlled fixture and all staged-check timings.

All required source/config/support files for the staged render must be staged. Global `[[source]]`
entries can point to relative sibling directories within the same repository. Absolute/external sources, escaping
source symlinks and submodule dependencies fail with a vendoring remedy. A gitignored personal
file, such as `loadout/permissions.local.toml` or a `.local` skill, may be absent from the index;
its working copy is never imported. A required input has to be staged in a form that can be
shared, never as a secret file added to Git.

Every selected template must be vendored and staged, including bundled `frontend` and `backend`.
A bare name can resolve to a machine override during normal sync, so silently substituting the
package copy would validate a different producer. Run the exact `loadout template add <name>
--vendored --root <source-root>` command reported by the check, then stage the template tree
and config.
Normal template resolution is unchanged.

## Installing hooks

```sh
loadout integrate git-hooks install --root . --dry-run
loadout integrate git-hooks install --root . --regenerate
loadout integrate git-hooks status --root .
loadout integrate git-hooks uninstall --root . --yes
```

Hooks are opt-in and `init` does not install them: adopting configuration and wiring a repository
to run loadout are separate steps, so a fresh `init` writes no hook. Installation selects
pre-commit alone, or adds post-checkout and post-merge with `--regenerate`. Exact paths, source
and installation decisions are printed before anything is written; `--dry-run` stops there. A
generated hook names no profile: each run resolves it the way `sync` and `check` do, from the
machine config's profile when it registers this very source, otherwise `default`. A hook and a
manual sync therefore follow the machine's profile together when it changes, rather than
retiring each other's outputs. An unusable machine config never blocks a repository-local
install.

Installation uses `git rev-parse --git-path hooks`. It creates only absent hooks in a repository-
local effective directory, including local custom `core.hooksPath` directories. It preserves
occupied hooks, and writes nothing to a directory outside the repository or reached through a
symlink. A linked worktree's default common hook directory is shared by every worktree, and
install writes it from any of them: the hook sets `repo=$(git rev-parse --show-toplevel)` when it
runs, so one hook serves each worktree's own copy of the source, and install says the directory is
shared. A local worktree-specific hooks path can be installed even when its directory does not yet
exist.
Existing exact managed bytes for the same source are recognized as managed when local. A hook for
another source is occupied and stays untouched. A hook loadout wrote for this source whose bytes
differ, such as one from an older version that named its profile, is stale: `install
--regenerate` rewrites it, checking just before the swap that it is still loadout's.

## Ownership and removal

Every generated hook carries `# loadout Git hook v1` on its second line. That marker, not the
command the script invokes, is what identifies a hook as loadout's — so renaming the command
does not orphan hooks already installed. `status` and `uninstall` classify on it:

| state | test | `uninstall` |
| --- | --- | --- |
| `managed` | marker present, serves this source, bytes match what loadout would write now | removed |
| `stale` | marker present, serves this source, bytes differ; `install --regenerate` rewrites it | removed |
| `occupied` | no marker, or the marker is loadout's but the hook serves another source | never touched |

The marker answers *did loadout write this*; the `--root` argument in the generated command
answers *which source does it serve*. One repository can hold several loadout sources but has
only one hook file per event, so both questions have to be asked. The rename from
`git-hooks run` to `hook-event` does not disturb `--root`, so a hook generated before it is
still recognised.

The effective hooks directory is classified by why it is or is not this checkout's alone:

| state | cause | `uninstall` |
| --- | --- | --- |
| `external` | outside the repository, or a symlink in its path | refused; loadout never wrote there |
| `shared` | repository-local but common to other worktrees | a marked hook is removed, with a notice |

`install` writes a shared directory as it would a local one, since the generated script finds
its worktree's top level when it runs, and refuses an external one with the commands to add by
hand. `.git/hooks` is common to every worktree, so adding a worktree after installation turns the
directory shared without anything touching the hooks in it.

`uninstall` requires `--yes` when input is not interactive.

Preserved hooks get exact integration commands. Set `repo=$(git rev-parse --show-toplevel)` in the
existing hook, then add the printed command at the appropriate point in its flow. Pass the Git
event's arguments through, and propagate a pre-commit failure. Loadout does not replace a hook
manager or install a dispatcher over an existing script.

Installed scripts invoke `loadout` on PATH and contain no interpreter or checkout-specific
absolute paths. Install the package on each machine and explicitly run
`loadout integrate git-hooks install` in a new clone. Hook files are local installation state
and are never added to the index.
The effective path is rechecked between preview and installation. A path that changes, or
leaves the repository, in that window is written to by nothing, and installation refuses so the
integration decision can be made again.

## Regeneration after Git events

Post-checkout (branch changes only) and post-merge call normal guarded sync for the recorded
source and the profile resolved at that moment. External edits cause refusal; regeneration never supplies `--force`. After a failure, the diagnostic explicitly
states that Git already completed and gives the sync command to retry, after reconciling the
reported conflict when the guard refused, or after fixing the reported problem when sync could not
run. The command carries `--profile` only when the hook was given one; otherwise the retry resolves
the profile again, as the hook did. A post-checkout failure can return nonzero after changing HEAD; Git merge can
return zero despite a post-merge failure. Inspect the diagnostic and resulting Git state rather
than treating either status as a rollback.

The hooks live in the common hook directory, so they also run in every linked worktree of the
registered source. A worktree is not the registered folder and may not render global scope
([0031](../decisions/0031-global-scope-renders-only-from-the-registered-source.md)), so there the
post-checkout and post-merge hooks sync nothing and print one line saying so; sync from the
registered source itself. The registration gate in `check --staged` asks the index alone whether a manifest is there, so
`git rm --cached loadout.toml` passes even with the file still on disk. The pre-commit
`check --staged` still runs in a worktree: it renders
only into its own sandbox, so it validates the worktree's snapshot without the registration gate.
A worktree of any other repository, or a clone, is refused like any other unregistered folder.
Git exports `GIT_DIR` and its other repository-local variables to the hooks it runs, and a git
started from a hook would inherit them and answer for the hook's repository whatever folder it
was pointed at. Every git loadout starts drops them first (`git rev-parse --local-env-vars`
lists them); `check --staged` keeps `GIT_INDEX_FILE`, since the index it was given is the one it
inspects. Only a different worktree of the registered source's repository counts as one, so a
registered source that is itself a linked worktree syncs from its own hooks.
