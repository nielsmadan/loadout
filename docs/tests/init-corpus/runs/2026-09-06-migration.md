# Init migration corpus — 2026-09-06

Five of nine public project inputs migrated; four projects and all five public global inputs
were refused without migration writes. A separate synthetic four-harness global input migrated.
These are preservation/refusal results, not a claim that all sampled configurations are adopted.

The [machine-readable result](2026-09-06-migration.json) contains all 14 exact upstream revisions,
mapping bases, command arguments/exits, issue paths, output hashes/modes and source reconstruction
results. The [input manifest](../../../../tests/fixtures/init_corpus.json) is the repeatable input
contract; [the procedure](../README.md) describes isolation and assertions. The final runner exited
0: every expected migration or unchanged refusal completed, with no skipped cases or timeouts.

## Setup and identity

Run started at 2026-09-05 23:34 UTC (2026-09-06 local) on Apple M1 Max, MacBookPro18,2,
64 GiB RAM, macOS 26.6.2, Python 3.13.6, Apple Git 2.50.1 and Loadout 0.1.0. The feature HEAD
was `ff9e7e8b268e456fa127a07da8848bc004e41141`, with Task 7 production changes. The JSON records
every Python source hash and tracked source patch hash
`6fb72d3cae393712e65bf3eacd4110c31d1d4d904e7445a4cc5fa0cc354bd7ff`.
Those hashes identify dirty code; HEAD alone does not identify the implementation tested.
This original run predates runner/fixture hash recording. It also predates the later independent
opaque-mode oracle and exact reconstructed output-inventory assertions; it verified planned
modes against outputs and complete per-file contents, not those stronger checks. Original
0640/0750 preservation was independently pinned by the live Git reconstruction regressions.
The updated runner hashes its own files and fixture, checks opaque modes directly against original
stat, asserts preview refusal exit 2, and checks the complete reconstructed output path set.
The recorded preview exits above were read from results, not asserted in that older refusal path.

```sh
.venv/bin/python tests/run_init_corpus.py \
  --project-inputs /private/tmp/loadout-init-spike-final.3sPbeZ \
  --global-inputs /private/tmp/loadout-global-init-spike.tNsaJU \
  --opencode-input /var/folders/v_/dg_p8zcs2nlcg2xq_v834dlh0000gn/T/loadout-init-corpus-kqpvhgni/opencode/repo-public \
  --synthetic
```

The final disposable evidence directory was `loadout-init-corpus-o0togtz6` under the session's
temporary directory. Commands used this checkout's isolated interpreter (`python -I -m loadout`),
never `uv` from a sampled repository. Every case had a new local Git clone and fake HOME with
harness relocation variables cleared. No upstream installer, script, hook or skill was executed.
No real home, registration, harness destination or checkout Git state was changed.

Thirteen retained HEADs matched their pins. OpenCode's old HEAD did not, and its pinned commit
object existed without a complete checkoutable object graph. Local checkout failed with
`fatal: unable to read tree (5cf9f517cfec3ef68d3e68a12a6a4b3163947f44)`; local fetch with lazy
fetch disabled also failed on a missing promisor object. A fresh public `gh repo clone` followed
by exact-pinned fetch/checkout supplied the fixture. Its complete index and HEAD tree both had
6,617 paths. The final run reused that complete clone locally. `gh` fixture-setup files in fake
HOME were snapshotted before migration, so they were not mistaken for migration writes. The old
sample was not reset or cleaned; the historical missing-root observation was not reproduced from
this complete pinned tree.

## Public results

For migrated rows, preview/init/check/sync/check-again/init-again/staged-check/reconstruction-sync
all exited 0. Refused rows had both preview and apply exit 2, with contents/modes, index bytes,
HEAD and fake HOME unchanged. A preview candidate labelled `migrated` is not a completed case
when another unresolved candidate prevents apply.

| input | scope | outcome | preserved output files / remaining decision |
|---|---|---|---|
| semantic-release-gitlab | project | migrated | 1 |
| pypistats | project | migrated | 1 |
| xcbeautify | project | migrated | 2 |
| agent-harness | project | migrated | 47 |
| agent-skills | project | refused | 2 authored `.tmpl` files need mappings |
| claude-code-showcase | project | refused | 2 source mappings and 2 dependency mappings |
| mcp-servers | project | refused | nested `src/everything/AGENTS.md` has no confirmed selected consumer |
| opencode | project | refused | 34 source mappings, including singular command/agent trees and support files |
| sentry | project | migrated | 176; explicit `.agents` mapping confirmed by its `AGENTS.md` |
| integralist-agent-skills | global | refused | 4 source, 2 activation, 1 unsupported-harness and 5 dependency issues |
| dwmkerr-dotfiles | global | refused | 4 source and 2 activation issues |
| dbochman-dotfiles | global | refused | 10 source, 4 activation and 1 dependency issues |
| archibate-dotfiles-claude | global | refused | 26 source and 1 dependency issues |
| anaiis-dotfiles | global | refused | 10 source, 2 activation and 4 dependency issues |

