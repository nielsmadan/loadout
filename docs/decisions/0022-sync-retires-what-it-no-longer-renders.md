# 0022 — `sync` retires what it no longer renders

**Status:** accepted (2026-10-11). Consumes the record
[0019](0019-sync-records-what-it-wrote.md) kept for this.

## Context

Removing something from the source did not remove it from the agent. Measured on `main`: a removed
skill's folder stayed, so every agent was still offered the skill; a removed harness kept reading
its `AGENTS.md` and `.codex/rules/permissions.rules`; removing the last MCP server left `.mcp.json`
listing it, and `sync` reported `Up to date.`. Nothing said so in any of the three.

[0008](0008-generated-files-carry-no-machine-state.md) named orphan removal as what would need a
record of past writes, and 0019 built that record and retained entries no longer rendered for
exactly this consumer.

## Decision

For each destination in the written record that this run does not render:

- **Unchanged since loadout wrote it**: retire it. A file is deleted and its `.gitignore` entry
  dropped; a skill folder goes with its files; a shared file loses only the keys loadout recorded
  writing, through the same stripping the merge already uses. `sync` prints `retired <path>`.
- **Edited since**: refused, as the guard refuses any hand edit; `--force` retires it anyway.
- **Not in the record**: never touched.

"Unchanged" is the guard's own verdict, from the one classifier behind `sync`'s refusal and
`check`'s drift kinds. Retirement adds no judgement of its own. A retired destination leaves the
record, so a file later put at the same path is not loadout's. `check` reports a pending
retirement as `orphaned`.

## Consequences

- The proof is the record, so everything 0019 says about losing it applies: no record, nothing
  retired. A sandbox that cannot write the record retires nothing, which fails safe.
- A skill folder holding a file loadout never wrote keeps that file and the folder. Only the
  directories retirement emptied are removed, and only up to the skill folder; any other directory
  a retired file leaves empty stays.
- A shared file is never deleted, even when stripping leaves it empty: loadout recorded keys in
  it, not the file. Its key set has no hash, so a hand edit to a recorded key is stripped without a
  refusal, as the guard already overwrites one while the file is still rendered.
- The record does not know why a path stopped rendering. A harness variable moving a destination,
  or a profile that renders fewer outputs, retires the old path's unchanged files too. Syncing
  again from the other environment or profile writes them back.
- `init resume`, `init recover` and init's backup are not covered here.
