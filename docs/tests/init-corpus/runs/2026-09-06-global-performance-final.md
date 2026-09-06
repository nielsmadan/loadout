# Mapped-global final comparison — 2026-09-06

All ten init runs completed with their correctness checks: one warmup and nine measured
samples. The 25-file median fell from 18.165 to **4.168 seconds**. The 100-file median was
**11.818 seconds**. The 1,000-file median was **131.393 seconds**: below the unchanged
180-second command cap, but **above the 120-second target**. That target remains unmet.

## Procedure and identity

```sh
.venv/bin/python tests/benchmark_global_init.py
```

The [procedure](../README.md#mapped-global-sources), hardware and inert workload match the
[baseline](2026-09-06-global-performance-baseline.md). Run started at 2026-09-06 02:21:39 UTC.
Production was `b06a06f` plus the final review fixes, with tracked source patch SHA-256
`874198b40a9649bb88e273e2afb060454b35891a1821986871a0c1fd9184ac49`.
The [complete measurements](2026-09-06-global-performance-final.json) include every production
file hash. The primary session verified that all hashes still matched after measurement.

The runner was unchanged throughout the baseline and all comparisons, SHA-256
`4a6e4acddaf50b4498c5a55e1f73622252c042104b47b89c57d10bf295aac463`.
One 25-file warmup preceded three fresh samples each at 25, 100 and 1,000 support files.
Init timers exclude setup and subsequent check/staged-check commands. No other heavy test ran
during timing; concurrent work was limited to read-only review, lightweight inspection and docs.
The full correctness suite and corpus rerun began only after this runner exited.

The earlier [single-sample final preflight](2026-09-06-global-performance-final-preflight.json)
also passed at 132.935 seconds. It is not included in the three-sample aggregate below.
The [first](2026-09-06-global-performance-first-fix.md) and
[second](2026-09-06-global-performance-second-fix.md) failed comparisons, including recovery,
remain separate records. The [profile](2026-09-06-global-profile.md) explains the measured
Git-process bottleneck; its instrumented timings are not mixed into this benchmark.

## Results

| Support files | Three init samples, seconds | Median | Range |
|---|---|---:|---|
| 25 | 4.168, 4.168, 4.163 | 4.168 | 4.163–4.168 |
| 100 | 11.830, 11.379, 11.818 | 11.818 | 11.379–11.830 |
| 1,000 | 130.766, 131.394, 131.393 | 131.393 | 130.766–131.394 |

Warmup: 4.486 seconds. Runner exit 0; every init, normal check and staged check exited 0,
without timeouts, omitted samples or skips. Every successful duration required exact output
bytes and full modes, original retirement and index removal, staged global source, unchanged
baseline HEAD, normal check and staged validation.

| Support files | Operations | Actual retirements / outputs | Output bytes | Check range, seconds | Staged range, seconds |
|---|---:|---:|---:|---|---|
| 25 | 149 | 26 | 25,687 | 0.184–0.185 | 0.456–0.481 |
| 100 | 374 | 101 | 102,487 | 0.339–0.379 | 0.530–0.596 |
| 1,000 | 3,074 | 1,001 | 1,024,087 | 2.214–2.449 | 1.446–1.579 |

These global migrations retire distinct mapped originals. The separate project benchmark
regenerates in place and has zero retirements; its timings are not interchangeable.

## Remaining limit and retention

The 100-file case meets the 15-second local target; the 1,000-file case misses 120 seconds by
11.393 seconds. The guard optimization is retained because every sample completes correctly,
including the previously timed-out workload. The target is not raised and no correctness or
fresh-observation check is removed to obtain a passing label. Further large-source optimization
remains a measured performance opportunity, not a claim resolved by this run.

Exact replacement bytes/modes are still checked before each original retirement. Independent
read-only Git queries may overlap, but all finish before mutation, followed by the final index
observation. Full resume/final checks and old or ambiguous journal fallbacks remain intact.

Raw logs, generated source and protected journals remain under `loadout-global-benchmark-qji_ncum`
in the session temporary directory. No real home, installed skill or live hook configuration
changed. This local result is not a latency guarantee for different repositories or machines.
