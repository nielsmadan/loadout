# Independent init corpus rerun — 2026-09-06

The primary session reran all 14 pinned public inputs plus the separate synthetic global case,
using the strengthened runner. Result: **exit 0**, five public project migrations, nine unchanged
refusals and one synthetic global migration. No cases were filtered, skipped or timed out.

This independently reproduces the [earlier corpus outcomes](2026-09-06-migration.md); it does not
turn the nine refusals into supported conversions. The [complete JSON](2026-09-06-root-verification.json)
records each case, commands, exits, comparisons, pins and source/runner identities.

## Actual setup

Started 2026-09-06 01:47:41 CEST (2026-09-05 23:47:41 UTC), from the development checkout:

```sh
.venv/bin/python tests/run_init_corpus.py \
  --project-inputs /private/tmp/loadout-init-spike-final.3sPbeZ \
  --global-inputs /private/tmp/loadout-global-init-spike.tNsaJU \
  --opencode-input /var/folders/v_/dg_p8zcs2nlcg2xq_v834dlh0000gn/T/loadout-init-corpus-kqpvhgni/opencode/repo-public \
  --synthetic
```

The new evidence root was
`/private/var/folders/v_/dg_p8zcs2nlcg2xq_v834dlh0000gn/T/loadout-init-corpus-gufvixuv`.
Each case used a new clone and fake home; the complete pinned OpenCode clone was reused read-only.
The [procedure](../README.md) defines isolation and inputs. No sampled program or installer ran.
No live agent configuration or development-checkout Git state was changed by the corpus runner.

Revision was `ff9e7e8b268e456fa127a07da8848bc004e41141` with Task7 source changes. Tracked source
patch SHA-256: `6fb72d3cae393712e65bf3eacd4110c31d1d4d904e7445a4cc5fa0cc354bd7ff`.
The JSON also records every Python source hash and these runner hashes:

| Input | SHA-256 |
|---|---|
| `tests/run_init_corpus.py` | `7c0d2420a88e79872a1dae364ab9fa0cf293cc2882b62c49b5e4b667b552cd08` |
| `tests/init_evidence.py` | `ad0fbe677fb55dd92c9e64f515cb426d8ef215ab03f21aed0b56a3f17ae47493` |
| `tests/fixtures/init_corpus.json` | `dd3ce66f4b851ebb6fdd082036feeca9e6c1dadc56cdc25ef972902a4c04a67c` |

## Stronger checks exercised

- Opaque expected modes came from each original file's filesystem metadata; planned modes had
  to agree. The plan was not its own mode oracle. Native document modes follow their documented
  canonical/partial-document contract instead.
- Every unresolved preview, as well as its apply attempt, had to exit 2. Contents/modes, index,
  HEAD and fixture home remained unchanged after refusal.
- Fresh source-only reconstruction checked the exact output-file inventory, including absence
  of undeclared outputs, as well as complete ordered owned documents, bytes and modes.
- Sentry's public reconstruction omitted private trees; a second reconstruction supplied only
  serialized private source. Neither reconstruction read obsolete originals or recovery journals.

## Results

| Migrated input | Compared outputs | Git-only reconstructed outputs | With explicit private source |
|---|---:|---:|---:|
| semantic-release-gitlab | 1 | 1 | Not needed |
| pypistats | 1 | 1 | Not needed |
| xcbeautify | 2 | 2 | Not needed |
| agent-harness | 47 | 47 | Not needed |
| sentry | 176 | 14 | 176 |
| synthetic-global, separate from public corpus | 10 | 10 | Not needed |

All 227 public output files and 10 synthetic global output files passed comparison. Sentry kept
all 96 Claude allow entries in their original order, with ordered hash
`cb51d0a9bcc326d4c7ab5a371ec1e31542606503a74a6a2ca6d673f0a9c9a5c6`.
Normal check/sync, repeated init, staged validation and applicable skill-add/edit/rename/delete
checks passed. The same four public projects and all five public global cases were refused,
with the reasons enumerated in the earlier report and this run's JSON.

## Limits and retention

This was a correctness rerun, not a controlled performance measurement: the implementer's full
suite ran during part of it. Do not compare its wall times with the uncontended benchmark.
Harness execution and installer equivalence remain untested. Refusals still need human-reviewed
mapping/activation decisions. Private source and journal preimages remain in disposable local
evidence; only sanitized metadata and original synthetic examples are published here.
The protected journals were retained, not deleted or used as reconstruction input.
