# Init performance baseline — 2026-09-06

Measured on local date 2026-09-06 (start 2026-09-05 23:01 UTC) at
`ff9e7e8b268e456fa127a07da8848bc004e41141`, with production source unchanged. The newly authored
runner was `tests/benchmark_init.py`; its workload and commands are captured in the
[machine-readable baseline](2026-09-06-performance-baseline.json). Run from the Loadout checkout
with `.venv/bin/python tests/benchmark_init.py`. One earlier warmup was discarded because the
runner supplied Git check-ignore's `-z` without `--stdin`; no measured samples came from that run.

Apple M1 Max / MacBookPro18,2, 64 GiB RAM, macOS, Python 3.13.6, Apple Git 2.50.1,
Loadout 0.1.0. One 25-file warmup, then three sequential runs at each size, concurrency one.
Every fresh existing Git fixture had one inert Claude skill plus exactly N 1-KiB binary support
files, mode 0644. Each init had a 180-second cap. Timing includes the real CLI through final
staging; fixture setup and subsequent correctness checks are outside the init timer.

| support files | raw init seconds | median | range | operations | input bytes |
|---:|---|---:|---|---:|---:|
| 25 | 5.817, 5.396, 5.410 | 5.410 | 5.396–5.817 | 97 | 25,687 |
| 100 | 13.876, 13.886, 15.535 | 13.886 | 13.876–15.535 | 172 | 102,487 |
| 1000 | 176.385, 158.454, 157.423 | 158.454 | 157.423–176.385 | 1072 | 1,024,087 |

All nine samples completed: init/check/staged-check exit 0, exact output bytes and 0644 modes,
source staged, generated paths removed from the index and ignored. The 100-file median met the
local 15-second budget, although one sample exceeded it. The 1000-file median exceeded the
120-second budget. These are local usability budgets, not a cross-machine SLA.

The 1000-file journals were 7,854,917 bytes and were rewritten at every cursor update. The
separately timed staged checks took 22.587–22.834 seconds at 1000 support files (0.809–0.897 at
100). Staged checking snapshots the repository's HEAD and index, not just Loadout files; this
fixture does not establish large-application-repository ergonomics.

After saving this baseline, a separate diagnostic profile at 100 files recorded 202 discovery
ignore queries and 303 transaction original-privacy queries; together they consumed about
9.7 seconds of a 17.1-second profiled invocation. Journal saving took 1.47 seconds across 350
calls. The profile ran alongside corpus work and is diagnostic attribution, not another timing
sample. This motivated batching sibling privacy checks before redesigning transaction storage.
Raw profile and command logs remain in disposable local evidence, not in Git.
