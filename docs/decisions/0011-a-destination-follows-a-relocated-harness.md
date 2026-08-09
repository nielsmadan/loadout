# 0011 — A destination follows a relocated harness

**Status:** accepted (2026-08-09, milestone 5).

## Context

Every `destinations` entry was a literal path — `~/.claude/settings.json`,
`~/.codex/rules/permissions.rules`, `~/.config/opencode/opencode.json`. Four of the five
harnesses let an environment variable move the directory those paths point into:
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR`, and — for OpenCode —
`XDG_CONFIG_HOME`. Antigravity has no such variable.

With any of them set, `sync` wrote to the pre-move path and the harness read from the new one.
The failure is silent in both directions: nothing errors, and `check` then compares against
the same stale path and reports clean. A user would see loadout confirming its output while
the harness ran on whatever was at the real location.

`XDG_CONFIG_HOME` was the sharpest case, because `machine.py` already honours it when locating
loadout's *own* `config.toml`. loadout respected the variable for itself and ignored it for
OpenCode.

## Decision

`destinations` entries expand `${VAR}` and `${VAR:-fallback}` when the manifest is parsed.

```toml
destinations = ["${CLAUDE_CONFIG_DIR:-~/.claude}/CLAUDE.md"]
```

- **A variable that is unset or empty takes the fallback.** Empty-counts-as-unset matches
  `machine_config_path`'s reading of `XDG_CONFIG_HOME`. An empty fallback counts as no
  fallback, so `${VAR:-}` is an error rather than a spelling that walks past the next rule.
- **A variable with no fallback and no value is an error**, not an empty string. Substituting
  nothing would silently write to `/CLAUDE.md` — the same class of quiet wrong-place write
  this ADR exists to close.
- **A resolved destination must be absolute, with no `..`.** A relative one is written under
  the process's working directory, which makes the output depend on where `loadout` was
  invoked from.
- **Anything brace-shaped that this grammar does not cover is an error**, not literal text:
  `${VAR-x}` without the colon, `${VAR:?x}`, a nested `${A:-${B}}`, an unclosed `${`. Left
  alone they become path components, so `sync` creates a directory named after the template
  and `check` then compares against it and reports clean — the failure this ADR closes,
  re-entered through a typo. A bare `$` with no brace is still literal, so a destination
  cannot contain a literal `${...}`.
- **Resolution happens per render, not per parse.** Only the targets the active profile
  selects are resolved, so a profile this machine never runs cannot fail the run over a
  variable it has no reason to set; and `~` resolves in the same step, so the `..` and
  absoluteness checks see the whole path rather than half of one. The target keeps the
  template, which is what `explain` shows and what a later render re-resolves.
- **loadout knows no variable names.** The manifest names them. A sixth harness with its own
  variable needs no code change, and the "never generalise from Claude" rule survives — the
  five variables differ in name and in kind, and only the manifest is in a position to say
  which one a given destination follows.

Global scope only. Project targets are repo-relative and have nothing to relocate.

## Consequences

- Rendering now reads process environment, so `render_*` is no longer a pure function of the
  source tree alone. This does **not** weaken
  [0008](0008-generated-files-carry-no-machine-state.md): that forbids machine state being
  *stamped into* generated content, and no environment value is ever written into a document.
  It is the same shape as [0010](0010-a-machine-config-locates-the-global-source.md), where
  the machine config decides which targets render without entering what they contain.
- **But "the environment changes only the destination, never the bytes" is false where
  `preserve` is used.** `render_permission_target` merges foreign keys read from *the file it
  is about to overwrite* (`emit.py`, `_preserved`), which is deliberate — it is what lets two
  destinations of one target hold different foreign keys. Since the environment now selects
  that file, it selects which foreign keys land in the document. Any target with `preserve`
  has content that depends on the environment. This is a real narrowing of the purity claim
  and the reason it is stated here rather than left implied.
- **`check` cannot see a file the environment has moved away from.** Both sides of the drift
  comparison resolve against today's environment, so after a variable changes, the previously
  written file simply leaves the path set: `sync` does not warn, `check` reports clean, and
  the stale file stays on disk where a shell without the variable will still read it. This is
  the same silent failure the decision closes, relocated from "the harness moved" to "this
  shell differs from that one". It is inherent to resolving at render time; the fix belongs to
  the orphan-tracking sidecar [0008](0008-generated-files-carry-no-machine-state.md) defers,
  which must therefore record resolved destinations, not just templates.
- A destination is now the only place in a manifest where the environment is read, which is
  the right place for it: it is the only field that names a path outside the repository. It
  also means a destination template is a **trusted input**: the grammar puts no allowlist on
  variable names, so a manifest can name any variable, and its value reaches both stdout and
  the filesystem as a directory name. That is acceptable because a manifest could already name
  any absolute path outright — but it is a capability the "loadout knows no variable names"
  rule buys, not a free one.
- Antigravity's `~/.gemini/antigravity-cli/settings.json` stays literal, and should. Writing
  `${SOMETHING:-~/.gemini}` there would imply a variable that does not exist.
