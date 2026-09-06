# Init migration corpus and performance

Run this occasional compatibility check after migration, discovery, artifact, Git or recovery
changes. It measures preservation and refusal safety on awkward real inputs; the purposive
corpus does not estimate ecosystem prevalence. The [old scaffold experiment](../init-corpus-flow.md)
and [its results](../init-corpus-results-2026-09-05.md) remain historical.

## Inputs and isolation

The exact nine project and five global upstream revisions, harness choices and explicit mapping
bases live in [init_corpus.json](../../../tests/fixtures/init_corpus.json). Public repositories
are read as data: never execute their installers, hooks, skills or application scripts. Reuse
retained object stores read-only, cloning into fresh disposable repositories at the pinned commit.
Do not reset old samples or manufacture history by committing sampled content.

Use this checkout's editable Python environment and Git. `gh` and network access are required
only for a public fallback when retained objects are incomplete. A commit object existing does
not establish that its trees/blobs exist. The runner first tries a local clone and pinned
checkout, then a local fetch; `--fetch-missing-public` permits a fresh HTTPS `gh repo clone` and
exact-pinned fetch if those fail. The failed local copy remains as evidence. No secrets are read.

Every fixture gets a fresh resolved fake HOME. The runner clears Claude, Codex, Pi, XDG and
OpenCode relocation variables and inherited Git overrides, disables system Git config, and sets
identity only in the disposable repository. Preserve the checkout's interpreter path: do not run
`uv` from a sampled repository. No actual home, agent destination or installed hook is touched.

## Run the corpus

From this checkout, where the two input directories contain the case names in the manifest:

```sh
.venv/bin/python tests/run_init_corpus.py \
  --project-inputs /path/to/project-inputs \
  --global-inputs /path/to/global-inputs \
  --fetch-missing-public --synthetic
```

Optional `--case NAME` selects diagnostic reruns. `--opencode-input /path/to/pinned-clone` reuses
a complete public fallback without another network clone. `--evidence /path/to/new-directory`
selects an empty dedicated evidence directory; omission creates one. The script prints its path.
Runnable code is [run_init_corpus.py](../../../tests/run_init_corpus.py); the synthetic global
fixture is original inert test data for all four supported harnesses, outside the 14 public cases.

The runner records actual command arguments, stdout/stderr, exits and wall times. Each case verifies:

- HEAD equals the pin and complete index/tree listings agree before init.
- Preview leaves content/modes/index unchanged. An unresolved apply exits 2 and preserves
  contents, index, HEAD and fixture-setup HOME state; refusal is not migration.
- Completed init preserves whole ordered documents and opaque bytes/modes; opaque expected modes
  come from original filesystem stat, while rendered documents use their canonical mode contract.
  Sentry's ordered
  Claude allow list must equal all 96 originals. Baseline/index changes stay in preview scope,
  originals retire as planned, generated copies remain ignored and absent from the index.
- Check, sync, repeated init and staged check run. Fresh-process sync reconstructs from actual
  staged Git blobs with Git's 100644/100755 modes, without originals, receipts or machine config.
  Optional private trees are absent in that public reconstruction. When private source exists,
  a second reconstruction supplies only those serialized private files and verifies all outputs.
- Supported skill routes exercise first addition, edit, rename and last deletion through sync.

Whole-tree privacy is conservative: one credential-shaped file can make a complete skill tree
private. Do not publish those values or silently count them as reconstructed from Git. Runtime
fields excluded by ownership are not part of the source reconstruction. For full filesystem modes
across Git, migration records explicit artifact modes; newly added undeclared paths inherit their
source mode. No test launches a harness, so preservation is not proof of that harness's execution.

## Run the performance comparison

```sh
.venv/bin/python tests/benchmark_init.py
```

This is real CLI init wall time on fresh existing-Git/fake-home fixtures, concurrency one: one
25-file warmup, three measured runs each at 25, 100 and 1000 fixed 1-KiB support files plus one
inert Claude skill. Setup/check timings are separate. Each init is capped at 180 seconds;
timeouts are censored failures, preserve recovery state and invoke supported recovery. Successful
durations count only after byte/mode/index/ignore, check and staged-check assertions pass.
Keep machine, workload, warmup and repetition counts fixed between baseline and comparison.

Local median budgets are 100 files within 15 seconds and 1000 within 120 seconds. They are not
cross-machine guarantees. Record all durations and ranges. Staged check snapshots full HEAD/index
trees; inspect its separate timing and repository size before making large-repository claims.

## Publish and retain

[init_evidence.py](../../../tests/init_evidence.py) can publish a result JSON with evidence and
checkout roots replaced by placeholders:

```sh
.venv/bin/python tests/init_evidence.py /path/to/evidence/results.json \
  docs/tests/init-corpus/runs/YYYY-MM-DD-label.json
```

Inspect the result before publication. Keep raw logs, third-party trees and protected recovery
preimages in disposable evidence. A v2 journal needs its payload files as well as its cursor.
Checked-in examples are synthetic; upstream identities remain in the manifest. Keep a dated
record describing the exact setup, dirty-code and runner/fixture identities, outcomes, limitations and any failed
exploratory run. Do not replace earlier measurements with later ones.

The [small project example](examples/project.md) and [small global example](examples/global.md)
show actual before, category-source and reconstructed after documents without retaining
third-party trees or private values.

## Recorded runs

- [Performance baseline, 2026-09-06](runs/2026-09-06-performance-baseline.md), the comparison baseline.
- [Migration corpus, 2026-09-06](runs/2026-09-06-migration.md).
- [Independent rerun, 2026-09-06](runs/2026-09-06-root-verification.md), including original-mode
  and exact reconstruction-inventory checks with recorded runner hashes.
- [Performance comparison, 2026-09-06](runs/2026-09-06-performance-comparison.md).
