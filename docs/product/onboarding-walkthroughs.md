# Onboarding — target flow

What `loadout init global` and `loadout init project` **should** do. This is a design target, not a description of current
behaviour. Written 2026-09-19 during the 1.0 surface review; implementation follows, and once it
lands this document describes what is.

Commands use the ruled syntax `loadout init global` / `loadout init project` (S-03).

## Who is onboarding

People who already have agent configuration and feel the pain of keeping it consistent across
harnesses. **Nobody arrives empty-handed.** A flow designed around an empty `HOME` is designed
around a case that does not occur.

Most start with global scope, because that is where duplication hurts first.

## The governing principle

**Loadout has rules about what it takes over. It has no rules about what it leaves.**

Extraction looks only for the artefacts loadout manages, at the paths each harness uses. Anything
else in those directories — OS cruft, harness runtime state, files loadout has no representation
for — is simply not seen. It is never mapped, never reported as a problem, never a reason to stop.

This is an allowlist, and it is what makes onboarding possible against a real configuration
directory, which is full of things that are not configuration.

## One case, not two

It does not matter whether `~/.claude` is a real directory, a symlink into a dotfiles repo, or
assembled by stow or chezmoi. Extraction reads **content** at known paths.

The distinction would only matter if loadout intended to write *back* to that location and had to
decide whether the link or its target was the source of truth. It does not: the loadout source is
somewhere the user names, and rendering targets the harness paths afterwards.

So there is no symlink question, and no "is this already version controlled" question.

---

## The flow

### 1. Ask where the loadout source should live

A directory the user names. It may be an existing repository, a new one, or a plain folder. Its
current contents are irrelevant — it is a destination, not evidence.

### 2. Detect harnesses, propose, confirm

The only detection that matters, alongside "is loadout already configured on this machine".
Present what was found, let the user add or remove, confirm.

### 3. If OpenCode is configured and skills exist — resolve the duplicate scan

OpenCode scans six locations for skills: its own two, Claude's two, and the `.agents/` pair. Since
loadout writes per-harness renderings, every skill name exists in more than one scanned location,
and **OpenCode resolves duplicates by race** — upstream reads every discovered `SKILL.md` with
`concurrency: "unbounded"` and the last write wins. It logs `"duplicate skill name"` at a level no
normal session shows.

The fix is one line:

```sh
export OPENCODE_DISABLE_CLAUDE_CODE_SKILLS=1
```

Explain it in a sentence, and **offer to write it to the shell rc**. This is the one environment
variable in the design that earns its place: it is a genuine setup requirement for a common
harness pair, not a relocation with side effects.

Note it is **not avoidable**. OpenCode scans Claude's skills directory whether or not loadout
writes OpenCode's — so anyone with Claude skills and OpenCode configured needs this regardless of
what they choose in step 4.

### 4. Ask whether skills should differ per harness

Only worth asking once step 3 has explained what it costs.

- **Yes** — skills carry `::: <harness>` sections and each harness receives its own rendering.
  One copy per harness on disk.
- **No** — every harness receives identical content.

Default to yes. A skill with no markers renders byte-identically everywhere, so the feature costs
nothing until used.

Both answers remain adjustable later: per harness with `[claude] skills = false`, per skill with
`[skills.review] agents = [...]`.

### 5. Read the existing configuration

At each confirmed harness's known paths, for the artefacts loadout manages. Per the governing
principle, everything else is invisible.

Skills need no environment variables to be kept apart — the five destinations are already
distinct:

```
~/.claude/skills           ~/.codex/skills         ~/.factory/skills
~/.config/opencode/skills  ~/.pi/agent/skills
```

### 6. Fold into loadout source, and report

Write the source tree. Report:

- what was taken, per harness and category
- conflicts, where two harnesses disagree about content loadout would otherwise share

The report of what was taken is the honest answer to "what is managed now" — its complement is
what loadout left, which needs no enumeration.

### 7. Scan for secrets

The source is about to become a commit, and probably a remote. Scan, show anything suspicious,
require confirmation before continuing.

Loadout cannot prove content is secret-free, and should say so rather than imply a guarantee.

### 8. Render to scratch and diff against disk

**The trust moment, and the centre of the flow.**

Render the complete output to a scratch location and diff it against what is currently on disk.
Then either:

- **Identical** — say so plainly. This is the strongest possible statement: *loadout reproduces
  your setup exactly.* Proceed.
- **Different** — show the differences and ask for approval before overwriting.

Nothing at a harness path is written before this step completes.

### 9. Write, record, register

Write the outputs, record deployment receipts, register the source in the machine config so
`--global` resolves later.

**This is part of `init`.** The user should not have to run a second command to reach a working
state; `init` that leaves nothing rendered is a half-finished operation, and a broken handoff
between two commands that each report success is how defects hide.

### 10. Offer the payoff

- **Commit** the source. Offer it; do not do it silently.
- **A second machine.** Version-controlling global configuration pays off when it is cloned
  elsewhere. End by showing how: clone, then `loadout init global --source <clone>` on that
  machine. Without this the user has done the work and not collected the reward.

---

## Decisions this encodes

