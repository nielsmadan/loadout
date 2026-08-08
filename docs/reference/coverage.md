# Coverage inventory

Every behaviour `docs/reference/` records, and the test that pins it. Written to answer one
question before the captured fixtures were replaced: **if `tests/golden/` disappeared, what
would stop being covered?**

The answer is narrower than expected. All 55 tests in `tests/test_permissions_renderers.py`
construct `Rules(...)` directly from synthetic entries — `Rules(allow=("foo", "bar"),
deny=("foo",))` — so every matcher and ordering quirk is already pinned independently of any
fixture. What the whole-document comparison uniquely covers is **wiring**, not semantics.

Keep this file current: a new behaviour in `docs/reference/` needs a row, and a row without a
test is a gap.

## Cross-cutting

| id | behaviour | source | pinned by |
|---|---|---|---|
| `last-match-opencode` | denies emitted after the allows they refine | [README](README.md#order-independent-vs-last-match) | `test_opencode_emits_deny_after_allow_for_last_match_wins` |
| `last-match-pi` | deny emitted last | [pi](pi.md#resolution) | `test_pi_emits_deny_last_so_it_wins_under_last_match` |
| `pi-moves-key` | Pi deletes and reinserts so an overwritten key moves to the end | [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md) | `test_pi_reorders_cross_key_entries_so_later_category_wins_position` |
| `opencode-keeps-key` | OpenCode assigns in place, so the key stays put — deliberately unlike Pi | [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md) | `test_opencode_does_not_reorder_cross_key_entries` |
| `dedupe-order` | `dedupe()` is order-preserving and never `set()` | [README](README.md#order-independent-vs-last-match) | `test_dedupe_preserves_order` |
| `glob-literal` | a trailing-`*` entry is kept literal on Claude, OpenCode, Pi | [README](README.md#globs) | `test_claude_pattern_keeps_a_glob_literal`, `test_opencode_keeps_a_glob_literal`, `test_pi_keeps_a_glob_literal` |
| `glob-skipped` | Codex and Antigravity cannot express a glob, so it is skipped | [README](README.md#globs) | `test_codex_skips_globs_and_lists_them_at_the_end`, `test_antigravity_skips_globs_its_matcher_cannot_express` |
| `glob-block-absent` | no trailing skipped-block when there are no globs | [codex](codex.md#pattern-shape) | `test_codex_omits_the_skipped_block_when_there_are_no_globs` |
| `claude-colon-star` | Claude needs the `:*` suffix, which matches bare and with-args | [claude](CLAUDE.md#pattern-shape) | `test_claude_pattern_appends_colon_star_to_a_prefix` |
| `both-forms` | OpenCode and Pi need both `<entry>` and `<entry> *` | [README](README.md#bare-vs-with-arguments) | `test_opencode_emits_both_bare_and_argument_forms`, `test_pi_emits_both_bare_and_argument_forms` |
| `purity` | renderers are pure and read no files | [ADR 0001](../decisions/0001-render-never-reads-its-own-output.md) | `test_renderers_are_pure`, `test_claude_never_reads_a_file` |
| `no-base-mutation` | a renderer never mutates the base it was handed | [ADR 0001](../decisions/0001-render-never-reads-its-own-output.md) | `test_claude_does_not_mutate_its_base`, `test_opencode_does_not_mutate_its_base`, `test_antigravity_does_not_mutate_its_base` |

## Claude

| id | behaviour | source | pinned by |
|---|---|---|---|
| `claude-owned-first` | owned keys precede hand-maintained ones inside `permissions` | [claude](CLAUDE.md#what-loadout-emits) | `test_claude_orders_owned_keys_before_hand_maintained_ones` |
| `claude-key-position` | `permissions` keeps its position in the base document | [claude](CLAUDE.md#what-loadout-emits) | `test_claude_keeps_the_permissions_key_at_its_position_in_the_base` |
| `claude-concat-order` | shell, then MCP, then extras | [claude](CLAUDE.md#what-loadout-emits) | `test_claude_concatenates_shell_then_mcp_then_extras` |
| `claude-ask-no-extras` | `ask` has no extras channel | — (test is the record) | `test_claude_ask_has_no_extras_channel` |
| `claude-empty-rules` | `rules = []` empties all three lists — the autonomous profile | [claude](CLAUDE.md#what-loadout-emits) | `test_claude_with_empty_rules_empties_all_three_lists` |
| `claude-stale-keys` | a generated key left in a base is discarded, not carried | [ADR 0001](../decisions/0001-render-never-reads-its-own-output.md) | `test_claude_discards_stale_generated_keys_from_a_base` |
| `claude-mcp-ascii` | `mcp-permissions.json` escapes non-ASCII; every other JSON output does not | — (test is the record) | `test_claude_mcp_serializes_with_ascii_escaping`, `test_every_other_json_renderer_keeps_unicode` |
| `claude-mcp-order` | three categories in fixed order | — (test is the record) | `test_claude_mcp_emits_three_categories_in_fixed_order` |
| `claude-project-order` | project variant emits allow, ask, deny — unlike the global one | — (test is the record) | `test_claude_project_emits_allow_ask_deny_unlike_the_global_renderer` |

## Pi

| id | behaviour | source | pinned by |
|---|---|---|---|
| `pi-schema` | `$schema` is pinned to the extension version | [pi](pi.md#config-file) | `test_pi_document_shape` |
| `pi-catch-alls` | `bash` and `mcp` are seeded `{"*": "ask"}` first | [pi](pi.md#document-shape) | `test_pi_seeds_catch_alls_first` |
| `pi-mcp-derived` | MCP targets are `<server>_<tool>` and `<server>:<tool>`, not `server/tool` | [pi](pi.md#mcp-targets-are-derived-not-servertool) | `test_pi_mcp_patterns_for_a_single_tool` |
| `pi-mcp-serverwide` | `mcp_server_<s>` / `mcp_connect_<s>` only for a `server/*` entry | [pi](pi.md#mcp-targets-are-derived-not-servertool) | `test_pi_mcp_patterns_add_server_wide_targets_for_a_wildcard` |
| `pi-project-shape` | project variant omits `$schema` and the catch-alls | — (test is the record) | `test_pi_project_omits_schema_and_catch_alls` |

## Codex

| id | behaviour | source | pinned by |
|---|---|---|---|
| `codex-tokens` | the entry is split on whitespace and each token JSON-quoted | [codex](codex.md#pattern-shape) | `test_codex_rule_quotes_each_token_positionally` |
| `codex-decisions` | `allow→allow`, `deny→forbidden`, `ask→prompt` | [codex](codex.md#pattern-shape) | `test_codex_maps_categories_to_its_own_decision_names` |
| `codex-newline` | output ends with exactly one newline | — (test is the record) | `test_codex_output_ends_with_a_single_newline` |
| `codex-mcp-deny-all` | `server/*` deny becomes `enabled = false` | [codex](codex.md#what-loadout-emits) | `test_codex_mcp_wildcard_deny_disables_the_server` |
| `codex-mcp-allow-all` | `server/*` allow becomes `default_tools_approval_mode` | [codex](codex.md#what-loadout-emits) | `test_codex_mcp_wildcard_allow_sets_default_mode` |
| `codex-mcp-per-tool` | per-tool tables plus a `disabled_tools` array | [codex](codex.md#what-loadout-emits) | `test_codex_mcp_per_tool_sections_and_disabled_list` |
| `codex-project-header` | project header is one line with no blank after | — (test is the record) | `test_codex_project_header_is_a_single_line_with_no_blank_after` |
| `codex-project-globs` | project variant does **not** skip globs — reproduces a live defect | [ADR 0003](../decisions/0003-port-byte-identical-before-changing-behaviour.md) | `test_codex_project_does_not_skip_globs_unlike_the_global_renderer` |

## Antigravity

| id | behaviour | source | pinned by |
|---|---|---|---|
| `agy-command-form` | entries wrap as `command(<entry>)` | [antigravity](antigravity.md#pattern-shape) | `test_antigravity_wraps_entries_in_command_form` |
| `agy-mcp-after-shell` | MCP entries append after shell entries within a category | — (test is the record) | `test_antigravity_appends_mcp_entries_after_shell_entries` |
| `agy-category-order` | categories emitted in order on an empty base | — (test is the record) | `test_antigravity_emits_categories_in_order_on_an_empty_base` |

## OpenCode

| id | behaviour | source | pinned by |
|---|---|---|---|
| `oc-catch-all-first` | `bash` seeded `{"*": "ask"}` first | [opencode](opencode.md#pattern-shape) | `test_opencode_seeds_the_bash_catch_all_first` |
| `oc-mcp-top-level` | MCP entries become top-level `permission` keys, `/` → `_` | [opencode](opencode.md#pattern-shape) | `test_opencode_mcp_entries_become_top_level_permission_keys` |
| `oc-extras` | `[opencode.extra]` toggles emitted verbatim | [opencode](opencode.md#what-loadout-emits) | `test_opencode_extra_toggles_are_emitted_verbatim` |
| `oc-permission-last` | `permission` is appended after the base's keys | — (test is the record) | `test_opencode_appends_permission_after_the_base_keys` |

## Covered only by whole-document comparison

These have no unit test, because they are not renderer behaviour — they are wiring. This is the
list the expected-output files exist for, and the reason the comparison stays.

| id | behaviour |
|---|---|
| `wiring-renderer` | each manifest target reaches the renderer its `render` key names |
| `wiring-base` | a declared `base` is loaded and its keys survive into the output |
| `wiring-preserve` | `preserve` carries a foreign key through, at the right position |
| `wiring-profile` | only profile-matching targets render |
| `wiring-serialization` | `indent=2`, per-renderer `ensure_ascii`, trailing newline, end to end |
| `wiring-header` | the generated-by header is present on the text outputs |
| `wiring-composition` | instruction fragments compose in manifest order with the right separators |
| `wiring-count` | every declared target produces an output |

## Recorded but not testable at render time

| id | behaviour | why |
|---|---|---|
| `wrapper-bypass` | an allowlisted command taking another command as an argument voids every deny | A property of the rule *set*, not of rendering. The intended fix is a build-time `neverallow` ceiling that refuses to emit. Not implemented. |
| `ask-shadows-allow` | an `ask`/`deny` entry that is a strict prefix of an `allow` makes the allow unreachable on all five harnesses | Same class — generalises [ADR 0005](../decisions/0005-a-deny-cannot-carry-exceptions.md). loadout does not detect it today; found by hand in `~/ac` on 2026-08-08 (`gh api --method` shadowing `gh api --method GET`). Candidate for a source validator. |
| `claude-afk-delta` | the two Claude bases differ only by `CLAUDE_AFK_TIMEOUT_MS` | A property of `~/ac`'s own base documents, not of loadout. Pinned by `test_base_drift_guard`, which is meaningless against a synthetic fixture. |
| `agy-headless` | `agy` soft-denies everything in headless mode regardless of the allowlist | Harness runtime behaviour; nothing is rendered differently. |
| `oc-compound` | OpenCode takes the least-permitted verdict across a `;`/`&&`/`\|` chain | Harness runtime behaviour. |
