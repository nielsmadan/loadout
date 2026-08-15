# Harness reference

How each supported agent harness handles permissions, and what `loadout` emits for it.

[config.md](config.md) is the other axis: where each harness keeps each of the seven kinds of
configuration — settings, instructions, permissions, hooks, mcp, plugins, skills — at global
and project scope. Start there when the question is *which file*; start here when it is *what
the matcher does*.

[plugins.md](plugins.md) is one slice across all of them: the portable plugin reference, what
each harness makes of it, and which half of it each one has to drop.

[extraction.md](extraction.md) runs the tables here backwards: what each rendered artifact can
be read back into, which pattern forms have to be collapsed, and what is reported rather than
guessed at.

One file per harness. Each records **what was verified** (with the date and version it
was verified against) separately from **what upstream documents**. When a harness
changes, re-check the verified claims first — they are the ones this project's output
depends on, and the ones a docs page will not tell you have changed.

**Antigravity is in these tables but is not a target.** loadout emits nothing for it — see
[0012](../decisions/0012-antigravity-is-dropped-until-it-matures.md). Its rows are kept because
the findings were expensive to establish and re-adding support depends on them.

| | [Claude](claude-code.md) | [Codex](codex.md) | [Antigravity](antigravity.md) | [OpenCode](opencode.md) | [Pi](pi.md) |
|---|---|---|---|---|---|
| resolution | deny → ask → allow | most-restrictive | deny > ask > allow | last match | last match |
| specificity affects order | no | no | no | n/a | n/a |
| glob patterns | yes | **no** | **no** | yes | yes (minimatch) |
| bare matches with-args | yes, via `:*` | yes (prefix) | yes (prefix) | **no** | **no** |
| permissions in own file | no | **yes** | no | no | **yes** |
| emission order matters | no | no | no | **yes** | **yes** |

## Cross-cutting rules

### Order-independent vs last-match

Claude, Codex and Antigravity resolve by decision priority, so emission order carries no
meaning. OpenCode and Pi take the last matching rule, so **denies must be emitted after
the allows they refine**. Both renderers do this, and Pi additionally deletes and
reinserts a key to move it to the end of the map when a later category overwrites an
earlier one.

This is why `dedupe()` is order-preserving and never uses `set()`: on two of five
harnesses, order is semantic.

### Globs

A source entry ending in `*` is a glob. Claude, OpenCode and Pi keep it literal — their
matchers understand `*`. **Codex and Antigravity cannot express it**, so glob entries are
skipped for those two and fall through to the harness's runtime approval prompt. Codex's
docs are explicit: patterns are "literal strings or unions of literals". Antigravity's
docs only ever show literal command strings.

Skipping is fail-closed — the command prompts rather than being silently allowed.

### Bare vs with-arguments

Three matchers prefix-match, so `pwd` matches `pwd --help` for free. Two do not:

- **Claude** needs the `:*` suffix, which matches both forms (verified — see
  [claude-code.md](claude-code.md)).
- **OpenCode and Pi** need **both** `<entry>` and `<entry> *` emitted, because their
  matchers treat `foo *` as not matching a bare `foo`.

Emitting only one form on OpenCode or Pi produces a rule that silently covers half of
what it appears to. This is a live bug in `~/ac/permissions/manage.py`, whose local-scope
renderer emits only the `<entry> *` form.

### The wrapper-command bypass

**Any allowlisted command that accepts another command as an argument voids every deny
rule on a positionally-matching harness.** The matcher sees `["bash","-lc","touch
forbidden"]`, which never matches a deny on `["touch","forbidden"]`.

Verified live on 2026-08-01: `prefix_rule(pattern = ["env"], decision = "allow")` made
all 42 deny rules bypassable on Codex. `env touch forbidden` created the file. Removed
from `[shell] allow` the same day; `printenv` covers the legitimate read-only use.

