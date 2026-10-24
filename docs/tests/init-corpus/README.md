# Init migration corpus and performance

Run this occasional compatibility check after migration, discovery, artifact, Git or recovery
changes. It measures preservation and refusal safety on awkward real inputs; the purposive
corpus does not estimate ecosystem prevalence. The [old scaffold experiment](../init-corpus-flow.md)
and [its results](../init-corpus-results-2026-09-05.md) remain historical.

## Inputs and isolation

The exact nine project and five global upstream revisions, harness choices and deploy bases live
in [init_corpus.json](../../../tests/fixtures/init_corpus.json). The pinned clones live in
`~/wrksp/loadout/corpus-inputs/<case>`; a case naming an `input` reuses that clone instead
(`integralist-agent-skills` reads `agent-skills`). Init reads only a live deployment,
so for each global case the runner copies the trees named under `deploy` into the fake HOME, as the
checkout's README installs them, before running init. Public repositories are read as data: never
execute their installers, hooks, skills or application scripts. Reuse
retained object stores read-only, cloning into fresh disposable repositories at the pinned commit.
Do not reset old samples or manufacture history by committing sampled content.

Use this checkout's editable Python environment and Git. `gh` and network access are required
only for a public fallback when retained objects are incomplete. A commit object existing does
not establish that its trees/blobs exist. The runner first tries a local clone and pinned
checkout, then a local fetch; `--fetch-missing-public` permits a fresh HTTPS `gh repo clone` and
exact-pinned fetch if those fail. The failed local copy remains as evidence. No secrets are read.

Every fixture gets a fresh resolved fake HOME, with all four XDG base directories inside it. The
runner clears the Claude, Codex, Pi, Droid and OpenCode relocation variables and every inherited
`GIT_*` variable, disables system Git config, points `GIT_CONFIG_GLOBAL` at an empty file, sets
identity only in the disposable repository, and gives every command `/dev/null` as stdin, so init
never stops to ask. Preserve the checkout's interpreter path: do not run
`uv` from a sampled repository. No actual home, agent destination or installed hook is touched.

## Run the corpus

From this checkout, all 14 cases:

```sh
.venv/bin/python tests/run_init_corpus.py
```

`--inputs DIR` reads the clones from elsewhere, `--fetch-missing-public` permits the public
fallback, and `--synthetic` adds the synthetic global fixture. Optional `--case NAME` selects
diagnostic reruns. `--opencode-input /path/to/pinned-clone` reuses
a complete public fallback without another network clone. `--evidence /path/to/new-directory`
selects an empty dedicated evidence directory; omission creates one. The script prints its path.
Runnable code is [run_init_corpus.py](../../../tests/run_init_corpus.py); the synthetic global
fixture is original inert test data for all four supported harnesses, outside the 14 public cases.

The runner records actual command arguments, stdout/stderr, exits and wall times in
`results.json` and `logs/`, prints each case's status, and ends with `converted N of 14`. It exits
1 when a case fails an assertion. Each case runs `init <scope> … --dry-run --json`, then
`--yes --json`, then `check`, `sync` and `check` again (`--root` for a project, `--global` for a
global case), and verifies:

- HEAD equals the pin and complete index/tree listings agree before init.
- Preview leaves the worktree's content and modes, the index and the fake HOME unchanged.
- An incomplete preview exits 2, and so does the apply that follows, leaving contents, index,
  HEAD and HOME as they were; the status names the issue codes. Refusal is not migration.
- A complete preview exits 0, and so does the apply. Init does not commit, and stages only the
  untracking its preview listed.
- `check` exits 0 after init, `sync` exits 0, and `check` exits 0 again.

An earlier runner also compared every adopted original with a generated write, one to one. That
stopped holding once project instructions and `.mcp.json` stayed in place rather than becoming
generated writes, so it failed every converting case and was removed, along with the
reconstruction and skill-lifecycle passes built on it. No test launches a harness, so a clean
check is not proof of that harness's execution.

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

### Mapped global sources

The project benchmark regenerates in place and has no obsolete source files to retire. Run the
separate global workload when changing retirement or transaction guards:

```sh
.venv/bin/python tests/benchmark_global_init.py
.venv/bin/python tests/benchmark_global_init.py --counts 25
```

It reuses the same inert skill and fixed 1-KiB support files in a disposable existing repository,
explicitly mapped to a separate fake-home Claude directory. Each original must be retired only
after its replacement is generated. The runner checks every original was retired,
complete output bytes/modes, original index removals, staged global source, unchanged baseline
HEAD, normal check and staged check. The default is a 25-file warmup followed by three runs at
25/100/1000; `--counts` selects a bounded comparison and `--repetitions` changes its sample count.
Keep both constant between compared runs. Each CLI command has the same 180-second cap and
evidence preservation as the project benchmark.

Global and project timings are distinct workloads. Do not use the project measurements to infer
retirement cost, or describe a single diagnostic sample as a controlled speedup.

Both init benchmarks assert that init commits nothing, stages nothing, and leaves the
generated outputs out of the index. A timed-out run is left as the kill left it: its directory
is disposable, and putting a killed init back needs a terminal.

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
- [Mapped-global baseline, 2026-09-06](runs/2026-09-06-global-performance-baseline.md).
- [Mapped-global first fix, 2026-09-06](runs/2026-09-06-global-performance-first-fix.md), including
  the 1,000-file timeout, cancelled repetition and supported recovery.
- [Mapped-global second-fix preflight, 2026-09-06](runs/2026-09-06-global-performance-second-fix.md),
  a further 1,000-file timeout with verified recovery.
- [Mapped-global retirement profile, 2026-09-06](runs/2026-09-06-global-profile.md), separating
  Git-process cost from parsing and fingerprint checks.
- [Mapped-global final comparison, 2026-09-06](runs/2026-09-06-global-performance-final.md), all
  samples completed; the 1,000-file median remains above the 120-second target.
- [Final corpus verification, 2026-09-06](runs/2026-09-06-final-verification.md), with the
  independent candidate-based output inventory oracle.
