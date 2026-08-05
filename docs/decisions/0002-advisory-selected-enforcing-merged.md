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

**Enforcing — union with deny-wins.** `deny > ask > allow`, commutative and associative, so a
rule means the same thing regardless of which source contributed it.

Set-difference on command patterns is never computed. sudoers(5) documents why: `!`-subtraction
is "generally not effective… a user can trivially circumvent this by copying the desired command
to a different name", so such restrictions "should be considered advisory at best". The matched
string is chosen by the caller.

## Consequences

- Delete-by-omission avoids the operator every merge-based system eventually grows and always
  gets wrong (Docker Compose `!reset`, systemd empty `Key=`, Helm `null`, NixOS #114131).
- A downstream layer structurally cannot weaken an upstream deny, so `prevent_global_weakening`
  becomes a property of the algebra rather than a validator to maintain.
- Source order carries no meaning; sources are a set.
- Cost: every fragment must be named explicitly, so manifests are verbose. Accepted
  deliberately — splicing can be added later, but cannot be removed once added.
