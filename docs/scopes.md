# What loadout is for, and the scope model

## The problem

Four AI coding harnesses each want the same information — which commands may run, what the
agent should know about a project — in four different formats, with four different matchers
and four different file layouts. Maintaining that by hand means four copies drifting apart.
Maintaining it with four ad-hoc scripts means the same, plus the scripts drift from each
other too: the system loadout replaced had two independent copies of its render logic, and
they diverged into a real defect.

loadout renders one source into every harness's format, and refuses to commit when a
generated file has drifted from its source.

## Who runs it

Whoever works in a repo that has adopted it. **Adoption is per-repo and all-or-nothing** —
either everyone touching a repo uses loadout, or nobody does. There is no supported
arrangement where one person regenerates while another hand-edits the same repo's
instructions.

That single fact settles most of the design. Because a generated file never has to serve
someone who cannot regenerate it, generated files never need to be committed, and shared
content can travel as *source* rather than as output.

## Two scopes

### Global scope — shipped

Machine-wide configuration: the rules and instructions that apply to every project. The source
lives in one repo (`~/ac`); generated files are written straight to each harness's home
directory.

**A machine config says where that source is.** `$XDG_CONFIG_HOME/loadout/config.toml`, or
`~/.config/loadout/config.toml`, names the directory holding the global `loadout.toml` and
optionally the profile this machine runs. `loadout sync --global` and `loadout check --global`
read it; `loadout init --global` writes it. Absent means "no global scope on this machine",
which is a legitimate state, not an error — see
[0010](decisions/0010-a-machine-config-locates-the-global-source.md).

Everything the global scope owns lives under one directory it wholly owns, the same shape
project scope already uses:

```text
~/ac/loadout.toml              the manifest — sources, targets, destinations
~/ac/loadout/permissions.toml  global rules
~/ac/loadout/instructions/     instruction fragments
~/ac/loadout/settings/         hand-maintained settings fragments, composed by name
~/ac/loadout/skills/           skill trees, one directory each
```

`sync` writes each document to its **destinations** — the real paths harnesses read, declared
per target in the manifest. There is no staging tree and no symlink layer. Three things follow.
Each destination is rendered on its own, so a target co-owning a live config file carries that
file's own foreign keys forward into it. Because the machine config already names the profile,
only one variant of a profiled document is ever generated, so nothing downstream has to choose
between staged alternatives. And a destination is a *template*, not a fixed path: it may read
`${VAR:-fallback}` so it follows a harness whose config directory has been moved, which makes
the resolved path a third axis of "personal" alongside the source and the profile — see
[0011](decisions/0011-a-destination-follows-a-relocated-harness.md).

**Accepted trade-off:** a fresh clone with loadout not yet installed no longer yields working
config. loadout is installed on every machine and `install.sh` is already a step, so this is
cheap; the alternative was keeping a shadow tree in git to serve a case that does not arise.

Whether global outputs stay committed is deliberately still open. Three of the four reasons
this document once gave for committing them do not survive: two are circular — "the check
treats a missing file as drift" and "a missing file is a dangling symlink" describe
consequences of committing, not arguments for it — and the third names a fallback that exists
only because the files are committed.
[0010](decisions/0010-a-machine-config-locates-the-global-source.md) records the correction.
The one real argument is that permissions are enforcing and the output diff shows blast radius
the source diff hides:
removing one entry from a rule list reads as one line changed in the source, and as 42 deny
rules going from bypassable to enforced in the output. A `loadout diff` reporting that before
writing would serve it better than a shadow tree.

### Project scope — built (permissions, instructions, skills and mcp)

Per-repo configuration, layered on top of global. Two sources per artifact type:

| source | committed? | holds |
|---|---|---|
| project | yes | rules and instructions everyone working on this repo gets |
| personal | **no** | your rules for this repo — machine paths, local tools |

