# Init compatibility corpus — 2026-09-05

**2026-09-06 addendum:** these observations describe the scaffold implementation at `86abb1b`.
The project file called `loadout.toml` below was `loadout/config.toml`; the global manifest remains
`loadout/loadout.toml`. The recommendation to keep init scaffold-only is historical and was
superseded by the transactional migration implementation. See the [current procedure and runs](init-corpus/README.md).
The old OpenCode revision claim is uncertain: its retained checkout later had HEAD `e2894562`,
while the pinned object was `5cf9f517`. The new run explicitly checks out the pin. The old
96-to-zero Sentry observation remains an adoption-loss sentinel, not a successful migration.

This report records the first manual run of the
[init compatibility corpus](init-corpus-flow.md). It tested the Loadout checkout at
`86abb1b` plus the clean working tree at the start of the run.

## Summary

Mechanical initialization is good at creating a small greenfield scaffold. It is not a
migration command.

- Project `init` succeeded in all nine repositories and produced a minimal source. It warned
  about some root instruction files but did not import existing setup or infer harnesses.
- Project `sync` then made the generated paths agree with that empty source. In the Sentry
  stress case this removed 96 existing Claude allow entries. The pre-sync check exposed the
  change, but sync did not require adoption confirmation.
- Global `init` succeeded in all five personal-config repositories, added only an untracked
  nested `loadout/` source and left every tracked file byte-for-byte untouched.
- Global `check` and `sync` both stopped with exit 3 because the new manifest intentionally
  declared no targets. This prevented an empty scaffold from touching live destinations.
- None of the tested global repositories' existing instructions, settings, permissions, MCP
  configuration, hooks, skills, harnesses or deployment symlinks were discovered or imported.

The safe split is therefore: keep `loadout init` as a greenfield scaffold, add stronger
preflight protection around project adoption, and use the `/loadout` skill for conversion of an
existing project or global setup. A separate `/loadout-init` skill would duplicate that
onboarding path without making the mechanical command more capable.

## Project corpus

The project run used the nine pinned commits and harness selections in the flow document.

### Common result

| stage | result across all nine cases |
|---|---|
| `loadout init` | exit 0 |
| first `loadout check` | exit 1; generated output was absent or differed |
| first `loadout sync` | exit 0; reported that there was no committed baseline and skipped the external-modification check |
| second `loadout check` | exit 0 |

Across the eight ordinary cases, initialization created only:

- a 22–44 byte project `loadout.toml`, depending on selected harnesses;
- a 385 byte empty permissions source;
- an empty personal permissions source;
- two to eight `.gitignore` entries for the personal source and generated destinations.

This is appropriately small for a new repository. The same behavior is insufficient for an
existing repository because no current content is moved into source.

### Existing setup observations

- Existing root `CLAUDE.md` and `AGENTS.md` files produced warnings. The warning surface did not
  inventory nested instructions or other agent artifacts.
- Harness selection was entirely the caller's `--harness` choice. Existing directories and
  files did not enable a harness automatically.
- `cpisciotta/xcbeautify` used `CLAUDE.md -> AGENTS.md`; `CodelyTV/agent-harness` used harness
  skill directories pointing at a shared `.agents/skills` tree. Initialization did not model
  either topology.
- Existing settings, permission rules, MCP definitions, hooks and skills were not imported.
- `.gitignore` was expanded for all preset outputs, including outputs for slices with no source
  content yet. That is mechanically consistent but can make the scaffold look more complete
  than the migration actually is.

### Sentry destructive-adoption sentinel

At `getsentry/sentry@a7b4a8a2a78a666418e527d812e81528147789da`, the existing
`.claude/settings.json` contained 96 allow rules. The first check showed that the empty Loadout
source would delete them. Sync printed the no-baseline warning, succeeded, and the resulting
file contained zero allow rules; unrelated top-level keys were preserved.

This is consistent with the documented first-sync adoption behavior, but it demonstrates the
practical hazard: a user can see the diff and still run a mechanical sync without first moving
the owned fields into Loadout source. Existing projects need either a refusal/preflight in the
command or agent-guided conversion before that sync.

## Global corpus

