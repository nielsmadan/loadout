# Final init corpus verification — 2026-09-06

The final source reproduced all 14 public-case outcomes: **five project migrations and nine
unchanged refusals**. The separate four-harness synthetic global migration also passed.
Runner exit 0; no cases were filtered, skipped or timed out. A refusal is not a conversion.

## Run and identity

```sh
.venv/bin/python tests/run_init_corpus.py \
  --project-inputs /private/tmp/loadout-init-spike-final.3sPbeZ \
  --global-inputs /private/tmp/loadout-global-init-spike.tNsaJU \
  --opencode-input /var/folders/v_/dg_p8zcs2nlcg2xq_v834dlh0000gn/T/loadout-init-corpus-kqpvhgni/opencode/repo-public \
  --synthetic
```

Started 2026-09-06 02:29:49 UTC. The [complete record](2026-09-06-final-verification.json)
identifies every pinned upstream, command, exit, source hash and comparison. Code was `b06a06f`
plus the final review fixes, tracked source patch SHA-256
`874198b40a9649bb88e273e2afb060454b35891a1821986871a0c1fd9184ac49`.
Runner SHA-256: `56c6dd2acffd0e36ae9550448f3268feb35fe9feca2fda0eae92535bcf1607ff`.
The [procedure](../README.md) and [pinned manifest](../../../../tests/fixtures/init_corpus.json)
define the inputs and isolation. Retained public clones were reused read-only; each run used
fresh fixture repositories and fake homes. No upstream script, installer or skill executed.

This was a correctness run alongside the full suite, after controlled performance timing had
finished. Its command durations are not performance comparison samples. Raw evidence remains
under `loadout-init-corpus-0uwbv0bi` in the session temporary directory.

## Successful reconstructions

| Input | Compared outputs | Git-only outputs | With explicit private source |
|---|---:|---:|---:|
| semantic-release-gitlab | 1 | 1 | Not needed |
| pypistats | 1 | 1 | Not needed |
| xcbeautify | 2 | 2 | Not needed |
| agent-harness | 47 | 47 | Not needed |
| sentry | 176 | 14 | 176 |
| synthetic-global, separate from public corpus | 10 | 10 | Not needed |

All 227 public outputs and 10 synthetic outputs passed comparison. Sentry's 96 Claude allow
entries remained completely equal and in their original order, ordered SHA-256
`cb51d0a9bcc326d4c7ab5a371ec1e31542606503a74a6a2ca6d673f0a9c9a5c6`.
Its public reconstruction used 40 staged source files. Supplying only its 164 serialized private
source files then reconstructed all 176 outputs; no obsolete originals or journal were supplied.

The final oracle derives required output inventory from discovered original candidates, not
the planner's generated-write list. A joint planner/renderer omission cannot silently shrink
that expected inventory. Original opaque modes remain independently checked. Normal check,
sync, repeat init, staged validation and applicable skill add/edit/rename/delete flows passed.

## Unchanged refusals

The four public projects `agent-skills`, `claude-code-showcase`, `mcp-servers` and `opencode`
still require source/category mappings, and in some cases dependency mappings. All five global
inputs remain unresolved:

| Global input | Unresolved categories |
|---|---|
| integralist-agent-skills | Source mapping, activation, unsupported harness, dependencies |
| dwmkerr-dotfiles | Source mapping, activation |
| dbochman-dotfiles | Source mapping, activation, dependencies |
| archibate-dotfiles-claude | Source mapping, dependencies |
| anaiis-dotfiles | Source mapping, activation, dependencies |

Each unresolved preview and apply attempt exited 2. The runner verified unchanged contents,
modes, index, HEAD and fixture-home state. These 18 expected refusals were the only nonzero
command exits in the complete record; there were no timeouts. The JSON retains exact paths
and issue messages for follow-up mapping. Mechanical init does not infer arbitrary installer
behavior or execute programs to discover it.

## Limits

This purposive corpus does not measure ecosystem prevalence or prove harness execution.
Complicated public global setups still need reviewed mapping/materialization decisions; the
synthetic global pass does not substitute for them. Private trees and protected recovery
preimages remain local. No live home or installed agent configuration changed.

## Final checkout and package checks

The primary session's `just check` exited 0: Ruff clean, 225 files formatted, strict mypy clean
across 55 source files, and **1,668 tests passed in 502.53 seconds**, with no failures, errors
or skips. The separate [current CLI QA](../../qa-init-migration/runs/2026-09-06-final.md)
passed all 12 cases in 40.19 seconds. These checks used the same final production source.

`uv build --wheel` succeeded. The primary session compared every file under `src/loadout`
against the archive: all 86 files were byte-identical, including three skill files and 28
starter files. Wheel SHA-256:
`480cb144bc9dacfde2409e873bbc2605176b22c25392dac6fc08fcc6cd8bcfe7`.
The wheel is retained under `loadout-init-final-package.mcBbqDq4Np` in the session temporary
directory. This verifies packaging, not normal agent-host loading; that gap remains explicit
in the CLI QA report. The package was not installed into live machine configuration.
