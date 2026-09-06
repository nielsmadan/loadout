# Mapped-global migration baseline — 2026-09-06

Migrating 25 support files from a global source repository took a median **18.165 seconds**.
The previous project benchmark had no obsolete originals to retire and did not cover this path.
This is a local baseline before the whole-feature review fixes, not a final performance result.

## Procedure and identity

```sh
.venv/bin/python tests/benchmark_global_init.py --counts 25
```

See the [repeatable procedure](../README.md#mapped-global-sources) and
[complete measurements](2026-09-06-global-performance-baseline.json). Run started at
2026-09-06 01:02:34 UTC. Production source was exactly
`b06a06f02dda2fb40bc12247a80e5c55fbc0448c`; its tracked source diff was empty throughout.
The newly added runner had SHA-256
`4a6e4acddaf50b4498c5a55e1f73622252c042104b47b89c57d10bf295aac463`.
All production and shared runner/fixture hashes are in the JSON.

Environment: Apple M1 Max / MacBookPro18,2, 64 GiB RAM, macOS 26.6.2, Python 3.13.6,
Apple Git 2.50.1, Loadout 0.1.0. Each fresh existing-Git repository held one inert 87-byte skill
and 25 fixed 1-KiB supporting files, all mode 0644. An explicit mapping deployed its `.claude`
source to a separate fake HOME. Harness relocation and Git overrides were cleared; identity
existed only in the disposable repository. No live home, agent or hook configuration changed.

CLI init ran sequentially: one 25-file warmup and three measured repetitions. Setup and later
check/staged-check commands are outside the init timer. Reviewers performed read-only inspection
and small diagnostics concurrently; no other full suite or heavy test ran. Keep that limitation
when comparing sub-second differences. Raw evidence remains under
`loadout-global-benchmark-rteuj4wn` in the session temporary directory.

## Results

| Sample | Init seconds | Check seconds | Staged check seconds |
|---|---:|---:|---:|
| Warmup | 18.926 | 0.186 | 0.459 |
| 1 | 18.862 | 0.195 | 0.475 |
| 2 | 17.556 | 0.189 | 0.464 |
| 3 | 18.165 | 0.186 | 0.466 |

Measured median: 18.165 seconds; range: 17.556–18.862. All four init commands and all subsequent
checks exited 0, with no timeouts, omitted samples or skips. Every sample had 149 operations,
including **26 actual retirements**, and preserved 26 outputs totalling 25,687 bytes. The runner
verified every output's bytes and mode, removal of every mapped original and its index entry,
staged global source, unchanged baseline HEAD, normal check and staged validation.

The review traced repeated whole-migration Git/privacy/completed-output checks before each
retirement. A separate diagnostic also found repeated private-path normalization during
preparation; this public-source benchmark does not isolate that private-tree cost. The records
here measure the complete CLI workload, not exclusive attribution to one function.
