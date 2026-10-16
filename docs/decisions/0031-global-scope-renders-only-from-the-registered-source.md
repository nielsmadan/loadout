# 0031 — Global scope renders only from the registered source

**Status:** accepted (2026-10-15, ruling D-08). Narrows
[0010](0010-a-machine-config-locates-the-global-source.md), which is left as written.

## Context

A root `loadout.toml` made any directory a global source. Global destinations are anywhere under
the home directory, so a cloned repository, or a pull request that added a `loadout.toml` to a
project, could write `~/.config/fish/conf.d/evil.fish` on the next `sync`, and `check` printed
the bytes of whatever file a destination named, `~/.ssh/id_fake` in the reproduction. Project
scope is contained to its repository; global scope had no boundary at all.

## Decision

- **Registration is the trust decision.** Global scope renders only when the root resolves to the
  source this machine's config registers: through `--global`, or `--root` or the cwd naming it,
  through a link or a relative path alike. There is no separate trust command;
  `loadout init global <path>` is how a folder becomes registered.
- **Anywhere else, a root `loadout.toml` is refused before anything is read or written**, with
  exit 3. That holds in a folder that also has `loadout/config.toml`: project sync fails closed,
  so a pull request that adds a `loadout.toml` is visible rather than silently ignored. A
  `loadout.toml` staged but not on disk is refused by `check --staged` the same way.
- **No readable machine config registers nothing.** A missing or unreadable one leaves no global
  scope anywhere. At a global source the unreadable-config error comes first, as before.
- A source that holds both scopes works at the registered source.
- Renders of a scratch copy that init or `check --staged` makes, and of the committed tree the
  sync guard compares against, may render global scope only on behalf of the source being
  registered or already registered. `RenderContext.registered` carries that decision.

## Consequences

- Every path that reaches a global render is behind the gate: `sync`, `check`, `check --json`,
  `check --staged`, the git hooks, the init preview and first sync, skill install and uninstall,
  and notices.
- `init global --registration keep` on a source init has not set up yet is refused before
  anything is written: its first sync renders global scope, and the kept registration names
  another source.
- A test that renders global scope registers its root explicitly (`tests/registration.py`), never
  through an autouse fixture, so a test of the refusal cannot be masked.
