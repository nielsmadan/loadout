# Migration planning and transactions

`discovery.discover()` inventories existing agent configuration. `migration.plan_migration()`
builds and verifies an immutable plan; neither function applies it, changes Git, prompts, or
executes discovered scripts. `migration_transaction` prepares and applies a resolved plan through
the deployment writer, with a scoped Git checkpoint and explicit recovery. The legacy init CLI
still uses `scaffold.py`; CLI integration consumes this separate library boundary.

```python
from pathlib import Path

from loadout.discovery import discover
from loadout.migration import plan_migration
from loadout.migration_models import RootMapping, SourceSelection

inventory = discover(
    Path("/work/dotfiles"),
    scope="global",
    agents=("claude",),
    mappings=(
        RootMapping(
            Path("/work/dotfiles/claude"),
            Path("/home/user/.claude"),
            ("claude",),
        ),
    ),
)
plan = plan_migration(
    inventory,
    selections=(
        SourceSelection(
            Path("/home/user/.claude/settings.json"),
            Path("/work/dotfiles/claude/settings.json"),
        ),
    ),
)
preview = plan.preview()
```

An explicit scope takes precedence. Existing Loadout configuration establishes its scope and
produces a clear no-migration result after config parsing. Uninitialized trees need an explicit
project/global choice. Project lookup walks to an enclosing Loadout or Git root. Shared
`AGENTS.md` and `.agents` discovery never establish which harnesses are configured, and installed
binaries are not inspected for membership.

## Roots and conflicts

