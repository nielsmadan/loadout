# Factory Droid

Factory Droid is a supported Loadout harness under the name `droid`. This page records the
portable mappings implemented against Droid 0.218.2 and Factory's configuration
documentation.

## Destinations

The global root is `${FACTORY_HOME_OVERRIDE:-~}/.factory`. `FACTORY_HOME_OVERRIDE` denotes the
home beneath which Droid keeps `.factory`; Loadout appends that directory name. Project files
live under `.factory/`, except that project instructions share the repository-root `AGENTS.md`
with Codex, OpenCode, and Pi.

| slice | global | project |
|---|---|---|
| settings and shell permissions | `.factory/settings.json` | `.factory/settings.json` |
| instructions | `.factory/AGENTS.md` | `AGENTS.md` |
| hooks | `.factory/hooks.json` | `.factory/hooks.json` |
| MCP server definitions | `.factory/mcp.json` | `.factory/mcp.json` |
| plugin declarations | `.factory/settings.json` | `.factory/settings.json` |
| skills | `.factory/skills/` | `.factory/skills/` |
| module config | authored path beneath `.factory/` | not in the legacy project preset |

Custom droids, slash commands, and output styles are not Loadout slices.

## Shell permissions

Loadout owns three top-level settings keys and preserves every other setting:

| Loadout decision | Droid setting |
|---|---|
| `allow` | `commandAllowlist` |
| `ask` | `commandDenylist` |
| `deny` | `commandBlocklist` |

The three keys are top-level in the file. Reading the 0.218.2 bundle alone suggests otherwise —
its settings class initialises as `settings = {general: {…}}` and reads
`this.settings.general?.commandAllowlist` — but `general` is the in-memory model, not the file:
a live Droid-written `~/.factory/settings.json` (checked 2026-09-14) carries `commandAllowlist`,
`commandBlocklist`, `sessionDefaultSettings`, `hooks` and `completionSound` all flat, and the
binary reads every one of those through `general?.`. Do not "correct" these keys into a `general`
block on the strength of the bundle.

The mapping follows Droid's own descriptions of the three lists and is not interchangeable:
`commandDenylist` entries "always require confirmation" and a denied command "can still be run
if you explicitly approve it", which is `ask`; `commandBlocklist` entries "can **never** run",
with "no prompt and no way to approve them", which is `deny`. Source: Factory's
[settings reference](https://docs.factory.ai/cli/configuration/settings), read 2026-09-14.

That page also states the resolution order — "Commands that appear in both the allowlist and
denylist default to the denylist behavior. The blocklist always takes precedence over both." —
so the three lists are most-restrictive-wins and Loadout does not pre-resolve an overlap between
them. It states the catch-all in the same paragraph: "Any command that is in none of the lists
falls back to the autonomy level you selected for the session." `[shell] default` is therefore
reported rather than translated, because there is no key to write it to.

### How an entry matches, and what that costs a glob

Verified against the 0.218.2 bundle (`Qgf`), which compiles each list entry to a RegExp:

    let H = T.map((_) => _.replace(/[.*+?^${}()|[\]\\]/g, "\\$&")),
        A = R.anchored ? "^" : "(^|[\\s;&|(`]+)",
        n = (_) => _.includes("/") ? "([\\s;&|)`/]+|$)" : "([\\s;&|)`]+|$)";

Two consequences, and the whole glob story follows from them.

**Metacharacters are literal.** Every regex metacharacter, `*` included, is escaped before the
pattern is built, so a `*` in an entry matches an asterisk. Droid's own shipped denylist relies
on this: it carries `rm -rf /` and `rm -rf /*` as separate entries, because those are two
different commands a person types, not a command and a pattern for it.

**Matching is by whole-word run, not exact string and not anchored prefix.** The entry is wrapped
in a leading `(^|[\s;&|(` ]+)` and a trailing `([\s;&|)` ]+|$)`, so it matches a token run
anywhere in the command line, bounded by whitespace, a separator, or the end. `ls` matches
`ls -la` and not `lsof` — Droid's own builtin rule for it records exactly that, with
`tests: {match: ["ls", "ls -la"], noMatch: ["lsof", "LS"]}`. `rm -rf` matches `rm -rf /foo`, and
also `x && rm -rf /foo`.

So Droid needs **less** than the harnesses whose matcher is a prefix: OpenCode is emitted as both
`pwd` and `pwd *`, while Droid carries the bare entry alone and still catches it after `&&`.

A portable trailing-`*` glob therefore cannot be represented, and the three lists do not fail the
same way when one is dropped:

| list | a dropped glob means | Loadout |
|---|---|---|
| `allow` | a pre-approval is withheld — fails closed | drops it, reports |
| `ask` | the command falls through to the session autonomy level | drops it, reports |
| `deny` | the block ceases to exist, with no fallback | **refuses to render** |

`deny` maps to `commandBlocklist`, the list with "no prompt and no way to approve", so a silently
dropped entry leaves the rendered policy wider than the source asked for. `render_droid` raises
instead, and names the rewrite: drop the trailing `*` and write the bare prefix. `rm -rf` covers
on Droid what `rm -rf *` was meant to, and every other harness already reads a bare entry as
"this command with any arguments".

Droid's command lists carry shell policy only. The `mcp-permissions` slice has no Droid
destination — verified negative against the settings reference (read 2026-09-14), whose only
MCP-approval keys (`mcpPolicy`, `mcpAutonomyOverrides`, `mcpAutonomyUrlOverrides`) are all
marked **Enterprise**. The mechanism therefore exists, but only in the org-managed tier Loadout
does not write, not in the personal `settings.json` this page covers.

`trustedFolders` is different from an authored setting: Droid writes trust timestamps there at
runtime. The global preset preserves that key from the live destination and refuses it in a
settings fragment, so a sync cannot turn machine-local trust history into versioned source.

## Hooks

Hooks use Droid's declarative `{ "hooks": ... }` document without an adapter. The documented
event set used for notices is `PreToolUse`, `PostToolUse`, `UserPromptSubmit`, `Notification`,
`Stop`, `SubagentStop`, `PreCompact`, `SessionStart`, and `SessionEnd`.

Commands may use Droid's `FACTORY_` and `DROID_` environment-variable namespaces. A hook
fragment containing another harness's project variable is refused instead of producing a
command that resolves incorrectly.

## MCP servers

`.factory/mcp.json` has the Claude-compatible `{ "mcpServers": ... }` shape. Stdio entries
carry `command`, `args`, and optional `env`. HTTP entries carry `type = "http"`, `url`, and an
optional `Authorization: Bearer ${ENV_VAR}` header. Loadout writes the variable reference,
never its secret value.

## Plugins

Droid addresses plugins as `<name>@<marketplace>`. The portable plugins fragment contributes
both `enabledPlugins` and `extraKnownMarketplaces` to `settings.json`; settings and permission
keys beside them remain intact. Removing a portable plugin reference removes its enablement on
the next render.

## Extraction and migration

Shell permissions, hooks, MCP definitions, and plugin declarations all have inverse
extractors. Unsupported shell globs remain reported rather than guessed. Global and project
migration recognize `.factory`, split co-owned `settings.json` into its native categories, and
verify isolated reconstruction before changing originals.
