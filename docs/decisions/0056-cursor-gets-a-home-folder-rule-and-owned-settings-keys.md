# 0056 — Cursor gets a home-folder rule and owned settings keys

**Status:** accepted (2026-11-10, ruled by the user). Extends Cursor support
([0054](0054-cursor-cli-is-a-supported-harness.md)).

## Context

Cursor shipped with two global slices missing, and a user adding `[cursor]` to their global
source set both by hand.

- **Instructions.** Cursor has no instructions file in its config folder, so
  [cursor.md](../reference/cursor.md#instructions) recorded global instructions as verified
  negative. Its rules loader does read `.cursor/rules/` in every folder above the workspace, and a
  signed-in headless run on 2026-11-09 showed a rule in `~/.cursor/rules/` applied, but only when
  its frontmatter came first. Loadout puts its generated header first in every instructions
  document, so even a hand-declared `[instructions.<name>]` target there produced a rule Cursor
  ignored.
- **Settings.** `approvalMode`, `sandbox.mode` and the default model live in `cli-config.json`,
  which the CLI shares with sign-in, caches and prompt counters and rewrites on every start.
  Loadout owned its `permissions` key only, while Claude's `defaultMode` rides on a settings
  fragment and Codex's `approval_policy` on `defaults`.

## Decision

- **Global instructions render to `~/.cursor/rules/loadout.mdc`.** It applies to every workspace
  under the home folder and stays there when `CURSOR_CONFIG_DIR` moves the rest of Cursor's
  config, because Cursor joins the rules path onto the workspace's ancestors, not onto that
  variable. Init routes nothing there: the file is one loadout names.
- **Any instructions destination ending in `.mdc` starts with `alwaysApply: true` frontmatter**,
  ahead of the generated header. The rule is the destination's, so a stealth source's project rule
  and a hand-declared target get it the same way.
- **`settings` under `[cursor]` names JSON fragments whose leaves loadout owns in
  `cli-config.json`.** It rides on the permissions slice, as Claude's does, and keeps an owned-key
  record beside the fragment, as Codex `defaults` does (0017), so a dropped key is removed. Leaves
  rather than top-level keys, because the CLI keeps its own keys inside `sandbox`. `$remove` is
  refused, and so is a fragment setting `permissions`.
- **Loadout takes the default model over even though the CLI rewrites it.** Any `--model` run
  saves over `selectedModel`. That shows as drift in `check`, and `sync --force` restores it, as
  for any owned key edited outside loadout; the user ruled that a documented drift is better than
  leaving the model unmanaged.

## Consequences

- A workspace outside the home folder gets no global Cursor instructions, and the docs say so.
- A global source whose `[all]` names instructions starts writing the Cursor rule on its next sync
  once it declares `[cursor]`.
- `apply_json` owns dotted paths, not only top-level keys, and drops a table its removals empty.
  Claude's `mcpServers` and Cursor's `permissions` are single-segment paths, so their output is
  unchanged.
- Init still adopts only `permissions` from `cli-config.json`. The CLI writes about twenty
  defaults into it on first start, so adopting the rest would pin values the user never chose.
