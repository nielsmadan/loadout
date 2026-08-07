# 0002 — Advisory artifacts are selected; enforcing artifacts are merged

**Status:** accepted (2026-08-02, milestone 2)

## Context

loadout composes four artifact types from potentially several sources. They do not split by
data shape — they split by **consequence**:

| | character | if a downstream layer drops one |
|---|---|---|
| instructions | advisory | the agent is less well-informed — recoverable |
| skills | advisory | a capability is absent — recoverable |
| MCP servers | advisory | a tool is absent — recoverable |
| **permissions** | **enforcing** | **a command runs that should not have** |

Agent instructions are not reliably followed and must not be load-bearing for security.
Permissions gate execution.

## Decision

Two mechanisms, sharing one source list.

**Advisory — explicit selection.** The consumer names every item it wants; *not* naming one is
how you drop it. An unqualified name that is ambiguous across sources is an error, never
last-source-wins. A name matching nothing is an error, so an upstream rename fails loudly
instead of silently changing output.

**Enforcing — union with deny-wins.** `deny > ask > allow`. An entry's final decision — the
verdict it resolves to — is commutative and associative: it means the same thing regardless of
which source contributed it or in what order sources are merged. This holds only for the
decision, not for position: emission order within a category, and last-source-wins conflicts on
map-shaped fields, follow source order and are not order-independent.

Set-difference on command patterns is never computed. sudoers(5) documents why: `!`-subtraction
is "generally not effective… a user can trivially circumvent this by copying the desired command
to a different name", so such restrictions "should be considered advisory at best". The matched
string is chosen by the caller.

## Consequences

- Delete-by-omission avoids the operator every merge-based system eventually grows and always
  gets wrong (Docker Compose `!reset`, systemd empty `Key=`, Helm `null`, NixOS #114131).
- A downstream layer structurally cannot weaken an upstream deny, so `prevent_global_weakening`
  becomes a property of the algebra rather than a validator to maintain.
- Source order carries no meaning for the decision each entry resolves to; sources are a set for
  that purpose. It still governs emission order and map-key conflicts — callers pass a fixed
  order (committed, then personal).
- Cost: every fragment must be named explicitly, so manifests are verbose. Accepted
  deliberately — splicing can be added later, but cannot be removed once added.
