# 0053 — Overlays become stealth sources, and templates become kits

**Status:** accepted (2026-11-07, ruled by the user). Renames the features recorded in
[0014](0014-a-vendored-template-is-source-not-output.md) and
[0045](0045-an-overlay-source-renders-into-repositories-that-have-not-adopted-loadout.md) to
[0052](0052-a-target-that-adopts-loadout-is-retired-by-the-overlay.md); their decisions stand
under the new names.

## Context

Both names described the mechanism, and both suggested the wrong one. "Overlay" reads as a UI
thing, and loadout already used the word for a profile fragment merged over another. "Template"
implies a starting point copied once, but a template stays linked and picks up its source's
current contents on every sync; only a vendored one is a copy. v0.10.0 shipped the overlay a day
before this ruling and it has no known users, so the names could still change cheaply.

## Decision

- **An overlay is a stealth source**, used in stealth mode: `loadout stealth add|list|remove`,
  `--stealth NAME` on `sync` and `check`, a `[stealth]` table, `stealth.toml`, records in
  `stealth-written/`, locks in `stealth-locks/` and exclude blocks marked `loadout stealth`. The
  name says what it is for: your own agent config in a repository you do not control, kept out of
  that repository's git. Beads uses the word for the same case ("Stealth Mode: Run
  `bd init --stealth` to use Beads locally without committing files to the main repo"), and it
  fits loadout's gear theme. `--stealth` stays an option that takes a registered name.
- **A template is a kit.** `loadout kit`, `kits = [...]`, the `kits` slice and a `kits/` folder.
  Kit fits every real bundle, `macos-vm` and `cli` included, and reads as "a loadout made of kits".
  None of the five harnesses uses the word for a config concept, and with it in place "template"
  means only a destination template.
- **The two kit modes are linked and vendored.** `--linked` replaces `--tracked`, and the docs'
  "declared", because linked is what dependency managers call the opposite of vendored.
- **Old spellings are refused, not aliased**, each with an error that names its replacement:
  `loadout overlay`, `--overlay`, `[overlay]`, `loadout template`, `--tracked`, `templates = [...]`,
  `use = ["templates"]` and a source with a `templates/` folder.
- **0.10.0 state is not migrated.** Nothing reads `overlays.toml`, `overlay-written/` or
  `overlay-locks/`, or recognises the old exclude markers. An overlay user runs
  `loadout overlay remove NAME --yes` with 0.10.0 before upgrading.

Rejected: guest (Chrome's Guest mode means without your settings and wiped on exit, the
opposite); stack (misses `macos-vm` and `cli`, "agent stack" already means loadout's harnesses,
and Pulumi uses stacks for environments); ambient (LangChain's background agents); untracked
(clashes with `--tracked`); shadow (suggests overriding); local, personal and private (taken);
sidecar (beside, not hidden); pack and bundle (generic); mixin (jargon); layer (implies an order);
preset (destinations); package and module (Pi packages, the module-config slice); role (a
persona); recipe and blueprint (copied once).

## Consequences

- "Stealth" can read as evading detection. The help text and the guide open with "Your own agent
  config in repos you don't control, kept out of git." to say what it hides and from whom.
- `check --st` no longer abbreviates `--staged`, since `--stealth` shares the prefix. Flag
  abbreviation is left as argparse has it.
- A 0.10.0 overlay left registered when its user upgrades is invisible to the new version: its
  files, exclude lines and `~/.claude.json` servers stay until 0.10.0 removes them or the user
  does. The upgrade note in the stealth guide and the troubleshooting page says so.
- `.codex/rules/<name>.rules` and `.loadout/opencode-instructions.md` use the registered name, not
  the feature's, so their paths do not change. "Overlay" keeps its other meaning, a fragment
  merged over another in a profile or a plugin reference.
