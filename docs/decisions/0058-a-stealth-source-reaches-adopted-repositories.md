# 0058 — A stealth source reaches adopted repositories

**Status:** accepted (2026-11-19, ruled by the user). Supersedes
[0052](0052-a-target-that-adopts-loadout-is-retired-by-the-overlay.md): a repository that holds
`loadout/config.toml` is no longer skipped by stealth sources.

## Context

0052 kept an overlay and a project out of the same repository, because two writers disagreed about
every shared document. The cost fell on the person who works in a repository that later adopts
loadout: their personal instructions, skills, servers and permissions stopped at the door, and the
only place left for them was the project's own `permissions.local.toml` and `skills/<name>.local/`.
Those cover two kinds of content, were hidden from git by nothing, and are a second personal
mechanism beside the one every other repository uses.

## Decision

- **One writer.** In a repository that adopted loadout, project sync takes every registered
  stealth source whose `targets` match it as a personal tier in the same render pass. Every file
  in the clone has one writer, project sync. Stealth output is never layered onto project files, and
  no personal folder is copied or linked in. The one second writer is `~/.claude.json`, where
  the stealth machinery still writes a personal Claude server one leaf at a time; a server in
  `.mcp.json` would be the team's file. The one file project sync points at without writing is
  OpenCode's `<stealth folder>/.loadout/opencode-instructions.md`, which the stealth side writes, so
  a git hook can name a file that a stealth sync has not yet written or updated. A repository that
  has not adopted loadout keeps the stealth path unchanged.
- **Opt-in render.** The tier is a field of the render context and defaults to none. Only `sync`,
  `check` and the stealth fan-out pass one, resolved once per run and used for every render in it,
  so the guard compares the same tier it writes. Init, migration validation, expected-output
  regeneration and `check --staged` stay team-only.
- **Everything the source renders.** Instructions, skills, MCP servers, permissions, the Claude
  and Droid `settings` fragments and linked kits, for the harnesses both the project and the
  source declare. A note names the harnesses skipped. Permission rules merge into the team's
  permission files and verdicts merge strictest-wins. Personal instructions go only to files the
  team does not use for its own: never `AGENTS.md` or `CLAUDE.md`.
- **The team wins a name.** A personal skill or server that has the team's name is refused with a
  one-line fix: rename it with `[skills.<name>] name = "<new>"` or `[servers.<name>] name =
  "<new>"`, or name the skill's folder `<name>.local` to replace the team's. The aliases are the
  new surface: they apply in a global source, a project and a stealth manifest.
- **Source health.** A source is matched, unmatched or unknown. An unreadable manifest, a kit that
  does not resolve, an unreadable registry, a failed git probe where a source could match, or a
  source being removed (only where its targets match) makes it unknown. While any source is held,
  every recorded personal path and key, of every source, is kept: nothing is retired, the exclude
  lines stay, and one typo never deletes personal state. A team document the personal tier writes into, or a personal
  instruction document another source still renders, is left as it is on disk while any source is
  held, with a `refused:` line, so a held source never loosens a permission. The team render never
  fails because of a personal source.
- **Several sources compose alphabetically.** In the registry's order, which is by name. Rules
  merge strictest-wins; instructions for one destination are one document in that order; on a
  server, skill or settings key two sources both set, the first wins and the second is refused,
  naming the other. There is no priority key.
- **Refusals do not stop the sync.** Everything else is written, `refused:` lines go to stderr and
  the exit code is 3, as for stealth. Run from a git hook, a refusal is printed but the hook keeps
  the team's exit code, so a teammate's commit cannot make every checkout's hook fail.
- **The exclude block is keyed by root.** Project sync owns a block in the repository's
  `info/exclude`, named for the repository's root, listing the units only the personal tier produces. It is
  written before the files and shortened after deletions, and a block whose root no longer exists
  is dropped. The record keeps the common dir and the contributing sources, so a retry can find a
  clone whose source is gone.
- **Reach.** `sync --stealth N`, `sync --global` and `stealth remove N` also sync the adopted clones
  N matches and the ones whose record names it, and retire the personal tier of a clone that
  stopped being adopted. A plain `loadout sync` does neither. `stealth remove` keeps its
  `removing` mark until every such clone is done, so it can be retried. A personal file the user
  edited stops the removal in that clone, naming `stealth remove N --force`, which releases only
  N's claims and finishes: a file only N wrote is kept and no longer recorded, and one another
  source still writes, or another source's own, is left as it is and stays that source's. An edited
  team file still stops it.
- **The two project personal files are removed**, with no migration. `permissions.local.toml` and
  `skills/<name>.local/` are refused by name, naming a stealth source as the replacement.

## Rejected

- **Layering stealth output onto the project's files.** It leaves two writers on one file, which
  is the failure 0052 existed to prevent. Stealth writes five of the six shared files whole, so the
  two renders would wipe each other's keys, and strictest-wins needs one merge.
- **A per-repository mapping in the global source.** It moves a personal choice into a file that
  describes the machine, one entry per repository, instead of the source that already names its
  targets.
- **A personal folder copied into the clone, or symlinked local files.** Each puts a second copy
  of personal content in the clone, outside what loadout records, so removing or changing the
  source cannot find it.
- **A tier limited to personal-only destinations.** It leaves out the permissions, settings and
  servers the user asked to reach, which live in files the team also writes.
- **Doing nothing.** The person working in a repository that later adopts loadout keeps losing
  their personal setup at the door, and the two project personal files stay as a second mechanism.
- **Keeping the two project files beside stealth.** Two personal mechanisms differ in what they
  cover, in how they are hidden from git and in how they are removed.
- **An explicit priority between sources.** New surface that nobody asked for; the registry's
  order is the one stable order it has.
- **Moving `loadout/settings/local/` to stealth in the same change.** The brief removed two
  files. An untracked co-owned settings file is usually a team file not yet committed, and
  [0030](0030-adoption-rulings-recorded.md) still adopts it into a private
  `loadout/settings/<harness>.json`. It stays an open question for 1.0.

## Consequences

- A repository is no longer exclusively an overlay's or a project's: a stealth source writes its
  `~/.claude.json` leaves there, and everything else comes through the project's sync.
- A tier whose source is broken holds its files and exits 3 until it is fixed. A personal document
  that no longer parses is refused alone; the team and the rest of the tier still sync.
- A native `[claude.extra]` or `[opencode.extra]` entry that names a renamed server keeps its text:
  the rename rewrites neutral MCP rules only.
- The per-root lock on the project folder degrades to no lock on a filesystem that refuses it;
  two concurrent syncs of one clone can then interleave their record writes, and the next sync
  repairs it.
- A `.local` skill that replaces a team skill also replaces it in a shared `.agents/skills` folder
  that a harness the source did not select reads; a note names that harness.
- A project below the repository's top level has no personal tier, with a note when a stealth
  source targets it or the repository's top.