| decision | rationale |
|---|---|
| Allowlist extraction, never block on unknown files | A real `~/.claude` is full of runtime state and OS cruft; blocking on it makes onboarding impossible |
| One case, not two | Symlinks only matter when writing back to the original location, which this flow does not do |
| No env vars for directory relocation | Relocating a harness root moves its runtime state too, and OpenCode cannot relocate without moving all of `XDG_CONFIG_HOME` |
| `OPENCODE_DISABLE_CLAUDE_CODE_SKILLS` is offered | Genuine setup requirement, unavoidable, with a documented random-resolution hazard behind it |
| Per-harness skill rendering is the default | `render_skill(skill, harness)` is the signature; a shared directory can hold only one rendering |
| Diff before write | Converts adoption from "overwrite and hope" into "prove it reproduces what I have" |
| `init` renders | A command that leaves nothing working is unfinished |

## Open

- **Conflicts at step 6.** What "conflict" means concretely, and whether resolution is interactive
  or deferred to editing the source afterwards. Project scope hits this harder — see below.

---

# Project onboarding — `loadout init project`

Shares the governing principle, the one-case simplification and most of the flow. The differences
are real enough to be worth stating rather than inheriting.

## What differs from global

| | global | project |
|---|---|---|
| git | init creates a source repository | **required? no. created? no.** Works in a plain directory |
| instruction collisions | five harnesses, five files, none | Codex, OpenCode, Pi and Droid all read the repo-root `AGENTS.md` |
| machine registration | yes | none — nothing machine-wide to point at |
| generated outputs | nobody shares a home directory | **must be gitignored** — teammates regenerate them |
| the payoff | a second machine | the rest of the team |
| templates | supplies them | consumes them |

## The flow

### 1. Refuse to re-initialise

If a loadout configuration already exists here, say so and stop. Adding a harness later is editing
`harnesses` and running `sync` (S-03); re-running `init` is not how anything is changed.

### 2. Confirm the location

Project configuration belongs at the repository root. If the working directory is not the root,
warn and confirm rather than refuse — a monorepo subdirectory may be deliberate.

**Git is not required and is not created.** Unlike global, where init produces a source repository,
a project may legitimately be a plain directory. Never run `git init` on someone's project.

### 3. Read the machine's global configuration for defaults

If a global source is registered, its harness list is the natural default here — someone using
Claude and Codex globally almost certainly wants both in this project.

Fall back to detecting what this project already contains. Detection-only is the uncommon case:
local agent configuration without any global setup is rare enough not to design around.

### 4. Offer harnesses, preselecting the global set

### 5. Offer templates, if any resolve

Project scope is where templates apply and the global source is where they come from, so this is
the moment to offer them — and with `template add` now enumerating what is available (S-06), the
same enumeration serves here. Choosing one also means choosing **tracked** or **vendored**.

Skip silently when nothing resolves.

### 6-9. As global

Read existing configuration at this project's harness paths; fold it in and report; scan for
secrets; render to scratch and diff against disk before writing anything.

One difference at the fold-in step, below.

### 10. Write, record receipts, and ignore the outputs

No machine registration. But the generated outputs **must** be added to `.gitignore`:

```
/.loadout-state/
/loadout/.loadout-state/
/CLAUDE.md
```

This is what makes `scopes.md`'s all-or-nothing adoption survive contact with a team. Without it
every teammate regenerates the same files and collides on them in review.

Per S-03, `sync` maintains these entries thereafter, so the config file stays the only thing a
user edits.

### 11. Offer the payoff — the team

Commit the source, and say plainly that **adoption is per-repo and all-or-nothing**: everyone
working in this repository needs loadout, because there is no supported arrangement where one
person regenerates and another hand-edits. That belongs at the moment of committing, not
discovered later by a colleague whose edits keep disappearing.

## The conflict project scope has and global does not

Codex, OpenCode, Pi and Droid all read the repository-root `AGENTS.md`. Claude reads `CLAUDE.md`.

If a project already contains both **with different content**, that is a genuine conflict and
loadout cannot render it away — there is one `AGENTS.md` and four readers.
`docs/reference/coverage.md:515` records the constraint: *"one instruction order per repo, not one
per harness"*, and `:531` notes the shared document is *"agent-independent by construction"*.

So the fold-in step must treat `AGENTS.md` / `CLAUDE.md` divergence as a first-class conflict,
present both, and have the user resolve it — not silently pick one. Global never reaches this, and
a flow that inherits global's reporting will skate past it.

## Open

- **What "resolve" means here.** Interactive merge, or write both into the source and let the user
  edit before the first render? The second is simpler and defers nothing important, since step 9
  diffs before writing.

---

## Note for implementers

Claude Code exports `CLAUDE_CONFIG_DIR`, and loadout honours it per ADR 0011. Redirecting `HOME`
alone does **not** isolate a test run — it will read the developer's real `~/.claude`. Unset
`CLAUDE_CONFIG_DIR`, `CODEX_HOME`, `PI_CODING_AGENT_DIR` and `FACTORY_HOME_OVERRIDE` as well. This
invalidated several probes during the review before it was caught.
