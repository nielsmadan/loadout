# 0016 — A catch-all is stated only where the document carries it

## Status

Accepted.

## Context

Every permission rule loadout renders is an entry in a list. What happens to a command *no*
entry matches was, until now, whatever each renderer hardcoded: OpenCode and Pi seeded
`permission.bash["*"]` with `ask`, Claude and Codex wrote nothing and let the harness's own
setting decide.

Retiring a large shared allowlist makes that catch-all the load-bearing decision rather than a
detail — the whole point of dropping 202 entries is that the fallback changes. A hardcoded seed
cannot express it, and hardcoding a different one would bake one machine's threat model into the
tool for the next reader to inherit unlabelled.

## Decision

`[shell] default` states the catch-all in the source. It is **authored for OpenCode and Pi
only**, and the boundary is where the key lives, not what the harness is capable of:

| harness | catch-all | why |
|---|---|---|
| OpenCode, Pi | `permission.bash["*"]` | loadout writes the map it sits in |
| Claude | `permissions.defaultMode` | loadout writes that file, but *preserves* this key from the base rather than authoring it; a second spelling would fight the settings slice |
| Codex | `approval_policy` in `config.toml` | loadout does not write that file |

Codex's rules file additionally has nowhere to put one. Its exec-policy DSL registers four
globals in `POLICY_BUILTINS_STATICS` — `prefix_rule`, `network_rule`, `host_executable`,
`paths` — verified by disassembling the registration sequence in the 0.149.0 binary. The
adjacent string blob is **not** an enumeration: it is linker-deduplicated, and `decision`, a
real `prefix_rule` parameter, lives in a different pool and is absent from it. Reading that blob
as a complete listing was the first version of this record, and is the seventh instance of the
failure AGENTS.md's "This machine is not the world" catalogues.

Three rules follow, and each closes a way the key could render a **wider** policy than the
source states:

1. **Across tiers, the strictest *stated* value wins**, and an omitted key casts no vote. This
   is the scalar counterpart of ADR 0002's deny-wins, and it differs deliberately from
   `opencode_extra`'s last-source-wins: a project source that never mentions the key must not
   tighten a machine that set one, and a template must not loosen it either.
2. **A bare `*` shell entry is refused at parse time.** It was the accidental spelling of a
   catch-all before the key existed. The two resolve by different algebras — strictest-wins for
   the key, last-match-wins for the entry — and under OpenCode's assign-in-place the entry lands
   exactly where the seed sits, so no extractor can tell them apart.
3. **Extraction settles disagreement on the strictest verdict rather than withholding.**
   Withholding an entry drops it to the catch-all; withholding the catch-all drops it to `ask`,
   the *middle* verdict, so a `deny` on disk would come back a prompt. For the same reason, while
   any shell entry is withheld the catch-all is not stated looser than the `ask` those entries
   used to fall through to.

A stated default that a selected target cannot carry is **reported at sync time** (ADR 0015),
not left to the reference docs.

## Consequences

`Rules.default` is `Decision | None`, and `None` is not `"ask"`: within one tier they render
identical bytes, across tiers only a stated value votes. Extraction therefore reads a document
whose catch-all is `ask` back as *unstated* — the one spelling the round trip does not preserve,
recorded under extraction's byte-stable ambiguities.

`pi-project` gets its own extractor. `render_pi_project` seeds no catch-all, so a leading `*`
there is a genuine rule; the seeded reading deleted it and re-rendered a different document while
reporting no loss.

**This does not make loadout an enforcement boundary.** `default = "allow"` plus a deny list is
subtractive policy, which ADR 0002 already rejects: every spelling that misses a deny pattern
now falls through to silent execution rather than to a prompt. The wrapper-command bypass in
`docs/reference/README.md` is the worked example, and it is unbounded under an `allow` default.
That is the operator's call to make, and the reference says so at the point of use.
