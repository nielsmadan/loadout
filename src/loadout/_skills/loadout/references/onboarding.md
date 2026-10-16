# Onboarding existing configuration

Start with a read-only preview:

```sh
loadout init project --harness claude --harness codex --dry-run --json
loadout init global /work/dotfiles --harness pi --dry-run --json
```

Scope is a subcommand: `init project` or `init global`. Global source selection defaults to cwd.
Use a dedicated directory, never HOME or the filesystem
root for a new repository. Project discovery finds the enclosing Loadout/Git root from nested
directories. Configured roots establish agents; a shared AGENTS.md or installed binary does not.
Repeat `--harness` when membership needs an explicit selection.

Project init creates canonical sources for configuration with known project destinations. An empty
project starts with `loadout/config.toml` and `loadout/permissions.toml`. Global init creates the
canonical global slice sources it needs, subagents included. Command trees and unsupported
harness-specific files remain where they are. Repeat init preserves existing source layouts.

Init then renders, as the first sync. When the render matches the existing files under loadout's
comparison it writes them and reports an exact reproduction; when it does not, the preview shows
the diff and approval covers overwriting. Do not tell the user to run `sync` after init; run it
after changing the source.

After project init, offer a bundled template: `loadout template add frontend --vendored` (or
`backend`), then `loadout sync`. These packaged templates work offline and provide short
instruction seeds with empty categories, vendored with content-hash provenance. Global scope
cannot activate them. Template instructions, permissions, skills and MCP servers compose through
the project's standard slices.

Init installs no Git hooks. Offer them separately, after init, with
`loadout integrate git-hooks install --root <source-root> [--profile <name>] [--regenerate]`.
Installation validates the staged dependency snapshot at pre-commit; `--regenerate` also runs
guarded sync after checkout/merge. Show preview paths and source/profile, preserving occupied or
shared hooks and reporting the exact integration commands. A clone needs the same command.
Templates must be vendored and staged, required dependencies must be in the repository, and
optional private sources may be
absent. A post-hook failure leaves Git's completed operation in place; resolve the reported
output conflict and retry normal sync, without adding `--force`.

Read the complete JSON `issues`, candidates and dispositions. An incomplete preview exits 2 and
changes nothing. Prompts are available in an interactive terminal; JSON and dry-run never prompt.
Init reads only the live deployment at each harness's known paths. A dotfiles copy in the
selected directory is not read; if the user keeps one, install it first so the live deployment
holds what they want adopted. Each refusal names the file to change; fix it on disk and run init
again. Never edit a harness file just to make a refusal disappear without telling the user.

Before applying, show scope, agents, authored sources, output destinations, retained/private
entries, removals, and the generated files init will untrack (`untrack_paths`). Native document
formatting may change as described in preview notes; opaque bytes and modes remain.

Apply the same resolved arguments with `--yes` after approval, omitting `--dry-run`. Init first
backs up every path it will touch to `$XDG_STATE_HOME/loadout/backups/`, then writes source and
outputs and retires mapped originals. It commits nothing and stages nothing: in a project it only
untracks the generated files it ignores, and it never creates a project repository. A new global
source gets an uncommitted repository; offer the user the commit, never make it unasked.

Global registration points `source` to the actual manifest directory. A conflicting registration
requires `--registration replace` or `--registration keep`; neither resolves source-copy
conflicts. Existing initialized sources are recognized
and left as source; repeat init reports no migration. Registration can still be explicitly changed.
Use normal check/sync for subsequent source edits.

If init fails it restores the backup itself (`"status": "restored"`, exit 1); report the error.
If an earlier init was killed, the next one exits 2 with `"status": "interrupted"` and the backup
path. Ask the user before restoring it:

```sh
loadout init restore --yes --json
```

The same command undoes the latest completed adoption when the user asks for that. A path changed
since that init is kept and listed under `kept` (exit 1); report each one. The backup holds copies
of the user's configuration, so keep it private and never paste its content into the conversation.

## Resolving conflicting copies at init

Init does not refuse when one skill has different copies in two harnesses' skills folders, when
one agent's copies hold different descriptions or instructions, or when a project's `CLAUDE.md`
and `AGENTS.md` disagree. It adopts everything else, adopts nothing of
that item, and lists it under `unadopted` in the JSON (in text: "Not adopted, conflicting copies
of …"): the kind (`skill`, `agent` or `instructions`), the name, and each copy's path with the harnesses it
serves. Every copy stays where it is, and nothing records the item as pending; `check` reports a
left-out skill's folders as `unmanaged` until it is resolved.

1. Read every copy listed.
2. Merge them. Unify incidental differences such as wording, order and whitespace. Keep a
   genuinely harness-specific difference as a `::: <harness>` body section, or, for a skill's
   frontmatter, as a block keyed by the harness name whose indented keys override the shared ones.
3. Write the merged item into the source: `skills/<name>/SKILL.md` or `agents/<name>.md` in the
   global source, or `loadout/skills/<name>/SKILL.md` or `loadout/agents/<name>.md` in a project. Project instructions become
   `loadout/instructions/main.md`, named by `instructions = ["main"]` in `loadout/config.toml`.
4. Sync. It refuses, because the copies hold content loadout never wrote. Show the diff, and run
   `sync --force` only after the user approves replacing them.

## Secrets in the adopted source

Init adopts a file that holds a secret as it is, into the source it would go to anyway, and lists
each source file under `secrets` in the JSON (in text: "Contains a secret: <path>: <reason>"). The
reason names what was found, never the value. The check is a heuristic: it can miss a secret and can
flag a value that is not one.

Tell the user the source now holds those secrets, so its remote must stay private, and offer to move
each one into an environment variable:

- An HTTP MCP server's bearer token: replace the literal `Authorization` header in `mcp.toml` with
  `auth_env_var = "<NAME>"`. Loadout renders the name for each harness: `bearer_token_env_var` for
  Codex, `Bearer ${NAME}` for Claude and Droid, `{env:NAME}` for OpenCode, `bearerTokenEnv` for Pi.
- Any other value: use the reference syntax of the harness that reads it (`${NAME}` in Claude and Pi
  MCP entries, `{env:NAME}` in OpenCode), and only in a source that harness alone reads.
  `mcp.toml`'s `env`, `args` and `headers` reach every harness as written, so a reference there is
  read literally by a harness that does not expand that syntax. When no reference fits, say so and
  leave the value in place.

Ask before each change, and make sure the variable is set where the harness runs before syncing:
an unset variable leaves the server unauthenticated or the setting empty.

## Later changes

The same skill handles later configuration using the configuration reference.
`loadout integrate skill install` vendors this bundled skill into the global source's
`skills/loadout/` and syncs it to every configured agent. It asks before source changes; `--yes`
approves them. Status identifies the source and its agents. Reinstall updates unchanged copies; uninstall removes only owned, unchanged content.
