# 0052 — A target that adopts loadout is retired by the overlay

**Status:** accepted (2026-11-04, overlay run 1 branch review). Supersedes the consequence in
[0045](0045-an-overlay-source-renders-into-repositories-that-have-not-adopted-loadout.md) that a
repository an overlay targets "can also be a project".

## Context

0045 let a target also be a project, with each path owned by one source and a path the project
records refused to the overlay. That held for whole files. It did not hold for the shared
documents, which the overlay owns key by key. A team that adopts loadout by committing a
`loadout/` folder bypasses the `init project` refusal: the next project sync found the overlay's
keys in `opencode.json` and its Pi files, called them hand-edited and advised porting them into
the team's source. After `--force` the project kept the overlay's keys as foreign ones, the
overlay refused the files as the project's on every run, and `overlay remove` deleted the
overlay-folder file the project's `opencode.json` still named.

## Decision

- **A repository that holds `loadout/config.toml` is not an overlay target.** A recorded one is
  retired like a target the patterns stopped matching: owned keys stripped, files removed, its
  `info/exclude` block and `~/.claude.json` servers taken out, with the note `adopted loadout; the
  overlay retired its files here`. This applies to a scoped run from inside the repository too.
  An unrecorded one is skipped with a message.
- **The overlay part runs before the project part** in a run that includes both, so the project's
  first sync never sees the overlay's files.

## Consequences

- An overlay and a project never share a repository. Personal configuration in an adopted
  repository goes in the project's personal files.
- `check` in such a repository before the next sync still shows the project part's view of the
  overlay's files; the overlay part, printed first, says they are being retired.
