# Onboarding existing configuration

Start with a read-only preview:

```sh
loadout init --dry-run --json
loadout init --project --harness claude --harness codex --dry-run --json
loadout init --global --source /work/dotfiles --harness pi --dry-run --json
```

Explicit scope wins. Existing source may establish scope; otherwise ask project or global.
Global source selection defaults to cwd. Use a dedicated directory, never HOME or the filesystem
root for a new repository. Project discovery finds the enclosing Loadout/Git root from nested
directories. Configured roots establish agents; a shared AGENTS.md or installed binary does not.
Repeat `--harness` when membership needs an explicit selection.

Offer an optional project starter with `--starter none|frontend|backend`; default to `none`.
These packaged templates work offline and provide short instruction seeds with empty categories.
Reuse the same selection in preview and apply. They are vendored with content-hash provenance and
participate in recovery/staging. Global init cannot activate project starters. A native project
receives template prose only on explicit `template_instructions = true` instruction routes;
original body bytes and modes survive. Other populated template categories require explicit
native source edits before selection. Follow the exact template command reported for an already
initialized source, then sync its managed outputs.

Read the complete JSON `issues`, candidates and dispositions. An incomplete preview exits 2 and
changes nothing. Prompts are available in an interactive terminal; JSON and dry-run never prompt.
Resolve only actual ambiguities, preserving unsupported and runtime/private entries. Unknown
installer activation stays unresolved; do not run the discovered code to make it disappear.

Confirm unusual source layouts with repeatable JSON arguments. Paths are literal strings, resolved
against cwd when relative; no shell or environment expressions inside JSON are evaluated.

```sh
loadout init --global --source /work/dotfiles --harness claude \
  --mapping '{"source":"/work/dotfiles/claude","destination":"/home/user/.claude","agents":["claude"]}' \
  --select-source '{"source":"/work/dotfiles/claude","destination":"/home/user/.claude"}' \
  --dry-run --json
```

Mapping keys are `source`, `destination`, `agents`, optionally `kind` (`harness`, `shared`, `file`),
`category`, and `destination_template`. File mappings name a category. Selections choose an
inventoried original for a destination; directory selections cover corresponding relative files.
Do not invent a mapping before confirming its agent and activation path with the user.

Before applying, show scope, agents, authored sources, output destinations, exact Git baseline
paths, retained/private entries, removals and final staging. Explain that credential detection is
heuristic: review checkpoint paths for private information. Existing Git commit hooks can run.
Native document formatting may change as described in preview notes; opaque bytes and modes remain.

Apply the same resolved arguments with `--yes` after approval, omitting `--dry-run`. The transaction
creates a Git repository only when outside an existing one, checkpoints eligible originals, writes
source and outputs, safely retires mapped originals and stages the result. It does not commit the
final migration. Git identity/signing/hook failures need repair, never bypasses.

Global registration points `source` to the actual manifest directory. A conflicting registration
requires `--registration replace` or `--registration keep`; `--force` is the legacy spelling of
replace and does not resolve source-copy conflicts. Existing initialized sources are recognized
and left as source; repeat init reports no migration. Registration can still be explicitly changed.
Use normal check/sync for subsequent source edits.

```sh
loadout init --resume /work/project/.loadout-state/migrations/ID/journal.json --yes
loadout init --recover /work/project/.loadout-state/migrations/ID/journal.json --yes --json
```

Resume continues frozen operations after guards. Recover restores only unchanged postimages and
reports conflicting paths with exit 1; successful baseline commits survive. Keep recovery data
private and never paste its content into the conversation. Report the journal path and status.

The same skill handles later configuration using the configuration reference. `loadout skill
install` installs this bundled skill into actual global skill source routes, deduplicating shared
trees. It asks before source changes; `--yes` approves them. Status identifies every source and its
agents. Reinstall updates unchanged copies; uninstall removes only owned, unchanged content.
