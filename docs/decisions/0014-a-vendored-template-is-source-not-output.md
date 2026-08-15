# 0014 — A vendored template is source, not generated output

**Status:** accepted (2026-08-15, templates). Settles open question 4 of
`docs/superpowers/specs/2026-08-09-templates-design.md`.

## Context

A template can be **vendored**: copied into the project as `loadout/templates/<name>/` and
committed, so the repository stands alone for contributors who do not install loadout. The copy
carries a content hash recorded in `loadout/config.toml`, which answers one question — has this
copy been modified since it was vendored?

That creates a state loadout has not had before: a tree inside the project that loadout wrote,
that a user may legitimately edit, and that has an upstream it can fall behind.

`loadout check` exists to catch **drift** — a generated file no longer matching what the source
would produce — and exits 1 when it finds any, which is what makes it usable in a pre-commit
hook and in CI. Asking whether a modified vendored copy is drift looked like a judgement call
about strictness. It is not; it is a question about what `check` is for.

Two wrong answers were available, and both are worth naming because each is locally reasonable:

- **Fail on it.** This reads a user editing a file they own as an error, and would break every
  CI run for a project that deliberately keeps a local change. It also has no remedy: `sync`
  refuses precisely this case, so `check` would demand a fix the tool declines to perform.
- **Say nothing.** Silence lets a project diverge from its template indefinitely with nothing
  ever mentioning it — which is the cookiecutter failure mode that vendoring exists to avoid.

## Decision

**A vendored template is source.** It sits under `loadout/`, beside `permissions.toml` and the
instruction fragments, and it is committed. Nothing regenerates it except an explicit
`loadout template sync`.

Therefore it falls outside `check`'s jurisdiction **by definition rather than by exemption**.
`check`'s contract is that generated output matches its source; a vendored template is one of
the inputs to that comparison, not one of its outputs. There is no rule being relaxed here.

Concretely:

- `loadout check` **reports** a vendored copy whose content no longer matches its recorded hash,
  on stdout, and **does not change its exit code**. Real drift still exits 1, and the note never
  suppresses it.
- `loadout template sync` is the gate. It refuses a modified copy, prints the diff against the
  upstream, and changes nothing.
- `loadout template list` states each template's mode and, for a vendored one, whether it is
  clean or modified.

This is the same posture the sync guard already takes toward a hand-edited generated file: it
**refuses to overwrite** rather than declaring the user wrong.

## Consequences

- A project may hold a deliberately modified vendored template forever. That is a supported
  state, not a broken one, and `check` stays green through it.
- The cost lands where it is visible: the next `template sync` refuses, and the user resolves it
  by moving the edits upstream or re-vendoring wholesale. The spec is honest that a template fix
  reaching twelve modified projects is twelve manual merges; if that becomes the common case
  rather than the rare one, that is the signal to build three-way merge.
- Upgrading to three-way merge later changes only the gate. The recorded content hash is exactly
  the base such a merge needs, so nothing decided here has to be migrated —
  see the spec's "Out of scope".
- A vendored copy with **no** recorded hash is not reported. Nothing is known about it, and
  inventing a complaint from an absence would be a claim this has no source for. `template sync`
  records one the next time it runs against a clean copy.
- [0008](0008-generated-files-carry-no-machine-state.md) is untouched by the recorded hash. That
  rule keeps *generated* content a pure function of the source so it never encodes machine state;
  a hash recorded in `loadout/config.toml` **is** source, and is machine-independent by
  construction — the digest covers only paths relative to the template root, so the same template
  hashes identically wherever it sits.
