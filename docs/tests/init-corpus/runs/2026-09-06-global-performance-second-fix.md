# Mapped-global second-fix preflight — 2026-09-06

The second optimization still did not finish the 1,000-file mapped-global case within
180 seconds. This is a failed single-sample preflight, not a completed performance comparison.

```sh
.venv/bin/python tests/benchmark_global_init.py --counts 1000 --repetitions 1
```

The [unchanged procedure](../README.md#mapped-global-sources) used the same machine, inert
workload and runner as the [baseline](2026-09-06-global-performance-baseline.md). The runner
SHA-256 remained `4a6e4acddaf50b4498c5a55e1f73622252c042104b47b89c57d10bf295aac463`.
Started 2026-09-06 01:55:36 UTC on `b06a06f` plus two frozen review-fix waves. Source and
runner identities are in the [complete JSON](2026-09-06-global-performance-second-fix.json).
No other heavy test or source edit ran during measurement.

The 25-file warmup passed all correctness assertions in 6.254 seconds. The only measured
1,000-file sample timed out at 180.005 seconds; the runner exited 1. There is no completed
large-case median or successful large-case check/staged-check result.

The runner preserved the complete interrupted journal and used supported recovery, which exited
0 in 4.785 seconds with no conflicts. The primary session then independently verified all 1,001
originals' bytes and full 0644 modes, unchanged baseline HEAD, clean tracked index/worktree and
removal of the generated global manifest. These recovery assertions passed; they do not make
the timed-out migration a success. Raw evidence remains under `loadout-global-benchmark-azg1nei7`
in the session temporary directory. No live home or agent configuration changed.

Logical-parent deduplication removed repeated queries but was insufficient for this workload.
The next pass uses a bounded real-CLI profile to choose the remaining optimization. Earlier
failed runs remain published separately.