The five repositories were public, non-archived and non-forks when selected. They are examples
of real personal configuration sources, analogous in role to `~/ac`, not evidence of ecosystem
prevalence.

Before initialization, two independent complete tree listings agreed on each repository's path
count and found no `loadout.toml`:

| repository | commit | tracked paths | existing Loadout manifests |
|---|---|---:|---:|
| `Integralist/agent-skills` | `ed3462c8d284e81481f300c4808068aff4117861` | 145 | 0 |
| `dwmkerr/dotfiles` | `f9c841a67478f4c07a006afdfd6e49f1cc65b109` | 136 | 0 |
| `Dbochman/dotfiles` | `f72c03a8c9d262119634f0af370d86437953ef0b` | 681 | 0 |
| `archibate/dotfiles-claude` | `3cd0d62c58c5cbdf3c8493cab0fe2c807af4e48c` | 1,743 | 0 |
| `datasci-iopsy/anaiis-dotfiles` | `2f5d1487e8704eed299233aaef57e4f38d17cb1e` | 126 | 0 |

### Command results

Every global case produced the same result:

| stage | exit | result |
|---|---:|---|
| `loadout init --global --source <clone>` | 0 | created the nested source and isolated machine config |
| tracked `git diff --exit-code` | 0 | no tracked file changed |
| `loadout check --global` | 3 | refused a manifest with no declared targets |
| `loadout sync --global` | 3 | refused the same manifest before rendering destinations |
| repeated `loadout init --global` | 3 | refused because the isolated machine config already existed; suggested `--force` |

After init, `git status --short` showed only `?? loadout/`. The added tree was identical in all
five repositories:

```text
loadout/
├── instructions/
│   └── .gitkeep                 0 bytes
├── loadout.toml               748 bytes
└── permissions.toml           298 bytes
```

The manifest declared one `global` source at `.` and only commented examples of instruction and
permission targets. The isolated machine config pointed at the resolved nested source path.

### What the scaffold missed

The identical output is evidence of both safety and blindness:

- `Integralist/agent-skills` already coordinates several harnesses and shares skills through
  symlinks. A migration must separate canonical content from harness-specific destinations.
- `dwmkerr/dotfiles` stores Claude, Codex and OpenCode setup inside a wider dotfiles system. A
  migration must preserve the surrounding deployment conventions or replace them explicitly.
- `Dbochman/dotfiles` has instructions, settings, rules, hooks and skills for Claude and Codex,
  plus install/sync logic with backup and conflict behavior. None became Loadout source.
- `archibate/dotfiles-claude` is designed to be cloned directly as `~/.claude`. Mechanical init
  would put `loadout/` inside that harness directory; this is safe but an awkward source layout.
- `datasci-iopsy/anaiis-dotfiles` symlinks a canonical checkout into `~/.claude`. Migration must
  decide when Loadout replaces those symlinks with direct destinations and what remains owned by
  the existing installer.

These are not exceptional parsing problems. They require ownership decisions: which files are
source, which are generated, which unsupported artifacts stay with the old installer, and which
destinations Loadout may take over. An agent can inspect those relationships and ask the user
when ownership is ambiguous; a generic scaffold cannot infer them safely.

## Additional environment finding

During an earlier project run, a sandbox denied the optional read of the machine config after
project files had already been written. Init exited 4 with a partially created project
scaffold. After the exact config path was granted and the session restarted, the corpus ran
normally.

This did not affect the product conclusions above, but it exposes an ordering issue worth
testing separately: preflight reads that may fail should happen before project mutations, or a
failed init should roll its writes back.

## Decision carried forward

Mechanical init should promise a deterministic, minimal greenfield source and refuse dangerous
adoption states it can identify. The `/loadout` skill should own existing-setup onboarding for
both scopes:

1. inventory configured harnesses and all relevant artifacts;
2. distinguish canonical files from symlinked or copied destinations;
3. extract representable instructions, permissions, settings, MCP configuration and skills;
4. report unsupported content and ambiguous ownership to the user without changing anything;
5. show the proposed source layout and destinations;
6. initialize or edit Loadout only after confirmation;
7. run check, show the full adoption diff, sync, and check again.

That keeps the command mechanically safe and the skill useful for the part that actually needs
judgment.
