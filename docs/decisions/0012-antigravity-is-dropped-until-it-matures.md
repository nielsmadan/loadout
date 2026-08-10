# 0012 — Antigravity is dropped until it matures

**Status:** accepted (2026-08-10). Reverses the inclusion of `antigravity` as a target harness;
`docs/reference/antigravity.md` and the Antigravity column in
[config.md](../reference/config.md) are kept as a record of what was verified.

## Context

loadout targeted five harnesses. Antigravity (`agy`) was included to cover the popular agents,
not because its mechanisms had been established — and as each was established it turned out to be
absent, unverifiable, or ineffective.

What loadout actually generated for it: **one file**, `~/.gemini/antigravity-cli/settings.json`,
out of eight global permission targets. Zero project targets — `PRESET["antigravity"]` was empty.

The findings, each verified and recorded in `docs/reference/`:

- **The generated file is ignored where it matters.** In headless mode `agy` soft-denies every
  file read and command regardless of `permissions.allow`; workspace registration and
  trusted-folder status are honoured only interactively. The sole escape is
  `--dangerously-skip-permissions`, which auto-approves everything including writes. Verified
  2026-07; it is why `agy` was dropped from the `/second-opinion` skill.
- **No global skills mechanism.** Verified negative against 1.1.11: no home-relative skills path
  of any kind — no `~/.agents/`, no `~/.gemini/**/skills`. Only project `.agents/skills/` and its
  own bundled assets.
- **No config-directory environment variable.** The only harness of five with none
  ([0011](0011-a-destination-follows-a-relocated-harness.md)), so a destination cannot follow it.
- **Globs cannot be expressed**, so glob entries are skipped and fall through to runtime approval.
- **`~/.gemini/settings.json` is empty** on a working install, and its relationship to the
  `antigravity-cli/` one is unknown. Plugin enablement was never established.

Two things do **not** rescue it:

- **Sandboxing does not substitute for the ignored allowlist.** Running `agy` inside a nono
  sandbox constrains filesystem paths and sockets, but not command-level policy —
  `--block-command` is deprecated and explicitly not child-process enforced — and outbound
  network stays open. So an ignored allowlist stops mattering for filesystem operations and
  keeps mattering for command and network ones.
- **Interactive use was never demonstrated.** `agy` was verified running inside a sandbox, but
  only via `--version` and `-p "…"`, both non-interactive. Since the permissions file is
  honoured *only* interactively, nothing observed shows it doing useful work.

The decisive reason was neither of those, and is worth recording plainly: the harness is more
hassle than it is worth, and its usage limits are small enough that a session lasts only a few
turns.

## Decision

**loadout does not target Antigravity.** The renderer, the harness name, the preset entry, the
fixture target and the tests are removed. `harnesses = ["antigravity"]` in a project config is now
an error naming the removal, rather than a name that silently generates nothing.

The reference pages stay. `docs/reference/` records *harness behaviour*, which remains true
whether or not loadout emits for it — and every finding above was expensive to establish and would
have to be re-established to re-add support. What is removed from the docs is the claim that
loadout supports it.

## Consequences

- New slices — hooks, mcp, plugins — are designed against **four** harnesses. Antigravity is out
  by decision rather than by omission, which stops each new spec re-deriving that its mechanisms
  are unknown.
- **loadout writes no `.agents/` path anywhere.** Antigravity was the only occupant of
  `.agents/skills/` at project scope; every other harness has its own directory. The `.agents/`
  convention becomes a recorded fact rather than a destination, and the skills slice loses its
  shared-directory collision case entirely.
- Four of the five reference pages describe supported harnesses and one does not. That asymmetry
  is deliberate and stated at the top of `antigravity.md`.
- Nothing is lost by deleting the renderer: it is in git history, and this ADR is the pointer.
  Recovering it is `git show` against the commit that removed it.

## Re-adding it

This is a "for now" decision, and the bar for reversing it is that the findings above stop being
true. Concretely, before re-adding:

1. Re-verify headless permissions — does `permissions.allow` apply without
   `--dangerously-skip-permissions`? If not, a generated permissions file is decoration.
2. Establish a global skills path, or confirm there is none.
3. Establish whether a config-directory variable exists, so destinations can follow it.
4. Establish the plugin enablement mechanism.
5. Re-check glob support.

The renderer itself is the cheap part and is recoverable from history. The verification is the
expensive part, which is why the reference pages are kept.
