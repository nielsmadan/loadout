# 0030 — Adoption rulings, recorded

**Status:** accepted (recorded 2026-10-15 for rulings made 2026-09-18 to 2026-10-15 and the changes
that carried them out). Supersedes the receipt passages in
[0020](0020-composition-uses-explicit-operators.md) and
[0028](0028-init-protects-an-adoption-with-a-backup-folder.md), which are left as written.

## Context

Several rulings shaped adoption and sync without an ADR of their own; code and reference docs
cited them by a planning ledger's IDs, which a reader of this repository cannot look up. Two
ADRs also still describe the deployment receipt, which no longer exists.

## Decision

- **One source of truth (D-04).** A change made in a harness's own UI or CLI belongs in the source.
  loadout flags it (`hand-edited`, `owned-key`, `unmanaged`) and does not merge it; the `/loadout`
  skill ports it, and the harness copy is then replaced or deleted. A rules file Codex reads whole,
  other than the one loadout renders, is deleted once its rules are in the source.
- **Conflicting copies are left out, not refused (D-05).** A skill, agent or project instruction
  whose copies disagree across harnesses is adopted by nobody: every copy stays in place and is
  named under `unadopted` for the `/loadout` skill to merge. Init adopts everything else.
- **A project's own settings follow Git (P-48).** The keys of a co-owned project settings file
  that no slice writes go into `loadout/settings/<harness>.json`: committed source when Git tracks
  the original, a private gitignored file when it does not.
- **A secret stays in the source, with a warning (D-07).** What a file contains does not decide
  where it goes. Init adopts a secret as it is, names each source file that looks like it holds
  one (`secrets`), says the source's remote should stay private, and redacts the values in its
  preview diff. Detection is in `credentials.py`.
- **No deployment receipt.** The record of what sync wrote ([0019](0019-sync-records-what-it-wrote.md))
  is the only one. Read "receipt" in 0020 and 0028 as that record; 0028's backup covers it.
- **A backup is spent once restored.** With nothing kept it is deleted; with something kept it
  shrinks to the kept paths and the write records restore updates. It counts only once its run
  began applying, and a restore marks it in progress before its first change, so a killed restore
  is offered again.
- **Restore removes nothing it cannot prove init wrote.** That holds for a repository init created
  as for any file: it goes only while every entry under its `.git` is one init recorded (what
  `git init` writes is recorded before it runs) and still as init left it, or is git's own
  runtime state while the repository has no index and no ref. A path now reached through a link
  added since is kept.

## Consequences

- Code and reference docs cite this ADR, or the ADR a ruling belongs to, instead of ledger IDs.
- [migration.md](../reference/migration.md) holds the detail of each rule: what init keeps
  private, what it leaves out, and backup and restore.