loadout merges the two and writes **generated outputs that are always gitignored** — eight
documents across four harnesses (`claude`, `codex`, `opencode`, `pi`), of which `AGENTS.md` is
one file three of them read, plus a skills directory per harness that has one. Claude's eighth is
`.mcp.json`, the one document the `mcp` slice adds at project scope on top of the seven permissions
and instructions already generated — OpenCode's `mcp` key composes into `opencode.json`, an
existing output, so it adds no file of its own; Codex and Pi have no project `mcp` destination
(see [reference/servers.md](reference/servers.md)). `.codex/config.toml`, in the system this
replaces, turned out to be a one-byte leftover of the old tooling rather than a real output, so
the port does not reproduce it.

Generated project files are never committed, because the merged output contains personal
content — two people would conflict on every regeneration. Shared content reaches other
people through the committed *source*, which works precisely because adoption is
all-or-nothing.

**loadout merges the tiers itself rather than using each harness's native mechanism.** Three
of the four do have one — Claude's `CLAUDE.local.md`, Codex's `AGENTS.override.md`, OpenCode's
`instructions` config key — but they are three different shapes, Pi has none so the merge path
must exist anyway, and the only thing native mechanisms would buy is committed outputs, which
nobody needs given all-or-nothing adoption. Revisit only if that adoption model ever changes.

Consequences:

- `loadout init --harness <name>` (repeatable) scaffolds both sources and adds the personal
  source plus every generated output to `.gitignore`; `loadout harness add <name>` enables one
  more harness on an already-initialised project and extends `.gitignore` the same way. A
  committed `.gitignore` rather than `.git/info/exclude`, which is per-clone and silently fails
  to apply on a fresh checkout. `init` warns when a tracked `CLAUDE.md`, `AGENTS.md` or
  `GEMINI.md` exists and proceeds — that file is now something loadout generates as soon as an
  `instructions` order is declared, so the note tells you to move its content into
  `loadout/instructions/` first. A repo declaring no order generates neither document, which is
  what keeps a permissions-only adopter's hand-written `CLAUDE.md` its own.
- **Per-entry ownership tracking still is not needed, but "loadout is the sole writer of every
  generated file" turned out to be false for two of the outputs.** `opencode.json` and
  `.claude/settings.json` are a harness's own multi-purpose config file — the ported system
  preserved foreign top-level keys in both (`$schema` in the former; `enabledPlugins`,
  `sandbox`, `deny` in the latter), and rendering them from `{}` would silently delete that
  content. loadout now reads the existing output as the renderer's base for those two targets
  and carries forward any key it does not itself generate, an amendment to
  [0001](decisions/0001-render-never-reads-its-own-output.md) rather than ownership tracking:
  the owned subtree is still regenerated unconditionally on every render, so it can never feed
  back — only foreign keys survive. The other three outputs are loadout-only and still render
  from a blank document.

**Both scopes now describe a slice the same way.** `SliceOutput` is one type in two tables —
`GLOBAL_PRESET` and `PROJECT_PRESET` — and the tables stay separate because a project path is
relative to the repo while a global one is a machine template resolved through `${VAR:-…}`
([0011](decisions/0011-a-destination-follows-a-relocated-harness.md)). That is the same reason
project scope carries no `[[source]]` list: a path in a committed file is wrong for everyone who
is not its author. So a project entry sets `output` and never `destination`, and a test pins it.

Two consequences worth stating because they look inconsistent side by side:

