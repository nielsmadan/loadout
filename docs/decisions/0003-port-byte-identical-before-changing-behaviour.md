# 0003 — Prove a port byte-identical before changing any behaviour

**Status:** accepted (2026-08-02, milestone 1; reaffirmed milestone 3)

## Context

loadout replaces working generators one subsystem at a time. Each port carries a risk: a
rewrite that looks right, passes review, and quietly emits different bytes — which for
permissions means a rule that no longer applies.

## Decision

Capture the existing system's output as frozen fixtures **before** writing any code, and treat
byte-identical reproduction as the acceptance criterion. `tests/golden/expected/` is truth: a
mismatch means the port is wrong, never that the golden is stale.

Behaviour changes ship **after** the port is proven, as their own reviewed commit — including
changes as small as an attribution header. That commit is the only kind permitted to modify a
golden, and it must change exactly the goldens it intends to.

## Consequences

- A byte mismatch is unambiguous. Combined with a behaviour change in the same commit, it
  could not distinguish "the port is wrong" from "the intended change landed".
- Inherited oddities are reproduced rather than tidied, and fixed deliberately afterwards:
  `claude/mcp-permissions.json` is serialized with `ensure_ascii=True` while the other six use
  `False`, almost certainly unintentional upstream, faithfully reproduced.
- Migration into the live system re-uses the same oracle: snapshot the current output first,
  diff after, and never hand-edit a generated file to make the diff go away.
- The discipline is what caught that the milestone-3 renderers were correct — two independent
  reviewers rendered from fixtures and diffed against the goldens before the integration task
  was due to prove it.
