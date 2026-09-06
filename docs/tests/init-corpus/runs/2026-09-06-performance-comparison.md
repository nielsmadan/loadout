# Init performance comparison — 2026-09-06

Both local median budgets now pass: 100 support files took 4.293 seconds (budget 15), and
1000 took 17.978 seconds (budget 120), compared with the saved baseline's 13.886 and 158.454.
These are local CLI measurements, not a cross-machine SLA.

## Method and identity

Run `.venv/bin/python tests/benchmark_init.py` from the Loadout checkout. The
[repeatable protocol](../README.md), [unchanged saved baseline](2026-09-06-performance-baseline.json)
and [post-fix raw results](2026-09-06-performance-comparison.json) provide all commands, exact
durations, operation counts, source hashes and correctness checks.

The comparison started at 2026-09-05 23:40:58 UTC (2026-09-06 local). Hardware, Python, Git, CLI,
fixture shape, isolation, concurrency, warmup, repetitions and 180-second timeout matched the
baseline: Apple M1 Max / MacBookPro18,2, 64 GiB RAM, macOS 26.6.2, Python 3.13.6, Apple Git
2.50.1, Loadout 0.1.0; one 25-file warmup and three measured runs each at 25/100/1000 files.
No other suite or heavy test ran concurrently with either measurement series.

Each fresh existing-Git repository contained one inert 87-byte Claude skill plus exactly N
1-KiB binary support files, all mode 0644. Fake HOME and cleared relocation variables prevented
live harness or Git configuration from entering the fixture. Init ran through the real CLI to
final staging; setup, output verification and later check/staged-check were outside its timer.
The runner additionally disables lazy Git fetching now; these synthetic repositories have no
remotes, so that setup guard does not alter their workload.

Both series used HEAD `ff9e7e8b268e456fa127a07da8848bc004e41141`. Baseline production source was
unchanged; comparison included Task 7's mode preservation, privacy batching and journal changes.
Its tracked source patch SHA-256 was
`6fb72d3cae393712e65bf3eacd4110c31d1d4d904e7445a4cc5fa0cc354bd7ff`; every Python source hash,
including the new privacy module, is in the JSON. Evidence roots were
`loadout-init-benchmark-4k5jjtn5` (baseline) and `loadout-init-benchmark-6v_eqjte` (comparison)
under the session temporary directory.
These historical series predate runner/fixture hash recording; current runs include those hashes
as well. Their original timing records have not been retrospectively assigned new runner hashes.

## Measurements

| support files | baseline raw seconds | comparison raw seconds | baseline median | comparison median | comparison range |
|---:|---|---|---:|---:|---|
| 25 | 5.817, 5.396, 5.410 | 3.675, 4.005, 3.682 | 5.410 | 3.682 | 3.675–4.005 |
| 100 | 13.876, 13.886, 15.535 | 4.441, 4.293, 4.232 | 13.886 | 4.293 | 4.232–4.441 |
| 1000 | 176.385, 158.454, 157.423 | 17.978, 16.885, 18.389 | 158.454 | 17.978 | 16.885–18.389 |

All nine comparison samples and the warmup completed, runner exit 0, no timeouts or skipped
samples. Every counted result passed exact bytes and mode 0644, generated-path index removals
and ignore checks, source staging, normal check and staged check. Operation counts remained
97/172/1072, with 26/101/1001 input and preserved output files totalling
25,687/102,487/1,024,087 bytes. Full-precision durations remain in the JSON.

## Attribution and tradeoffs

The saved baseline and separate diagnostic profile preceded optimization. Git ignore queries
were repeated per file during discovery and original retirement. Exact-parent batching cuts
those subprocesses while preserving logical/canonical and nested-repository semantics; a later
phase rechecks privacy instead of reading a stale cache. A live regression went from 50 probes
for 25 siblings to the bounded passing result.

Sentry also exposed large whole-journal rewrites; its diagnostic history is in the
[corpus report](2026-09-06-migration.md). V2 now writes a protected content-addressed operations/
metadata payload only when that payload changes. Each transition still atomically writes and
fsyncs its small cursor, and metadata transitions persist the new payload before referring to it.
V1 recovery remains supported and corrupt/missing/unprotected payloads are rejected.

