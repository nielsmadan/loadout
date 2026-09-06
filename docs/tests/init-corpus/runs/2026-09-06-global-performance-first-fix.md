# Mapped-global first fix — 2026-09-06

The first optimization reduced the 25-file median from 18.165 to 6.146 seconds, but the
1,000-file case still exceeded the 180-second command limit. This run is incomplete and does
not establish acceptable large-source performance.

## Procedure and identity

```sh
.venv/bin/python tests/benchmark_global_init.py
```

The [procedure](../README.md#mapped-global-sources), machine and workload match the
[baseline](2026-09-06-global-performance-baseline.md). The runner was unchanged, SHA-256
`4a6e4acddaf50b4498c5a55e1f73622252c042104b47b89c57d10bf295aac463`.
Run started at 2026-09-06 01:33:19 UTC on `b06a06f` plus the first review-fix wave. The
[complete partial results](2026-09-06-global-performance-first-fix.json) identify every source
file and the tracked production patch. Production was frozen throughout the measurements and
recovery. A reviewer inspected files concurrently; no other heavy test or full suite ran.

## Results

| Support files | Completed samples, seconds | Median | Outcome |
|---|---|---:|---|
| 25 | 5.862, 6.567, 6.146 | 6.146 | All three passed |
| 100 | 20.611, 20.459, 21.341 | 20.611 | All three passed |
| 1,000 | None | — | First timed out at 180.006 seconds |

The 25-file warmup passed in 5.874 seconds. Each completed sample passed exact output bytes and
modes, original retirement and index removal, staged source, unchanged baseline HEAD, check and
staged-check assertions. The 25-file cases had 149 operations and 26 retirements; the 100-file
cases had 374 operations and 101 retirements. These are mapped-global results, not the separate
in-place project benchmark.

The runner recovered the first timed-out case through `loadout init --recover` (exit 0,
3.622 seconds). Its complete interrupted journal was retained first. During the second
1,000-file repetition, the run was cancelled rather than spending another two timeout periods
on the same unresolved bottleneck. The runner exited 130; that cancellation is not a timing
sample, and the third repetition never ran.

The second fixture's complete journal was also preserved before supported recovery. Recovery
exited 0 in 3.538 seconds with no conflicts. All 1,001 original files were checked byte-for-byte
and at mode 0644; baseline HEAD was unchanged, tracked worktree and index were clean, and the
generated global manifest was removed. The [recovery record](2026-09-06-global-performance-first-fix-recovery.json)
is separate because cancellation interrupted the runner before it could record that sample.
Raw logs and both protected interrupted journals remain under `loadout-global-benchmark-blu55j0q`
in the session temporary directory. No real home or agent configuration was changed.

The remaining repeated privacy-path traversal was selected for a second optimization pass.
These failed and cancelled samples are retained; a later successful run must be recorded
separately.
