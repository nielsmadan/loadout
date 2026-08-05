# 0005 — A deny cannot carry exceptions, and no layer may re-allow part of one

**Status:** accepted (2026-08-02, milestone 2)

## Context

The tempting feature is "deny this whole family, except this one safe member". Every
permission system is asked for it.

Claude Code documents the constraint directly:

> "A broad deny rule like `Bash(aws *)` blocks every matching call, including calls that also
> match a narrower allow rule like `Bash(aws s3 ls)`, **so a deny rule can't carry allowlist
> exceptions.**"
> "**If a tool is denied at any level, no other level can allow it.**"

AppArmor users requested per-hierarchy exceptions in 2009 (Launchpad #451422). The maintainer
named hierarchy-based precedence as the alternative; it was never implemented. Fifteen years
later AppArmor 4.1 shipped an author-declared priority integer instead.

## Decision

Permanent: **a lower layer may never re-allow a subset of an upper layer's deny.** Specificity
does not confer precedence. Any future escape hatch must be an explicit upstream-granted
exception, never implicit narrowness.

## Consequences

- The merge stays order- and layer-independent, which is what makes sources a set.
- Denies must be written at the granularity they are meant to bite at, because they cannot be
  refined later. `git tag -l` is allowlisted rather than allowing `git tag` and denying the
  mutating forms.
- This does **not** address commands that take other commands as arguments — an allowlisted
  `env`, `xargs` or `bash -c` voids every deny rule on a positionally-matching harness. No
  deny rule can fix that class; it needs a build-time ceiling that refuses to emit. Not yet
  implemented.
