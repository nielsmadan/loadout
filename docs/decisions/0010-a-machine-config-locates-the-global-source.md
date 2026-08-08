# 0010 — A machine config locates the global source

**Status:** accepted (2026-08-08, milestone 5).

## Context

Nothing told loadout where the global source lived. `--root` defaulted to the current
directory, and global scope worked only because `~/ac/sync.sh` passed `--root "$SCRIPT_DIR"`.
Running `loadout sync` from anywhere else did the wrong thing quietly.

The active profile had the same problem from the other end. It was a *deploy-time* choice:
both variants of every profiled document were generated into a staging tree, and a shell
script picked between them by symlinking one, using state it persisted itself. Two systems
therefore had to agree on which profile was live, and the one that decided was not the one
that generated.

Both are machine state — the answers differ per machine and belong to no repository. There
was nowhere for machine state to live, so it ended up distributed across a shell script's
argument, a shell script's state file, and the shape of a staging tree.

## Decision

One file holds it: `$XDG_CONFIG_HOME/loadout/config.toml`, falling back to
`~/.config/loadout/config.toml`.

```toml
source  = "~/ac"
profile = "autonomous"   # optional
```

- `source` names the directory holding `loadout.toml`, with `~` expanded. It must exist.
- `profile` is optional and defaults to `"default"`. `--profile` overrides it.
- Any other key is an error, so a typo fails loudly rather than being ignored.
- **Absent is not an error.** It means this machine has no global scope, which is the correct
  state for someone who only uses project scope. `--global` without it is the error, and says
  so by name.

This is the counterpart to [0008](0008-generated-files-carry-no-machine-state.md), not an
exception to it. Generated files stay a pure function of the source precisely because there is
now one sanctioned place for the state that is *not* part of the source.

## Consequences

- Knowing the profile at generate time means only one variant is generated, so there is no
  longer a set of staged alternatives for anything downstream to choose between. That is what
  retired the symlink layer: `sync` writes each document straight to its destinations.
- Switching profiles is now "write it to the machine config and sync". `~/ac/sync.sh`'s
  `--autonomous`/`--normal` flags do exactly that and nothing else.
- The config is written by `loadout init --global` and hand-edited afterwards. It is machine
  state, so it is never version-controlled and never generated.

## Correction to `docs/scopes.md`

That document gave four reasons for committing global outputs. Two were circular — "the check
treats a missing file as drift" and "a missing file is a dangling symlink" are consequences of
committing outputs, not arguments for it. A third, "the sync script falls back to the committed
files when loadout is not installed", is the same shape: the fallback exists because the files
are committed.

Only the fourth survives. Permissions are enforcing, and the output diff carries information
the source diff does not: deleting one line from `permissions.toml` reads as one line in the
source and as dozens of deny rules going from bypassable to enforced in the output.

That reason argues for *seeing* the blast radius, not necessarily for keeping a shadow tree in
git. Whether generated global outputs stay committed is therefore still open; a `loadout diff`
that reports the blast radius before writing would answer it better.