Global inventory examines both the selected directory and actual live harness roots. It expands
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `XDG_CONFIG_HOME` and `PI_CODING_AGENT_DIR`, plus OpenCode's
supplemental `OPENCODE_CONFIG` and `OPENCODE_CONFIG_DIR` inputs. The default Claude MCP file is
`~/.claude.json`; relocation puts it at `${CLAUDE_CONFIG_DIR}/.claude.json`, as recorded in
[config.md](config.md#mcp). Supplying an environment mapping to discovery makes the root choices
testable without relying on the process's home directory.

A dotfile directory named `claude`, `.claude`, `opencode`, `.config/opencode`, `codex`, `.codex`,
`.pi`, or `.pi/agent` does not by itself prove a global deployment role. Confirm it with a
`RootMapping`. Direct harness-root repositories use the same mapping. `kind="file"` plus an
explicit category maps an unusual individual artifact; `kind="shared"` retains shared-root
membership. Source selections identify a destination and its chosen original. Directory
selections apply to corresponding relative paths. Different copies remain conflicts until
selected; byte-and-mode identical copies can use one producer.

Unknown files inside confirmed roots, JSONC files, unmaterialized templates, and installer
activation remain unresolved. A complete plan cannot contain unresolved authored candidates.
Global inventory also reports recognizable unsupported top-level trees (`.gemini`, `.cursor`,
`.windsurf`, `.antigravity`, `.kiro`, `.continue`) as unresolved directory entries. This is a
bounded recognition list, not an exhaustive harness catalog or a scan of arbitrary repositories.
The planner recognizes static local references in native configuration and common relative
shell/JavaScript imports; it inventories dependencies within selected roots. Dynamic paths,
globs, outside dependencies and declarations still pointing at relocated originals require an
explicit mapping. Relative dependencies are checked in both original and deployed coordinates;
matching source/destination pairs preserve their layout. Absolute references to retired source
locations remain blockers. This is bounded discovery, not execution or a general interpreter.

Symlinks are followed for inventory only within the selected project/source and explicitly
identified roots. Runtime exclusions apply to both the logical path and the canonical path's
role beneath confirmed harness roots, including canonical forms of symlinked roots. Those
canonical role checks do not enlarge the allowed inventory boundary. The spelling of a workspace ancestor, such as `projects`
or `cache`, is not a runtime role. Entry preconditions retain link text, parent identity, target
fingerprints, and inventoried directory listings, including each directory traversed while
searching for nested instructions. A later transaction must replace link entries
without writing through them.

## Source and validation

New source lives in `<selected-directory>/loadout/`; an already initialized root manifest keeps
its existing source root. `source_root` identifies the actual manifest directory for the later
machine-config write. Every artifact category has a source directory. Explicit routes preserve
agent membership, nested instruction paths, rules, commands, skills, module files and relative
support subtrees. Tree routes discover first additions, edits, renames and removals.

Native composite documents split settings, permissions, hooks, plugins and MCP into separately
owned fragments. JSON null/false/empty values and ordered nested keys survive literally. Native
supporting assets remain byte-and-mode exact. Portable permissions are used only after the
proposed TOML is serialized, parsed by `parse_rules`, and rendered with the normal renderer;
complete ordered content must match. Text conversion must also retain the normal deployment
mode. Silent extractor losses fall back to native content, including compact Codex rules,
OpenCode bare commands, conflicting Pi aliases and interleaved Claude permission lists.

Validation writes only a disposable reconstruction directory and starts a fresh isolated Python
process. It parses the actual serialized configuration and runs `render_all`. Global destination
templates are independently resolved against discovery's captured path environment, checked
against the plan, and substituted at the destination boundary with empty scratch locations.
No live destination residuals, resolver caches or undeclared machine source are available. The
comparison covers complete output inventory, required absences, ownership, ordered native
fingerprints, opaque bytes and modes. Normal document outputs use mode `0600`; partial outputs
carry `mode_policy="preserve-destination"`, using the inventoried live mode or `0600` when absent.

Partial source contains only authored fields: `mcpServers` from Claude's runtime registration,
Codex configuration excluding `projects`/`trust`, and Pi settings excluding
`lastChangelogVersion`. The later transaction applies these owned fields into live documents;
runtime fields are neither source nor checkpoint content.

Empty core categories are routed but dormant until their first entry. An originally present
empty instruction remains present. `categories` reports readiness for each agent: `routed`,
`explicit-binding`, or `unsupported`. Module/support filenames are chosen by their consumers,
and templates need explicit selection; new arbitrary targets require an artifact binding, with
a README in the category directory. Existing module/support targets have concrete routes. The
unsupported scope distinctions follow [config.md](config.md) and [servers.md](servers.md),
including the documented Codex project-skill negative and unverified project MCP support.

## Privacy and transaction handoff

Personal filenames and Git-ignored authored inputs remain private. For an ignored symlinked
output, privacy follows the canonical authored source's Git status. Literal credential-shaped
keys, authorization headers and MCP environment values select private source, without exposing
their values in previews or parser errors. This detection cannot prove that arbitrary authored
content is secret-free; the preview explicitly requests checkpoint review. Private contributors
are optional so another checkout can omit personal source. A transaction must protect private
source directories, especially when a copied file's original executable mode must survive.
Every used private category namespace (`loadout/<category>/local/`) carries a directory-level
ignore and a `private_paths` exclusion, so later additions remain private too.

Auth, sessions, history, caches and trust data are excluded before source and checkpoint
selection and left at their original paths. `private_paths` includes logical originals, their
canonical targets and new source; it is an exclusion input for both baseline and final Git
operations. Existing Git history is outside this planner's ownership.

`MigrationPlan` exposes frozen `source_writes`, `generated_writes`, `expected_outputs`,
`required_absences`, `obsolete`, `originals`, `ignores`, `private_paths`, `checkpoint_paths`,
`preconditions`, and `artifact_routes`. Its JSON-serializable `preview()` contains metadata rather than file
contents or fingerprints of secret values. `complete` requires a successful isolated validation
and no issues, or a recognized existing-source no-op. Generated partial content is the authored
document, not a frozen replacement for runtime fields. Transaction preparation must reread and
compare preconditions, preserve foreign fields with the existing deployment boundary, capture
recovery state, and only then perform the planned writes and safe retirements.

`originals` gives every inventoried input and canonical target a path, destination, action and
reason. `retire` entries include redundant selected/unselected copies and symlink entries inside
the selected source directory. `generated` entries are still required output paths; `retain`
entries identify external targets, private/runtime originals and retained dependencies. Tree
categories do not imply retention. Retirements name individual entries, never whole directories.
Destination comparisons use the planned layout below the selected root or home boundary, so a
harness-root symlink that the transaction will replace cannot make its old canonical files look
like future generated outputs. Workspace ancestor aliases still identify the same physical root.
The transaction preserves retained private/runtime entries when replacing a shared ancestor.

## Applying a resolved plan

```python
from loadout.migration_transaction import (
    apply_migration,
    prepare_migration,
    recover_migration,
    resume_migration,
)

prepared = prepare_migration(plan)
preview = prepared.preview()
result = apply_migration(prepared)
```

Preparation is read-only apart from disposable validation/ignore-check directories. It requires
a complete plan, repeats serialized-source reconstruction, freezes complete destination bytes
and modes, and adds receipt, parent, Git privacy, index and ref preconditions. Preview lists the
exact baseline and final staging paths without file contents. Apply rechecks those preconditions;
there is no unresolved-plan or `--yes` bypass in this API. Already initialized source produces
an explicit no-op. A caller may pass `machine_write=SourceWrite(...)` for an explicitly planned
machine registration; that exact write and its preimage participate in recovery, including for
an existing-source no-op. The library does not infer or overwrite a machine configuration path.

Partial JSON/TOML postimages include the existing foreign fields and adopted mode before a link
is removed. Opaque sources retain bytes and modes; private source namespaces have mode `0700`.
Adopted link entries are removed before normal installation. A harness directory link becomes
an ordinary directory containing generated files and links to inventoried retained entries in
the original canonical container. Those links preserve runtime/auth visibility and terminal
link semantics; canonical external targets remain untouched. An uninventoried child, unsafe
ancestor, or retained entry depending on a retired source blocks preparation. Mapped originals
retire individually after their replacement outputs validate.

The initial installation uses `deployment.apply_deployment`, including its normal pending and
complete ownership receipts. Its optional `write` callback lets the transaction durably record
each existing writer operation. Subsequent `sync`/`check` use ordinary native ownership, including
first entries, edits, last-entry deletion, and output drift protection.

## Git checkpoint and final staging

An enclosing repository is reused, including a linked worktree or detached/unborn HEAD. New
repositories require a dedicated directory outside HOME and the filesystem root. For an existing
repository, the alternate baseline index starts from HEAD and adds only eligible adopted input
and topology entries. For a new repository it captures nonignored contents, excluding discovered
private/runtime/canonical namespaces and recognizable security paths. Ordinary application paths
named `projects`, `debug` or `history` remain eligible. The exact path preview is reviewable;
these exclusions do not establish that arbitrary content is secret-free, and existing history
is not rewritten.

A baseline commit is made only when its index differs from HEAD. It uses normal Git identity,
signing and existing hooks; failures preserve the original configuration and identify a recovery
journal. Discovered harness scripts/installers are not run. Symlink descendants are translated
to their Git link entries and eligible canonical files. Ancestor aliases of the selected root
are normalized to the physical repository for checkpoint, staging and ignore paths, including
aliases to a nested directory inside an enclosing repository. Adopted terminal symlinks keep
their own Git entries. Unmerged indexes and ambiguous partially staged adopted files, including
private/generated files affected by final removal, block preflight.
Unrelated staged and unstaged work is preserved.

After a successful checkpoint, only affected entries in the captured original index are refreshed
to the new HEAD, under a guarded index lock. Final staging adds public source, removes generated
and retired/private inputs from the index, and stages only Loadout's own `.gitignore` additions
on top of the existing index version. Unrelated unstaged ignore lines remain unstaged. A new
repository also retains ignore content included in its baseline. Generated files stay on disk
and are ignored. Root-relative literal pathspecs and escaped ignore patterns preserve filename
metacharacters; an individual ignore path containing a newline is rejected before mutation.
There is no final migration commit.

Git privacy is checked again before checkpointing and final staging, including during resume.
The journal preserves the original decisions and fingerprints the applicable `.gitignore` files,
repository excludes, global excludes and effective ignore settings for original, canonical and
new source paths, including the targets of global exclude-file symlinks. A policy change requires
rediscovery; generated-path ignores cannot mask a new privacy rule. Only the transaction's
recorded ignore-file changes are accepted. Git identity
and signing settings may still be repaired after a failed checkpoint.

## Interruption and recovery

The protected journal is `<selected-root>/.loadout-state/migrations/<id>/journal.json`. Its
container is mode `0700`, files are mode `0600`, and its own `*` ignore is installed before
preimages, including before a checkpoint hook can run. Tracked, symlinked, foreign-owned or
public state containers are refused. Completed journals remain private recovery records; they
are not authored configuration or staging inputs.

`MigrationFailure.journal` gives the actionable path. `resume_migration(path)` continues the
frozen operations after checking their state. A checkpoint interrupted after committing is
recognized from its recorded alternate index and parent, and its original-index refresh can
finish without another commit. A retry after identity/hook failure rechecks the original input
and privacy preconditions before committing. The journal also records every generated output's
complete bytes and mode, including outputs requiring no write. Apply and resume validate the
whole output inventory and required absences before retirement and completion, including a
retry after final index replacement. `recover_migration(path)` restores only unchanged transaction
postimages and returns `RecoveryResult.conflicts`; concurrent edits and their preserved
preimages remain available for explicit reconciliation. An intervening Git ref/index change
blocks rollback of the affected transaction. Successful baseline commits are never rolled back.

Filesystem writes, Git refs and the index are separate guarded operations, not one atomic unit.
Power loss or concurrent edits can therefore require explicit recovery. An initialized repository
and successful baseline remain after recovery; no reset, clean, stash deletion, signing bypass,
or broad recursive cleanup is used.
