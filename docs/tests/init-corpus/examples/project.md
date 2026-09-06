# Small project migration example

This is original synthetic test data, executed through the real CLI on 2026-09-06 at 02:09 CEST.
The [complete record](../runs/2026-09-06-project-example.json) contains all three original files,
all 31 authored source files, all three outputs, and their fresh Git-index reconstruction.
No third-party excerpts, private source or recovery preimages are included.

## Before

The project had Claude and Codex guidance plus `.claude/settings.json`:

```json
{
  "permissions": {
    "allow": [
      "Bash(git status)",
      "Read(src/**)"
    ]
  },
  "model": "qa-model"
}
```

`CLAUDE.md` contained `Existing Claude guidance.` followed by CRLF; `AGENTS.md` contained
`Existing Codex guidance.` followed by LF. These files were uncommitted. An unrelated note had
different HEAD, index and worktree versions.

## Loadout source

`loadout/config.toml` is the entry point:

```toml
harnesses = ["claude", "codex"]
presets = false
artifacts = "artifacts.toml"
```

`loadout/artifacts.toml` binds the output to separately editable categories:

```toml
[[artifact]]
agents = ["claude"]
output = ".claude/settings.json"
format = "json"
order = ["permissions", "model"]
emit_empty = true

[artifact.parts.settings]
source = "settings/native/claude/.claude/settings.json"

[artifact.parts.permissions]
source = "permissions/native/claude/.claude/settings.json"
keys = ["permissions"]

[artifact.parts.hooks]
source = "hooks/native/claude/.claude/settings.json"
keys = ["hooks"]

[artifact.parts.plugins]
source = "plugins/native/claude/.claude/settings.json"
keys = ["enabledPlugins", "extraKnownMarketplaces"]
```

The settings source contains only `{"model": "qa-model"}`; the permissions source contains
the complete ordered `permissions` object. Hooks and plugins start as `{}`. The two instruction
sources live at `instructions/native/claude/CLAUDE.md` and `instructions/native/codex/AGENTS.md`,
with the original bytes; their copy routes record mode 420 (0644).

The full artifact index and empty category files are in the record. Every category has a place
to begin; arbitrary module/support filenames need an explicit binding. Empty routed files do
not create extra instruction outputs.

## After and reconstruction

All three output files equal their originals in this example, including permission order and
the different instruction line endings. They remain at the harness-required root paths, now
generated and ignored. Authored source, output removals from the index and ignore changes are
staged. The baseline commit changes only the three adopted configuration files; the unrelated
note's HEAD/index/worktree versions remain distinct.

The test exported only the 31 authored Loadout files using `git checkout-index` into an empty
directory, then ran `sync` and `check` there. All three reconstructed outputs match. It copied
no original harness files, ownership receipts, journal or machine registration. Later in the
same case, a source edit synced successfully and an external output edit was preserved/refused.

## Repeat this example

Use the [CLI QA prerequisites and isolation](../../qa-init-migration/README.md), then run:

```sh
qa_evidence=$(mktemp -d -t loadout-init-project-example)
LOADOUT_QA_REPORT_DIR="$qa_evidence" uv run pytest -q tests/manual_init_qa.py::test_populated_project_sources_index_and_drift
```

Recorded result: **1 passed in 5.88 seconds**, exit 0, no skips. Revision `ff9e7e8` plus Task7
source changes; exact source and driver fingerprints are in the JSON. This is one synthetic
project example, separate from the 14 pinned public corpus cases and the global example.