- **Project instructions are one order for the repo, not one per harness.** Codex, OpenCode and
  Pi all read a repo-root `AGENTS.md` ([reference/config.md](reference/config.md#instructions)),
  so one path would have to hold three orders. One order makes `CLAUDE.md` and `AGENTS.md`
  byte-identical by construction — `composition.render` takes no agent argument — rather than by
  a check that would pass whenever nothing was rendered.
- **Skills cannot share a path the same way.** `render_skill` takes a harness and varies its
  output by it: `::: <harness>` sections are kept or dropped and `:concept[…]` expands per
  harness. Two harnesses pointed at one skills directory would need different bytes there. Same
  shape of problem, opposite answer, and the reason is in the two renderers' signatures.

  **This is why OpenCode needs `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1`.** It scans Claude's
  skills directories as well as its own, so writing a per-harness flavour to each makes every
  name exist twice — and OpenCode resolves duplicates by race rather than precedence. Setting
  the variable removes the collision instead of hoping the right copy wins. See
  [reference/opencode.md](reference/opencode.md#required-setup-opencode_disable_claude_code_skills).

  **`check` reports when neither variable is set**, because loadout is then producing output whose
  correctness depends on setup it does not control — which is what the notices surface is for. It
  reads the machine that *renders*, and the machine that runs OpenCode is the same one: generated
  project files are never committed and adoption is all-or-nothing, so everyone working in a repo
  syncs it themselves. CI is the exception, and there it costs one advisory line that cannot move
  an exit code. Writing only `.claude/skills/` instead was considered and rejected: it would hand
  OpenCode content rendered for Claude permanently, which is the failure
  [reference/config.md](reference/config.md#instructions) records from the instructions document,
  and it would make `::: opencode` dead code for the harness it names.

  **Codex gets no skills entry**, a verified negative rather than an omission — no
  project-relative skills path exists in the 0.147.0 binary, and its extra-roots mechanism is a
  setting in `.codex/config.toml`, a file loadout does not own.

**Hooks and plugins at project scope are a `PROJECT_PRESET` entry away, not a project.**
`compose_permission_document` is scope-agnostic — it takes a contributor list and knows nothing
about which scope built it — so a value renderer or a generated adapter works there the moment a
preset entry names one. They are deliberately not built: nothing asks for them yet, and a slice
nobody uses is one nobody notices breaking.

**Project scope has no profiles**, and that is a decision rather than an omission:
`render_project` takes no profile argument. A profile selects among machine-wide variants of one
person's setup; a repo's configuration is the same for everyone who checks it out, which is what
`permissions.local.toml` already exists to carve an exception out of.

## Still open

- Antigravity, if it matures. `agy` was dropped as a target — its generated permissions file is
  ignored in headless mode, it has no global skills mechanism and no config-directory variable,
  and its plugin enablement was never established. `docs/reference/antigravity.md` keeps the
  findings and [0012](decisions/0012-antigravity-is-dropped-until-it-matures.md) lists what has
  to become true to re-add it.
- Per-project-type templates — **done.** A template carries four artifact types — permissions,
  instructions, skills, MCP — and each plugged into the same resolution as it shipped, which is
  what "a dimension rather than a milestone" meant. A project declares `templates = ["web"]`, the
  name resolves against a vendored copy and then the global source's sources, and the result
  merges as the lowest tier. `aiconf`, the shell-and-skill mechanism this replaced, was retired
  from `~/ac` on 2026-08-31 (`a4e1228`), closing
  [0007](decisions/0007-loadout-owns-all-agent-configuration.md)'s consequence that it would
  disappear rather than shrink. See
  [reference/templates.md](reference/templates.md) and
  [0014](decisions/0014-a-vendored-template-is-source-not-output.md).
- MCP **server definitions shipped** on 2026-08-24 — `mcp` now renders `<source>/mcp.toml` to six
  of the eight harness/scope destinations; see [reference/servers.md](reference/servers.md).
  **`~/ac` cut over on 2026-08-29** (`48126e0`): the source is `loadout/mcp.toml`, `preserve =
  ["mcp"]` is gone from the manifest, and `mcp/servers.toml` and `mcp/sync.py` are deleted. Not to
  be confused with the
  `mcp-permissions` slice, which renders tool-approval *policy* from `permissions.toml` and is
  complete on all four harnesses — the two shared a word before the rename and produced one wrong
  gap analysis from it. See [reference/config.md](reference/config.md#mcp--retracted-as-a-gap-and-built).
- Skills **shipped** on 2026-08-15 — `skills/sync.py` is deleted and loadout renders all 50 to
  every harness, so this bullet no longer names it.
- Build-time ceilings (`neverallow`) for the wrapper-command bypass described in
  [0005](decisions/0005-a-deny-cannot-carry-exceptions.md). The unresolved part is that a
  ceiling has to reason about command *shape* — "does this command take another command as
  an argument" — rather than string prefixes.
