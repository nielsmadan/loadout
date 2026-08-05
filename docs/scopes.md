# What loadout is for, and the scope model

## The problem

Five AI coding harnesses each want the same information — which commands may run, what the
agent should know about a project — in five different formats, with five different matchers
and five different file layouts. Maintaining that by hand means five copies drifting apart.
Maintaining it with five ad-hoc scripts means the same, plus the scripts drift from each
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

Machine-wide configuration: the rules and instructions that apply to every project. Source
lives in one repo (`~/ac`); generated files are written there and deployed to each harness's
home directory, mostly by symlink.

Global outputs **are** committed. The reasoning is specific to this scope and does not carry
to project scope:

- The check treats a missing file as drift, so a fresh clone with uncommitted outputs fails
  its own pre-commit hook until someone syncs.
- The sync script's fallback — "loadout not found, using the committed files" — only means
  something if those files exist.
- Deployment is by symlink, so a missing file is a dangling link.
- Permissions are enforcing, and the emitted diff carries information the source diff does
  not: removing one entry from a rule list reads as one line changed in the source, and as 42
  deny rules going from bypassable to enforced in the output.

### Project scope — designed, not yet built

Per-repo configuration, layered on top of global. Two sources per artifact type:

| source | committed? | holds |
|---|---|---|
| project | yes | rules and instructions everyone working on this repo gets |
| personal | **no** | your rules for this repo — machine paths, local tools |

loadout merges the two and writes **one generated output per harness, always gitignored**.

Generated project files are never committed, because the merged output contains personal
content — two people would conflict on every regeneration. Shared content reaches other
people through the committed *source*, which works precisely because adoption is
all-or-nothing.

**loadout merges the tiers itself rather than using each harness's native mechanism.** Four
of the five do have one — Claude's `CLAUDE.local.md`, Codex's `AGENTS.override.md`,
OpenCode's `instructions` config key, Antigravity's `.agents/rules/` directory — but they are
four different shapes, Pi has none so the merge path must exist anyway, and the only thing
native mechanisms would buy is committed outputs, which nobody needs given all-or-nothing
adoption. Revisit only if that adoption model ever changes.

Consequences:

- `loadout init` scaffolds both sources and adds the personal source plus every generated
  output to `.gitignore`. A committed `.gitignore` rather than `.git/info/exclude`, which is
  per-clone and silently fails to apply on a fresh checkout.
- No per-entry ownership tracking is needed: loadout is the sole writer of every generated
  file, so there is nothing to avoid clobbering.

## Still open

- Whether project scope ships permissions first or permissions and instructions together.
- Absorbing per-project-type template config, so template rules land in the project *source*
  rather than being written into harness outputs by a separate tool.
- Build-time ceilings (`neverallow`) for the wrapper-command bypass described in
  [0005](decisions/0005-a-deny-cannot-carry-exceptions.md). The unresolved part is that a
  ceiling has to reason about command *shape* — "does this command take another command as
  an argument" — rather than string prefixes.