| probe | Claude | Codex |
|---|---|---|
| `bash -lc '<denied>'`, wrapper not allowlisted | denied | denied (prompts) |
| `bash -lc '<denied>'`, wrapper allowlisted | **bypass** | **bypass** |
| `env <denied>` | denied | **bypass** |

Codex normalises one level — its own `/bin/zsh -lc` wrapper — but a nested explicit
wrapper is not normalised.

Still unresolved in `~/ac`, in the same class as the removed `env`: `find` (bare, and
`-exec`), `git rebase` (`-x`), and `docker exec cc-workbench`. A deny rule structurally
cannot fix this — the intended mechanism is a build-time `neverallow` ceiling that
refuses to emit, which is milestone 4.

### Relocating the config directory

Four of the five let an environment variable move the directory `loadout` writes into.
The variable differs in name *and in kind* — this is the case the "never generalise from
Claude" rule exists for.

| harness | variable | what it moves |
|---|---|---|
| Claude | `CLAUDE_CONFIG_DIR` | all of `~/.claude` — `settings.json`, `CLAUDE.md`, `ide/`, `teams/` — and `~/.claude.json` with it |
| Codex | `CODEX_HOME` | `~/.codex`, so `rules/` moves too |
| Pi | `PI_CODING_AGENT_DIR` | `~/.pi/agent`, and `extensions/` under it |
| OpenCode | `XDG_CONFIG_HOME` | the global config dir: `(XDG_CONFIG_HOME ?? ~/.config) / "opencode"` |
| Antigravity | **none** | `~/.gemini/antigravity-cli/settings.json` is built from `$HOME` and nothing else |

**Verified 2026-08-09** by inspecting the installed binaries: Claude Code **2.1.226**
(`function bcc(){return process.env.CLAUDE_CONFIG_DIR}`, feeding the `Hn()` used for every
`~/.claude` path), Codex **0.147.0** via 60 `CODEX_HOME` references including `"CODEX_HOME
points to "`, Pi **0.84.1** via `getAgentDir()` in its shipped source maps plus `extensions:
join(globalBaseDir, "extensions")`, OpenCode **1.18.15** via `globalConfigPath`. The
Antigravity negative is `agy` **1.1.11**, a scan of every `AGY_*` / `ANTIGRAVITY_*` /
`GEMINI_*` name in the 170 MB binary; the only config-dir-shaped one,
`ANTIGRAVITY_EXECUTABLE_DATA_DIR`, is the editor's data dir, not the CLI's settings path.

**OpenCode has two decoys.** `OPENCODE_CONFIG` names a config *file*, and
`OPENCODE_CONFIG_DIR` does **not** relocate anything — it adds a further `.opencode`-shaped
directory, loaded after the global config, that shadows it. Only `XDG_CONFIG_HOME` moves the
file `loadout` writes.

A manifest destination follows these with `${VAR:-fallback}` — see the schema in the
[README](../../README.md#loadouttoml). Nothing in `loadout` knows these variable names; the
manifest does.

## Upstream documentation

Where each harness documents its permission surface. Check these when output stops
matching what a harness actually enforces.

| harness | permissions | config reference |
|---|---|---|
| Claude | https://code.claude.com/docs/en/iam | https://code.claude.com/docs/en/settings |
| Codex | https://developers.openai.com/codex/agent-approvals-security | https://developers.openai.com/codex/config-reference |
| Codex rules | https://developers.openai.com/codex/rules | |
| OpenCode | https://opencode.ai/docs/permissions/ | https://opencode.ai/docs/config/ |
| Antigravity | https://antigravity.google/docs/cli-features | |
| Pi | `@gotgenes/pi-permission-system` package docs | |

Upstream churn is the dominant ongoing cost: Claude's `settings.json` took roughly 164
schema-affecting changes in 15 months. Codex ships two mutually exclusive permission
schemas. OpenCode v2 renames nearly every key, including one silent boolean inversion.
