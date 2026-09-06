# Mapped-global retirement profile — 2026-09-06

Git subprocess latency dominated the remaining 100-file migration cost. This diagnostic
selected the next optimization after the [second large-case timeout](2026-09-06-global-performance-second-fix.md).
It is not a controlled before/after benchmark.

## Procedure

The saved `profile_global.py` wrapper called the unchanged `benchmark_global_init.run_case`:
one unprofiled 25-file warmup, then two fresh 100-file cases with only init wrapped in
`python -I -m cProfile -o <case>/init.prof -m loadout`. Setup, normal check and staged check
were outside profiling. The complete original correctness assertions remained active.
The [source identity](2026-09-06-global-profile-before-identity.json) records `b06a06f` plus
the frozen second review-fix wave. No other heavy test ran during these two profiles.

Exact invocation from the checkout:

```sh
uv run python /var/folders/v_/dg_p8zcs2nlcg2xq_v834dlh0000gn/T/loadout-global-profile.zJcJNcr7F8/profile_global.py before
```

The wrapper, raw profiles, logs and identities remain in that temporary directory. The durable
[mapped-global benchmark](../README.md#mapped-global-sources) is the repeatable performance
acceptance procedure; profiling adds overhead and these samples must remain separate.

## Before optimization

Both real CLI migrations completed: [first result](2026-09-06-global-profile-before-1.json),
[second result](2026-09-06-global-profile-before-2.json). Each had 374 operations and 101 actual
retirements. Exact bytes/modes, source staging, original retirement/index removal, baseline,
normal check and staged check all passed, exit 0, without timeouts.

| Observation | First | Second |
|---|---:|---:|
| Init wall seconds | 22.042 | 21.382 |
| Profile total seconds | 21.963 | 21.301 |
| `migration_git.git` calls | 874 | 874 |
| Git cumulative seconds | 15.762 | 15.129 |
| `_guard_git` cumulative seconds | 16.657 | 16.028 |
| `privacy_policy` cumulative seconds | 9.217 | 8.830 |
| `_git_parse` cumulative seconds | 0.075 | 0.074 |
| `_entry_fingerprint` calls | 34,527 | 34,527 |
| Fingerprint cumulative seconds | 0.178 | 0.174 |

These cumulative times overlap through the call graph; do not add them. The primary session
independently read both raw profiles and complete result records. The evidence points to fresh
Git-query overhead, not parsing or fingerprint scans, as the next optimization target.
Any concurrent implementation must still finish every required observation before retirement.

## After concurrency

The same wrapper's `after` run completed in 16.163 and 15.419 seconds:
[first result](2026-09-06-global-profile-after-1.json),
[second result](2026-09-06-global-profile-after-2.json),
[source identity](2026-09-06-global-profile-after-identity.json).
All original correctness checks passed, with the same 101 retirements and 374 operations per
case. Neither run timed out. Git query forms remained unchanged; independent observations ran
concurrently through a bounded executor and completed before retirement.

Both profiles still observed 874 Git calls. A separate primary-session probe confirmed that this
installed Python 3.13.6 profiler records worker-thread calls: four worker invocations produced
four recorded calls. Do not assume thread coverage from a profiler's name; verify it in the
runtime used. Cumulative durations across overlapping calls are not additive wall time.
Independent thread-safe guard tests also compare the exact sequential/concurrent observations.

After profiling, the final index-byte check was moved after the concurrent privacy join to
preserve its original last-observation position. The diagnostic profiles therefore precede
that ordering-only refinement. The final unchanged benchmark, not these profiles, measures
the final source. No workload, timeout or correctness assertion was weakened.
