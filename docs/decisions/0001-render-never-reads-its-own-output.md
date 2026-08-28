# 0001 — A renderer never reads its own output

**Status:** accepted (2026-08-03, milestone 3)

## Context

The system loadout replaced generated eight permission files, four of which opened by reading
the file they were about to write and using it as a template:

```
path = REPO_ROOT / "claude" / "settings.json"   # the file it is about to WRITE
settings = json.loads(path.read_text())         # ...and it READS that same file
perms = settings["permissions"]                 # replaces one key, keeps the rest
```

This was not reproducible from a clean checkout, let corruption propagate forward into every
later run, made rendering untestable without a filesystem, and made the golden test partly
vacuous — the hand-maintained keys matched trivially because they came from the file being
compared. One output additionally read a *different* renderer's result, creating an
undeclared ordering dependency between them.

No harness offered an escape. Claude has one canonical settings file per scope with no
imports; OpenCode merges across locations but not within one. Only Codex and Pi keep
permissions in their own file, which is exactly why those two never needed a template.

## Decision

Split the file's two roles in the source repo. A **base document** holds the hand-maintained
content, is an **input**, and is never written. Renderers take `(rules, base)` and return a
document; the base is a parameter.

A base is defined as *the output file with the generated keys stripped* — nothing invented.
The manifest enforces that a `base` may never name a path that is also a generated `output`.

`preserve` is a bounded exception: named top-level keys are copied forward from an existing
output for keys owned by a *different* generator. loadout never generates those keys, so it
cannot corrupt them, and a `preserve` entry may not name a key the renderer produces.

## Consequences

- Renderers became pure functions and are tested without a filesystem.
- A clean checkout renders correctly.
- The golden test now bites on the whole document rather than one key.
- Two Claude targets share one renderer, differing only by which base and rule selection the
  manifest gives them.
- Cost: hand-maintained content is duplicated between the interactive and autonomous Claude
  bases. Guarded by a test pinning their intended difference.

## Amendment (2026-08-06, milestone 4)

Project scope adds a second bounded exception, reached by a different route than `preserve`.

Two of the five project outputs — `opencode.json` and `.claude/settings.json` — are a harness's
*own* multi-purpose config file, not a loadout-only artifact. `opencode.json` can carry a
top-level `$schema`; `.claude/settings.json` can carry `enabledPlugins`, `sandbox` and `deny`
alongside `permissions`. The ported system (`manage.py:489`) read the existing file and
preserved everything it did not own; rendering these from `{}` would silently delete that
content.

For targets with `preserve_foreign=True`, `render_project` reads the existing output file and
passes it as the renderer's `base`, instead of `{}`. This is not a violation of the decision
above: every renderer still assigns its owned subtree unconditionally on every render, so that
subtree is always regenerated and can never feed back — only keys loadout does not generate
survive. It is also what preserves key position: `copy.deepcopy(base)` followed by an in-place
assignment of the owned key does not move a foreign key that came first, whereas appending
preserved keys after rendering would.

The other three project outputs are loadout-only and keep rendering from `{}`.

## Amendment (2026-08-28, codex delivery)

[0017](0017-ownership-may-be-declared-instead-of-derived.md) adds a second way to reach this
decision's invariant, for the case a base cannot express: where a slice **declares** the keys
it owns, the strip happens at the destination rather than in the repo. A base becomes optional
there. The rule that generated content is regenerated unconditionally, and never read back into
itself, is unchanged.