This trades disk retention for much less repeated serialization and writing. Completed v2
cursors were 146/147/148 bytes, but retained payload versions totalled
1,486,655/4,330,625/38,488,945 bytes. Baseline v1 journals were
306,523/886,695/7,854,917 bytes. Keep the whole protected transaction directory when recovery
is needed; a copied cursor alone is insufficient. This task did not add journal garbage
collection or make a claim about peak memory.

There were no censored runs in either measured series. The current runner preserves an
interrupted cursor and payload directory before invoking supported recovery and reports a
nonzero overall exit when a timeout occurs. That timeout-only preservation guard was added
after the successful comparison; it did not run or alter any counted duration.

## Staged-check scale remains separate

| support files | comparison staged-check seconds | comparison range |
|---:|---|---|
| 25 | 0.563, 0.552, 0.527 | 0.527–0.563 |
| 100 | 0.808, 0.817, 0.829 | 0.808–0.829 |
| 1000 | 23.051, 22.845, 22.894 | 22.845–23.051 |

The baseline 1000-file staged checks took 22.587–22.834 seconds, so this work did not improve
that separate operation. Staged checking materializes complete repository HEAD/index blobs;
file/blob shape and unrelated assets matter, not just the count of Loadout source files.
Sentry's separate 20,842-path repository took 11.796 seconds for its final staged check. Do not
infer large-repository ergonomics from either the tiny source fixture or a single application.

## Recovery storage addendum — 2026-09-06

The timing and storage measurements above cover applying migration through final staging.
They did not exercise completed-transaction recovery. An independent review reproduced a
separate recovery defect: each restored operation updated `metadata.recovered_operations`,
publishing and retaining another complete payload. With 25 support files and 97 operations,
successful CLI recovery grew five payloads (1,336,570 bytes) into 102 (28,297,374 bytes).
The [original review reproduction](2026-09-06-recovery-defect.json) retains its commands and
measurements. Its runner/source hashes were not recorded, and have not been assigned later.

Recovery progress now lives in the small version-2 cursor. Both legacy journal versions still
import saved metadata progress; payload validation and protected preimages remain in place.
Reloading a version-2 journal reuses its verified payload. Each restored operation, including
an entry already at its preimage, persists its operation number before recovery continues.
Recovery also durably records `recovering` before restoring the index, allowing an interrupted
retry to recognize that index while still refusing foreign edits and forward resume.

The [fixed CLI reproduction](2026-09-06-recovery-cursor.json) used the same 25-support-file,
97-operation fixture. Its five payloads totalled 1,312,516 bytes both before and after recovery;
every payload hash was unchanged. Only the cursor grew, from 174 to 551 bytes. Absolute payload
sizes differ between fixtures because their recorded paths differ; the recovery delta is zero.
Init, recovery and a second recovery all exited 0 without a timeout. Original bytes and 0644
modes, baseline HEAD, the original index and a clean tracked worktree were verified; the authored
migration config was removed. No preimage garbage collection was introduced.

Reproduce the storage check from the checkout with:

```sh
.venv/bin/python tests/benchmark_recovery.py
.venv/bin/pytest -q tests/test_migration_recovery.py
```

The CLI runner records source and runner hashes and uses the benchmark's fake HOME, inert input
and Git isolation. This was a storage/correctness run alongside focused tests; its recorded
durations are diagnostic and are not a new timing comparison. An earlier runner attempt passed
the macOS `/var` alias to recovery and was refused with exit 3 (`artifact path is a symlink:
/var`). The runner now resolves its evidence directory before constructing journal paths. That
failed exploratory attempt is excluded from the passing result; its logs remain in the disposable
`loadout-recovery-cursor-evidence.lIavAj9zgt` directory.

The regression first failed after two durable restores, growing seven payloads / 1,836,918 bytes
to nine / 2,387,260 bytes. It now checks payload count and bytes at every recovery save across
interruption, reload and conflict retry, then verifies original bytes/modes, baseline HEAD and
an unrelated partially staged file. A separate test first reproduced the spurious index conflict
after interrupted recovery. Compatibility tests cover v1 and earlier v2 metadata progress,
repeated writes to one path, invalid cursor types/ranges and foreign index edits. The original
apply measurements above remain unchanged.
