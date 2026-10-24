# Migration

`loadout init project` and `loadout init global` adopt configuration only when Loadout has a
known slice destination for it.

## What init adopts

- permissions and MCP tool policy
- instructions at the harness's standard instruction path
- settings owned by a supported settings slice
- hook and plugin declarations
- MCP server definitions
- skills: each folder holding a `SKILL.md`. A loose file or a folder without one beside them, such
  as a `README.md` or a `shared/` folder, is no skill and stays in place; `check` reports it only
  where a harness reading the folder would load it
  ([config.md](config.md#what-a-harness-loads-from-a-skills-or-agents-folder))
- subagents: each harness's top-level agent files become one source agent per name, with a
  per-harness `model` map, and an `effort` word or map, recording what differed. A file adoption
  cannot read (such as a Codex agent holding a table) or whose name differs from its file name
  stays in place, named in a preview note. Copies whose description or instructions differ are
  left out (below).
- global module configuration and support files

Project command trees and other harness-specific files are left in place. They keep
working because init neither moves them nor adds their paths to `.gitignore`.

## What discovery reads

Discovery is an allowlist. Under each harness root it reads only the paths below, and everything
else there is left alone unread: never mapped, never reported, never a reason to stop. A `.DS_Store`
is skipped wherever it appears, including inside a listed tree. See
[0024](../decisions/0024-discovery-reads-an-allowlist.md). The table is `MANAGED_PATHS` in
`src/loadout/discovery.py`; a path ending in `/` is a tree.

Global scope:

| root | paths read |
|---|---|
| `${CLAUDE_CONFIG_DIR:-~/.claude}` | `CLAUDE.md`, `settings.json`, `mcp-permissions.json`, `skills/`, `agents/`, `hooks/`, `rules/`, `scripts/` |
| `${CLAUDE_CONFIG_DIR:-~}/.claude.json` | the file itself (only `mcpServers` is adopted) |
| `${CODEX_HOME:-~/.codex}` | `AGENTS.md`, `AGENTS.override.md`, `config.toml`, `hooks.json`, `rules/`, `skills/`, `agents/` |
| `${FACTORY_HOME_OVERRIDE:-~}/.factory` | `AGENTS.md`, `settings.json`, `hooks.json`, `mcp.json`, `skills/`, `droids/`, `plugins/`, `hooks/`, `rules/`, `scripts/` |
| `${XDG_CONFIG_HOME:-~/.config}/opencode`, or `$OPENCODE_CONFIG_DIR` | `AGENTS.md`, `opencode.json`, `opencode.jsonc`, `skills/`, `agents/`, `plugins/`, `plugin/`, `hooks/`, `rules/`, `scripts/` |
| `$OPENCODE_CONFIG` | the file itself |
| `${PI_CODING_AGENT_DIR:-~/.pi/agent}` | `AGENTS.md`, `settings.json`, `mcp.json`, `extensions/pi-permission-system/config.json`, `skills/`, `agents/`, `plugins/`, `hooks/`, `rules/`, `scripts/` |
| `${XDG_CONFIG_HOME:-~/.config}/mcp/mcp.json` | the file itself (Pi) |
| `~/.agents` | `mcp.json`, `mcp/mcp.json` (Pi); `skills/` is left in place, see below |

OpenCode's `plugin/` is read because OpenCode scans `"{plugin,plugins}/*.{ts,js}"` in every config
directory (`packages/opencode/src/config/plugin.ts:21`, 1.19.0); its docs name only `plugins/`.
`providers/` and `profiles/` are read for no harness. Verified negative: neither name, quoted or as a
path segment, is a config directory in the Claude Code 2.1.299 binary, the Droid 0.240.1 binary, Pi
1.2.0's `dist` or OpenCode 1.19.0's source (whose config scans are `agent`, `mode`, `plugin`,
`command` and skills); Droid's one `"profiles"` is `~/.factory-dev/profiles`. Nor is `plugin/` read by
Claude, Droid or Pi, by the same searches.

Project scope, relative to the repository root:

| root | paths read |
|---|---|
| `.claude` | `settings.json`, `mcp-permissions.json`, `skills/`, `agents/` |
| `.codex` | `rules/`, `agents/` |
| `.factory` | `settings.json`, `hooks.json`, `mcp.json`, `skills/`, `droids/` |
| `.opencode` | `skills/`, `agents/` |
| `.pi` | `extensions/pi-permission-system/config.json`, `skills/`, `agents/` |
| `.agents` | `skills/`, for Codex only, see below |
| repository | `opencode.json`, `opencode.jsonc`, `.mcp.json`, and every `AGENTS.md`, `AGENTS.override.md`, `CLAUDE.md`, `CLAUDE.local.md` and `opencode.md` outside the harness roots |

Codex, OpenCode and Pi all read `.agents/skills`, but init adopts a shared file only for a harness
whose loadout output is that folder, since anything else renders to another one and the
reconstruction would find the original missing. That is Codex in project scope, whose project
skills render to `.agents/skills`, and no harness in global scope, where every skill renders to
the harness's own folder (`SHARED_SKILL_WRITERS` in `discovery.py`, derived from the presets).
So `~/.agents/skills` stays where it is and the harnesses that read it keep reading it there, and a
project's `.agents/skills` is adopted for Codex alone; OpenCode and Pi go on reading the copy
loadout renders there.

One more kind of read goes beyond the table. A file that an adopted document references, such as
a hook script named by a command, is read when it lies under a selected root. A reference that is
missing, outside the roots, dynamic (`$VAR/…`), a glob or a directory is left as written: the
adopted document keeps pointing at the original path, which keeps working because loadout does not
move it.

The selected directory is never scanned. A dotfiles copy there (`claude/`, `.claude/`, an installer,
a `Makefile`) is not read; init always adopts the live deployment. Runtime state inside a listed
tree, such as `node_modules/` or `.env`, is still excluded, and so is what a harness writes into
its own skills or agents folder, each only in that folder: Claude Code's `skills/synced`
([claude-code.md](claude-code.md#account-skills-skillssynced)) and Droid's two mission droids
([droid.md](droid.md#mission-droids)).

## What init refuses

Discovery has no option to point it at another layout, so every refusal names the file to change;
fix it on disk and run init again. An incomplete preview exits 2 and changes nothing.

| code | when |
|---|---|
| `harnesses-required` | no harness was given and none has configuration at a known path |
| `symlink-target`, `symlink-cycle` | a link at a read path is missing its target or loops |
| `external-symlink` | in project scope, a harness root links outside the repository; its content would become committed source. There is no override: share it as a loadout template, or replace the link with a real copy (`cp -RL`) and run init again ([0026](../decisions/0026-a-project-root-linked-outside-the-repository-has-no-override.md)) |
| `git-link` | in project scope, a link points into the repository's `.git`, which holds credentials such as a token in `.git/config` and runs hooks; init reports it and never reads through it |
| `linked-folder`, `linked-file` | in project scope, a harness folder that is itself a link inside the repository, such as `.claude -> config/claude`, or a link inside one, such as `.claude/skills/x/scripts -> ../../../tools`; project sync never writes through one, so init reports it instead of walking it. Replace it with a real copy (`cp -RL`) and run init again |
| `opencode-both-formats` | `opencode.json` and `opencode.jsonc` side by side: OpenCode reads both and merges them, the `.jsonc`'s keys winning; combine them into one file |
| `invalid-native-document` | a document at a read path does not parse |
| `dependency-activation` | an adopted declaration references a de-linked original instead of the copy loadout deploys |
| `source-composition` | two originals would write different content to one source file outside the cases below; make them identical or remove one. Also a Codex rules file holding a statement adoption cannot carry, quoted with its line; see [extraction](extraction.md#a-hand-written-codex-rules-file) |
| `source-exists`, `initialized-source`, `initialized-conflict` | the source directory already holds conflicting loadout source |
| `registration-conflict` | global: another source is registered; choose `--registration` |
| `reconstruction-failed` | the serialized source does not reproduce the originals |

## What init leaves out

Three cases are not refusals. A skill whose copies in two harnesses' skills folders hold different
bytes or modes for the same file, an agent whose copies hold different descriptions or
instructions (a different model or effort is not a difference: it becomes a per-harness map), and
a project whose `CLAUDE.md` and `AGENTS.md` hold different content, are each **left out**: init adopts everything else, adopts nothing of that item, and
leaves every copy where it is. The preview and `--json` (`unadopted`, in the preview and in the
result) name the item, its kind (`skill`, `agent` or `instructions`), and each copy's path with the
harnesses it serves. Merging is judgement, so it belongs to the bundled `/loadout` skill: it writes
the merged item into the source, and the next `sync` refuses because the copies hold content
loadout never wrote; with the diff reviewed, `sync --force` replaces them. Until then `check`
reports a left-out skill's folders, or agent's files, as `unmanaged`. Nothing records the item as pending.

Project scope keeps one instruction source, so a disagreement there is one item, `main`. A
`CLAUDE.md` that is a symlink to `AGENTS.md`, or whose whole content is `@AGENTS.md` (or
`@./AGENTS.md`), surrounded by whitespace and optionally preceded by loadout's banner, is the same
document as `AGENTS.md`; anything more in it is a disagreement. In global scope each harness keeps
its own, so `~/.claude/CLAUDE.md` and `~/.codex/AGENTS.md` may differ.

## Permissions and MCP servers from several harnesses

Permissions and MCP servers are keys in documents loadout co-owns, so leaving one out would render
it empty and delete the harness's rules. At both scopes init merges them instead. The harnesses'
permission entries are unioned into one `permissions.toml`, and an entry two harnesses give
different verdicts takes the stricter one (deny > ask > allow), so a deny from any harness reaches
every harness, and so does an allow nothing contradicts. MCP servers combine by name into one
`mcp.toml`; a name two harnesses define differently is a refusal naming the server.

## Project settings

`.claude/settings.json`, `.factory/settings.json` and `opencode.json` are shared with the slices that
write into them. Init adopts every top-level key no slice writes into `loadout/settings/claude.json`,
`droid.json` or `opencode.json`, and sync renders the file from that source plus the slices. Which
keys a slice writes is read from its renderer, not listed by hand. A file Git tracks is adopted into
committed source even when it holds a secret, since its content is already in the repository's
history. An untracked or ignored one is private: its original stays in place and its settings source
is written private and gitignored. An untracked `opencode.jsonc` is still deleted once its keys are
in that private source, since OpenCode would otherwise read both files. Every generated project file init lists in
`.gitignore` is also removed from the index, so a clone renders it rather than checking out a stale
copy. Droid's and Pi's `settings.json` count as partly runtime state only at global scope.

## `opencode.jsonc`

Init adopts `opencode.jsonc` as though it were `opencode.json`: comments and trailing commas are
dropped, and the preview says *"comments in opencode.jsonc will be lost"*. Approving the preview
writes `opencode.json` and deletes the `.jsonc`, since OpenCode would otherwise read both. At project
scope its keys loadout does not own, such as `theme`, go into `loadout/settings/opencode.json` like
any other project settings. OpenCode 1.19.0 loads `opencode.json` and then
`opencode.jsonc` from the same directory and merges them (`packages/opencode/src/config/config.ts`
globally, `config/paths.ts` in a project), so init refuses when both exist rather than pick one.

## Source shape

Project init writes `loadout/config.toml` plus the canonical project slice sources it found.
Global init writes `loadout/loadout.toml`, canonical global slice sources, and registers that
directory in the machine config. Neither scope writes `artifacts.toml`.

Existing generated destinations are reconstructed in isolation before approval.

## What init keeps private

An original is adopted privately, into a gitignored source file that stays on this machine, when
Git ignores it or its name holds `.local`. At project scope a co-owned settings file Git does not
track is private too, since it may hold something personal, while one Git tracks is already in the
team's history and goes into committed source. Everything else goes into the source it would go to
anyway, in either scope.

What a file contains does not change where it goes ([0030](../decisions/0030-adoption-rulings-recorded.md)): a secret stays in the source, adopted
as it is, and init warns about it. The preview lists each committable source file that
looks like it holds one, as `Contains a secret: <path>: <reason>`, `--json` lists the same under
`secrets` (`path`, `reason`), and the closing message says the source's remote should stay private
and that the `/loadout` skill can move a secret into an environment variable. Where it goes does
not change, but who can read it does: a source file holding a detected secret is written
owner-only (group and other bits cleared, so 0600 for a 0644 original), and so is anything adopted
from an original only its owner could read, such as `mcp.toml` from a 0600 `~/.claude.json`. A
skill file with a secret deploys from that source, so its generated copy is owner-only too. The
reason names what
was found, never the value. A file holding such a value is never diffed, in the preview or in any
diff loadout prints, on a terminal or in `sync-abort.diff`: one line names the file, counts the
lines that differ and gives the reason, `notes.md: 2 line(s) differ; diff not shown because the
file holds a secret (a value labelled as a secret) — read the file directly`. The check is made
inside `diffs.diff_lines`, so no printed diff can skip it, on either side of the diff, and it reads
the whole of both texts, whatever their size or bytes, since a diff would show them in full. Nothing
is hidden: a change anywhere in the file, inside the secret or beside it, is counted, so read the
file on disk and the source directly to see it (see
[0032](../decisions/0032-a-file-holding-a-secret-is-never-diffed.md)). Where a label's value ends
decides whether it is a reference, a placeholder or a literal: to its closing quote or,
unquoted, to the end of its line after `key: value` or `key = value`, a ` #` or ` //` comment and
trailing whitespace aside, and after a bare `KEY=value` to the first whitespace, as a shell reads
it; a YAML block scalar's value (`password: |`, with its chomping and indentation indicators in
either order) is its indented body, and after an authorization label a one-word scheme (`Token`,
`Digest`, `ApiKey`) is skipped. One vocabulary names a secret wherever it sits
(`SECRET_NAMES` in `credentials.py`): a key, a flag or a `name = value` label whose last words are
token, API key, secret, password, passphrase, pass, credential(s), authorization, auth, access
key, private key, secret key, client secret, signing key or PAT, or a provider's name followed by
key (`OPENAI_KEY`, `stripeKey`), so `apiKey`, `X-API-KEY`, `--api-key` and `api_key =` all count,
and `--api-key-file`, `max_tokens` or `auth_env_var` do not. A file looks like it holds a secret when it has:

- such a key, or such a label in text, holding a literal value;
- a literal value under `env`, `environment`, `headers` or `http_headers`, other than a number, a
  switch such as `true` or `on`, a path, or a name that never holds a secret (`PATH`, `*_ENV`,
  `DISABLE_*`, `Content-Type` and the like), read from such an object's text when a JSON
  document does not parse;
- the value after, or given inline to, such a flag (`--token`, `--api-key`, `--auth`), in an
  argument list or a command line, in any file, a Codex `.rules` pattern's `"--api-key", "…"`
  included; a value starting with `-` is the next flag;
- a value shaped like a secret anywhere in the file: a PEM private key; a GitHub (`ghp_`,
  `github_pat_` and kin), GitLab, npm, OpenAI/Anthropic `sk-`, Stripe `sk_live_`/`rk_test_` and
  kin, Hugging Face `hf_`, AWS `AKIA`/`ASIA`, Google `AIza`, Slack or JWT token; a Django
  `django-insecure-` key; a Slack webhook URL; credentials or a long token in a URL; a key in a URL query; an
  `Authorization: Basic` credential; a user:password given to an HTTP client with `-u` or `--user`
  within 256 characters of the command's name; or an
  `aws_secret_access_key`.

A reference such as `$API_KEY`, `${API_KEY}`, `{env:API_KEY}` or `$env:API_KEY`, a number, a
switch, a path, and a documentation placeholder ending in `EXAMPLE` or made of x's after its prefix
(`AKIAXXXXXXXXXXXXXXXX`), is not a secret. A binary file (a NUL in its first 8 KiB) is not
scanned, and only the first MiB of any other file is; neither limit applies to the check before a
diff. The check is a heuristic: it can miss a
secret and can flag a value that is not one. A private original is listed as
`Private (stays on this machine): <path>: <reason>`, with the reason in `--json` as
`private_reason`: its name, Git ignoring it, or Git not tracking it.

A Codex MCP server's literal `http_headers` go into `mcp.toml` as `headers` and render back into
`config.toml`; a token among them is reported like any other secret. Keys the server source cannot
hold, such as `env_http_headers`, are named in the preview as not adopted: the next sync writes the
server without them.

## Convert or put everything back

Before its first write, init records a before-image of every path it will touch in
`$XDG_STATE_HOME/loadout/backups/<timestamp>/` (`~/.local/state` when unset): the source it writes,
the originals it retires or de-links, `.gitignore`, the machine registration, the first render's
outputs, the write record, a new source repository's `.git`, and the Git index when it untracks
generated files. Each missing ancestor a write could create is recorded as absent. Every path is
recorded in one spelling: beneath a harness root it replaces it stays at the harness path, and
anywhere else its parent is resolved, so a link above a harness root (written through, ADR 0023) is
followed to where the file really is. Beneath a link init replaces, nothing more is recorded:
putting the link back restores everything under it. Each before-image's content is its own file
under `images/`, named by its SHA-256, and is checked against that digest before anything is put
back; a damaged or missing one refuses with nothing restored. An `in-progress` marker is written
last, so a marker always has a complete manifest beside it.

The backup lasts one run:

- **A success deletes it.** Nothing remains to undo the adoption with; the source is uncommitted,
  so Git shows what init wrote.
- **A failure puts every path back** straight away, whatever stopped the run, Ctrl-C included, and
  deletes the backup. What init created goes first, deepest first: a file, or a folder with
  everything in it, such as a new source repository or a link init replaced with a directory. Then
  what it replaced comes back, shallowest first: file bytes and modes, links, folders and the Git
  index. This runs in the same process right after the failure, so nothing is checked for later
  edits. A write killed before its rename leaves a temporary file beside its target, named
  `.loadout-tmp-` and eight random characters; a file of exactly that shape beside a path in the
  backup is removed, and any other file, a `.loadout-notes.md` included, stays. Init exits 1,
  with `"status": "restored"` under `--json`. If putting back fails too, the backup stays marked
  and init exits 1 with `"status": "interrupted"`.
- **A run killed part way** leaves the marker. The next `init`, of any scope or root, puts that
  backup back before anything else: at a terminal it asks first, and says that edits made since
  to those paths are lost; under `--json`, `--dry-run` or without a terminal it exits 2 with
  `"status": "interrupted"` and changes nothing. A backup written by another version of loadout is
  refused, naming the directory to delete.

After a failure that was put back, and when the plan cannot complete (exit 2), init recommends
doing the conversion with the bundled `/loadout` skill. At a terminal it asks whether to copy the
skill into the user skills folder of each harness being converted, and on yes runs
`loadout integrate skill copy` for them: a plain copy, outside any source, that refuses rather than
overwrite a differing `loadout` folder. Without a terminal, or under `--json` (as
`recommendation` and `install_command`), it gives the command instead. After Ctrl-C it recommends
the skill without asking. A refusal whose every issue is a choice an option makes
(`harnesses-required`, `scope-required`, `registration-conflict`) names that option instead.

Init commits nothing and stages nothing. In a project it never creates a repository, and the only
index change is removing the files it adds to `.gitignore`. A new global source gets its own
repository, uncommitted. A before-image keeps content, mode and kind, not modification times,
extended attributes, ownership or hard links. See
[0033](../decisions/0033-init-converts-or-puts-everything-back.md).

When reading an existing permission document loses something, such as an entry a trailing `*`
shadows or a Codex `justification`, the preview names it, file first, before anything is
written. What each harness can lose is listed in [extraction](extraction.md#what-each-renderer-loses).

A Codex rules file other than `permissions.rules`, such as the approvals Codex keeps in
`default.rules`, is adopted and then deleted, since Codex reads every file in its rules folder.
The preview lists it as `retire`. It and a global `opencode.jsonc` are the originals init deletes
outside the project or source root, each inside the harness root init read.

## Symlinked deployments

A global harness root that is a symlink, such as `~/.claude` pointing into a dotfiles repo, is
adopted, and so are stow-style per-file links, inside it or inside a real harness root such as a
`~/.codex/config.toml` linking into dotfiles. Adoption **de-links**
them; see [0023](../decisions/0023-adoption-de-links-a-symlinked-deployment.md):

- loadout's outputs become real files at the harness path, each starting as the file it was and
  then rendered the way `sync` renders it. A document loadout shares with the harness, such as
  Codex's `config.toml` or Pi's `settings.json`, keeps every key loadout does not own, such as
  `model` or `lastChangelogVersion`;
- every entry loadout does not manage becomes a link back into the original target, so runtime
  state, credentials, commands and subagents keep working. A directory loadout writes nothing into,
  such as `commands/`, links back whole, so a file added to it later appears;
- the target's bytes are not changed. Its copies of the adopted files are simply no longer read.

The harness root is a real directory afterwards, so an entry added later directly in the target, or
inside a directory loadout writes into such as `skills/`, does not appear at the harness path.

The preview lists each link as `Replace symlink: <link> -> <target>`, and `--json` lists it under
`delinked`. After applying, init says what it did:

    ~/.claude was a symlink into ~/dotfiles/claude. Loadout's files are now real files here;
    everything else still links into ~/dotfiles/claude. ~/dotfiles/claude's copies of CLAUDE.md,
    settings.json are no longer used. Entries added later to ~/dotfiles/claude's top level, or
    inside skills/, will not appear here.

Only links at or below a harness root are deployment links. A link above one — macOS's
`/tmp` → `/private/tmp`, a symlinked home directory, `~/.config` for OpenCode — is resolved, never
replaced, and loadout writes through it. A `~/.config` that links into a dotfiles repo therefore
receives OpenCode's generated files in that repo, and loadout's own machine config and write
records with them. The preview says so in one line per link:

    ~/.config is a symlink to ~/dotfiles/config; loadout writes through it, so the files it writes
    under ~/.config land in ~/dotfiles/config.

`sync` writes through the same way and prints nothing about it. A file that already exists in the
target and differs from what loadout last wrote is still refused as modified outside loadout.

Still refused: a symlink cycle, and in project scope a harness root linked outside the repository,
whose content would otherwise become committed project source. That refusal has no override; it
names the link's target and the two remedies, a loadout template or a real copy made with `cp -RL`.
In project scope init also refuses each link project sync would refuse: any link into `.git`, a
harness folder that is a link, and any link inside one. A global harness root that is a link is
still de-linked ([0023](../decisions/0023-adoption-de-links-a-symlinked-deployment.md)). The preview checks its outputs against the project's links before
it copies a single original into its scratch render, and a refusal there names the project's own
paths.

## Older adopted repositories

Nothing using artifact routes was published. Re-initialise repositories adopted by a route-based
development build: move command and subagent files from the Loadout source tree back to their
harness paths, remove `artifacts` and `presets` from project configuration, then run init again.

Sync appends current generated destinations to `.gitignore`. It leaves stale entries from older
versions alone because Loadout cannot distinguish its old entries from user-authored policy.

## Known limitation

Global `module-config` carries hook support files for harnesses with known global roots. Project
scope has no `module-config` destination. A Droid project hook that names a support script must
therefore keep that script at its harness path; init leaves it there.