Global mappings were explicit static source/destination assignments supported by upstream source
documentation. They did not execute or guess installer effects, fill missing dependencies,
enable unsupported harnesses, or choose among unresolved copies. Those remaining decisions keep
the public global migration count at zero. The corpus is deliberately awkward and purposive;
5/14 is not an ecosystem compatibility rate.

## Preservation and first-edit evidence

All 227 migrated public output files were compared against original source input: complete
ordered owned JSON/TOML documents or exact opaque bytes, plus full filesystem modes. Generated
project copies were ignored and absent from the Git index; retirement actions and staged scope
were checked against the preview. The existing pinned inputs required no baseline content
changes. Migration staged 22, 27, 33, 89 and 123 paths respectively for the five migrated projects;
no unrelated paths entered that scope.

Fresh-process sync used only actual staged `loadout/` Git blobs, materialized with Git's
100644/100755 modes, without original files, receipts or machine registration. The four smaller
projects reconstructed every output. Sentry reconstructed 14 public outputs from 40 staged
source files. Its two skill trees were conservatively private because of credential-shaped
content: 162 generated files were omitted from the Git-only clone. Supplying the 164 serialized
private source files explicitly then reconstructed and compared all 176 outputs. Private source
and recovery preimages remain disposable local evidence, not checked-in examples.

Sentry's Claude allow list remained all **96 entries in original order**, with full-list equality
and ordered SHA-256 `cb51d0a9bcc326d4c7ab5a371ec1e31542606503a74a6a2ca6d673f0a9c9a5c6`.
The historical 96-to-zero scaffold result remains in the original report; it is not relabelled
as a successful migration.

Pypistats, xcbeautify, agent-harness and Sentry also exercised source skill addition, edit, rename
with old-output retirement, and last-file deletion, followed by check. The semantic-release input
has only its imported instruction output and was covered by repeated init/check/sync and Git
reconstruction, not a claimed skill edit.

The separate original synthetic global fixture migrated 10 files across Claude, Codex, OpenCode
and Pi, staged 54 scoped paths, reconstructed all outputs from 50 staged source files, and passed
the same skill-edit lifecycle. Its `example` object in the JSON records small before/source/after
documents, including ordered permissions, null/empty fields and an inert executable support file.
This example is not counted as a sixth successful public input and contains no upstream excerpts.

## Reproduced defects and corrections

- Git preserved only the executable bit of opaque source files. Live regressions first failed
  with original 0640 becoming 0644 and 0750 becoming 0755 after actual `git checkout-index`
  reconstruction. Authored `mode`/tree `modes` now preserve full output permissions across Git;
  omitted declarations retain legacy source-mode behavior. Those two cases now pass.
- A live 25-sibling privacy regression observed 50 Git ignore probes. Exact-parent batching now
  passes its bounded probe assertion while preserving nested repository semantics and rechecking
  later ignore-file changes. Logical and canonical paths remain checked; there is no cross-phase
  result cache. See the separate [performance comparison](2026-09-06-performance-comparison.md).
- Sentry's first explicit-mapping run completed init in 100.806 seconds after a 32.835-second
  preview, with a 20,597,144-byte v1 journal and 468 operations. It was not a timeout. Rewriting
  that payload on each cursor step was a practical cost; v2 keeps the protected payload separate
  from the small durable cursor and still reads v1 recovery state. The final Sentry init was
  45.740 seconds. These single corpus invocations are diagnostic context, not controlled benchmark
  repetitions. [The exploration record](2026-09-06-sentry-exploration.json) preserves its failure:
  the runner wrongly expected private whole-tree outputs in the Git-only reconstruction. The
  two-stage public/private oracle fixed that runner mistake without weakening privacy.

Another exploratory synthetic-global fixture used `/var` rather than its resolved `/private/var`
path; migration correctly refused its symlinked destination ancestor. The runner now resolves its
fresh evidence root. These fixture failures were retained, not counted as successful migrations.

The 15 new regression cases passed, including malformed mode maps, payload corruption/protection,
cursor-only writes, metadata payload replacement and v1 recovery. A focused existing interrupted,
resume, recovery and journal matrix passed 53 tests (50 deselected, zero skipped). The earlier
broader artifact/migration/deployment matrix passed 324 tests before the journal change; final
whole-suite verification is reported by the Task 7 handoff rather than attributed to that older run.
The full check also exposed an existing hook-tampering fixture reading v1's inline `operations`
from a v2 cursor (`KeyError`). Its focused reproduction failed, then passed after updating only
the fixture to save a coherently tampered v2 payload. The same hook-escape rejection and recovery
before Git initialization remain asserted; the test was not skipped or weakened.

## Limits

This validates source preservation and CLI lifecycle, not live harness execution or installer
equivalence. Runtime-owned fields intentionally outside artifact ownership are not reproduced.
Private source is intentionally absent from a public clone. Unresolved files need reviewed
mapping/activation decisions, not speculative format support added to make the table green.

Staged checking materializes entire repository HEAD/index trees. The final Sentry staged check
took 11.796 seconds with 20,842 tracked input paths; the synthetic many-file benchmark measures
a different blob/file shape. Neither proves low overhead for large binary-heavy repositories.
