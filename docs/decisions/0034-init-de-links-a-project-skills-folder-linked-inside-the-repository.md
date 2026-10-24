# 0034 — Init de-links a project skills folder linked inside the repository

**Status:** accepted (2026-10-24). Extends [0023](0023-adoption-de-links-a-symlinked-deployment.md)
to project scope for one shape, a harness `skills` folder; 0023 stands as written.

## Context

Round 3 made project init refuse every link sync would refuse: a harness folder that is a link,
any link inside one, and any link into `.git`. Project sync never writes through a link, so a
project adopted through one could not be synced.

That refusal blocked a common way of sharing skills. Three of the nine project setups in the init
corpus link a harness skills folder to one real folder in the repository, `.claude/skills ->
../.agents/skills` (sentry, Integralist/agent-skills, CodelyTV/agent-harness, the last also
`.opencode/skills`). Simply reading through the link does not help: the first render then stops
at sync's guard, "through the link .claude/skills -> .agents/skills".

## Decision

A project harness `skills` folder that links to a real folder in the same repository, outside
`.git`, is adopted rather than refused.

- **The harness reads the target itself**, as OpenCode and Pi read `.agents/skills`: init retires
  the link, and the harness goes on reading the folder.
- **Otherwise** init reads through the link. At apply it replaces the link with a real folder,
  using 0023's de-link topology (`_topology`), which the first render fills, and drops the link
  from the index.
- The target folder stays as it was. Unlike 0023, nothing is linked back into it: a link in a
  project would be committed, and project sync refuses to write through one. An entry of the
  target that loadout does not render is left there instead. That is safe only for an entry the
  harness cannot load from the folder, by the per-harness rule in
  [config.md](../reference/config.md#what-a-harness-loads-from-a-skills-or-agents-folder), so for
  any other init refuses at preview, exit 3, with nothing changed.

Every other project link refusal stays:

- **Out of the repository** (`external-symlink`): adopting it would copy external content into
  committed source ([0026](0026-a-project-root-linked-outside-the-repository-has-no-override.md)).
- **Into `.git`** (`git-link`): `.git` holds credentials, such as a token in `.git/config`, and
  runs hooks; init never reads through it.
- **Any other linked harness folder, and any link inside one** (`linked-folder`, `linked-file`).
  A skills folder is the one place where every entry is either a skill loadout renders or one its
  harness ignores, so a real folder of rendered skills replaces the link without losing anything.
  A folder such as `.claude` or `agents/` mixes loadout's files with the user's own, and replacing
  it would need the links back that a project cannot commit.

## Consequences

- After init, `.claude/skills` is a real, gitignored folder of rendered skills, and the link's
  removal from the index is staged. A retired link is an unstaged deletion, like every retired
  original.
- The folder the link led to keeps its content, and stays tracked unless a harness renders into
  it: Codex's project skills render to `.agents/skills`.
- A skill added to the target later no longer appears in the replaced folder. It reaches the
  harness once it is added to the source and synced.
