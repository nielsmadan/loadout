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

## Composition and inheritance

The operator map is [composition.md](composition.md). Profile tests are in
`tests/test_profile_inheritance.py`; source selection tests are in
`tests/test_source_overrides.py`.

| behaviour | pinned by |
|---|---|
| legacy targets inherit individual fields and instruction substitution reaches output | `test_legacy_target_inherits_fields_and_applies_substitution`, `test_permission_target_inherits_renderer_and_replaces_lists`, `test_explicit_manifest_path_remains_the_start_of_inheritance` |
| removals precede child fields across a chain, including quoted paths and whole-target replacement | `test_removals_apply_before_child_fields_in_a_chain`, `test_removing_a_target_allows_wholesale_replacement` |
| removing agent fields exposes shared defaults; supplied maps replace completely | `test_agent_field_removal_exposes_all_and_maps_replace` |
| missing/overlapping removal paths, list traversal and parentless removal fail | `test_invalid_removals_are_refused`, `test_removal_without_parent_is_refused` |
| declared skill replacements select the whole tree while other lower-tier skills survive | `test_skill_override_replaces_the_entire_tree` |
| declared module replacements preserve the winner's bytes and mode while other lower-tier files survive | `test_module_override_keeps_the_winners_bytes_and_mode` |
| overrides require both contenders, safe exact names and a participating category | `test_override_must_name_both_contenders`, `test_invalid_override_declarations_are_refused`, `test_override_requires_the_category_in_use` |
| parsed skill/module overrides reach deployed bytes, modes and clean checks | `test_declared_overrides_reach_sync_and_check` |


| behaviour | pinned by |
|---|---|

## Migration planning


| behaviour | pinned by |
|---|---|
| metadata-only unresolved preview, explicit scope and approval | `test_unresolved_json_preview_does_not_mutate`, `test_noninteractive_apply_requires_explicit_approval`, `test_project_init_does_not_adopt_global_live_state` |
| global cwd default, source and harness-default registration, matching-config preservation and explicit replacement | `test_global_defaults_to_cwd_and_registers_actual_manifest`, `test_repeat_global_init_saves_defaults_and_preserves_machine_config`, `test_matching_registration_keep_preserves_harness_defaults`, `test_existing_source_registration_is_an_explicit_transaction` |
| project harness precedence: explicit selection, machine default, discovery, initialized project and stale global source | `test_explicit_project_harnesses_override_machine_defaults`, `test_project_init_uses_machine_harness_defaults`, `test_machine_harness_defaults_take_precedence_over_project_discovery`, `test_initialized_project_ignores_machine_harness_defaults`, `test_stale_global_source_does_not_block_machine_harness_defaults` |
| duplicate source choices are independent of approval | `test_global_copy_conflict_requires_selection_even_with_yes` |
| init scope subcommands: bare usage, and flags rejected outside their scope | `test_bare_init_prints_usage_naming_the_two_scopes`, `test_init_project_rejects_global_only_flags`, `test_init_global_rejects_project_only_flags` |
| interactive choice/decline and cancellation before mutation | `test_interactive_agent_and_starter_choices_then_decline`, `test_global_apply_cancellation_does_not_mutate` |

| behaviour | pinned by |
|---|---|
| package includes onboarding reference | `test_built_wheel_contains_the_complete_skill_tree` in `tests/test_bundled_skill.py` |

[migration.md](migration.md).

| behaviour | pinned by |
|---|---|
| explicit scope and shared membership ambiguity | `test_shared_instructions_do_not_invent_scope_or_harnesses` |
| unknown and unmaterialized native input blocks completion | `test_unknown_native_and_materialization_inputs_block_complete_migration`, `test_unmapped_external_hook_dependency_blocks_completion` |
| initialized source produces an idempotent no-migration result | `test_existing_source_is_not_reinterpreted` |
| ignored generated links preserve tracked source privacy | `test_ignored_symlink_output_keeps_tracked_canonical_source_public` |
| canonical runtime roles through symlinked harness roots, with unchanged crawl limits | `test_symlinked_harness_root_excludes_canonical_runtime_aliases`, `test_symlinked_harness_root_does_not_expand_inventory_boundary`, `test_symlink_cycle_in_harness_root_is_reported` |
| private canonical originals and future private tree entries stay excluded | `test_private_instruction_excludes_canonical_original_and_aliases`, `test_private_tree_namespace_ignores_future_entries` |
| dependency mappings agree in original and deployed coordinates | `test_mapped_relative_dependencies_preserve_source_and_deployed_layout`, `test_absolute_dependency_on_relocated_original_stays_blocked`, `test_relative_dependency_with_disagreeing_mapping_stays_blocked` |
| recognizable unsupported global harness trees block migration | `test_global_unsupported_harness_tree_is_unresolved` |
| instruction discovery preconditions every traversed directory | `test_instruction_search_records_each_traversed_directory`, `test_instruction_search_preconditions_canonical_root_listing` |

Additional migration review regressions are in `tests/test_init_review_regressions.py`:

| behaviour | pinned by |
|---|---|
| nested auth/runtime originals remain excluded from both Git phases | `test_nested_runtime_files_remain_original_and_outside_both_git_phases` |
| candidate-based corpus inventory detects a joint planner/renderer omission | `test_corpus_oracle_catches_joint_planner_renderer_omission` |

## Migration transactions

[migration.md](migration.md#applying-a-resolved-plan).

| behaviour | pinned by |
|---|---|
| existing-source no-op retains explicit registration | `test_existing_source_noop_can_write_explicit_registration` |
| unrelated partial staging and unstaged ignore hunks survive | `test_unrelated_partial_index_and_ignore_hunks_survive` |
| ambiguous staging and unmerged index refuse preflight | `test_partially_staged_adopted_files_require_resolution_even_when_private`, `test_unmerged_index_is_rejected_before_preimages` |
| input/index/HEAD and Git privacy changes invalidate preparation | `test_stale_preparation_is_rejected_before_journal`, `test_source_privacy_change_in_info_exclude_blocks_preparation` |
| unresolved plans cannot reach application | `test_unresolved_plan_cannot_be_prepared` |
| checkpoint survives refresh failure and recovery | `test_failure_after_checkpoint_commit_records_and_refreshes_success`, `test_recovery_after_final_staging_keeps_baseline_and_restores_original_entries` |
| resume revalidates inputs and refuses intervening new-repo commits | `test_resume_after_hook_failure_refuses_changed_checkpoint_content`, `test_new_repository_resume_refuses_an_intervening_user_commit` |
| preimages stay private through hooks; malformed state is refused | `test_untrusted_recovery_state_is_rejected_before_checkpoint`, `test_journal_is_ignored_during_the_real_checkpoint_hook`, `test_loaded_journal_rejects_invalid_shape_before_recovery` |

Retirement optimization is covered in `tests/test_init_review_regressions.py` and

| behaviour | pinned by |
|---|---|
| privacy checks refresh aliases, nested repositories, ignore files and config includes | `test_privacy_policy_refreshes_nested_repositories_and_logical_aliases`, `test_privacy_policy_refreshes_ignore_content_configuration_and_symlinks`, `test_parallel_privacy_keeps_git_include_path_expansion_and_fresh_values` |
| concurrent guards keep every Git observation for born/unborn/detached refs and wait for all query results | `test_parallel_git_guard_keeps_every_observation_and_ref_check`, `test_parallel_privacy_queries_all_finish_before_success_or_error` |
| final index bytes are checked after concurrent privacy reads finish | `test_parallel_git_guard_checks_index_after_all_privacy_reads` |

## Git integration

The contract is [git-integration.md](git-integration.md). These cases run in
`tests/test_git_integration.py`, with real Git events and isolated HOME/harness roots.

| behaviour | pinned by |
|---|---|
| migrating absolute/escaping old sources validates only index dependencies; HEAD and index outputs remain protected | `test_head_source_migration_keeps_head_and_index_output_ownership` |
| selected profile controls instruction/permission ownership before destination expansion | `test_staged_ownership_selects_profile_before_resolving_destinations` |
| active ownership records must be staged and match their producer, including initially empty records; valid updates and producer retirement remain allowed | `test_staged_ownership_record_matches_its_producer_and_allows_updates`, `test_staged_ownership_record_deletion_requires_producer_retirement`, `test_staged_initial_producer_requires_its_generated_record` |
| bare bundled and machine template names require staged vendoring | `test_bare_templates_never_fall_back_to_machine_or_bundled_source` |
| nested global source includes sibling dependencies and isolates global destinations | `test_nested_global_snapshot_contains_sibling_sources_and_isolates_destinations` |
| local custom paths, exact managed identity, other-source/occupied/shared preservation | `test_hook_installation_preserves_occupied_shared_and_other_source_hooks` |
| main checkout preserves default and absolute custom hooks shared with linked worktrees; exclusive paths still install | `test_main_checkout_preserves_hooks_shared_with_linked_worktrees` |
| sharing is checked again after standalone preview | `test_standalone_install_rechecks_hook_sharing_after_preview` |
| a hook loadout wrote is removed; one without the marker, or serving another source, is preserved | `test_uninstall_removes_loadout_hooks_and_leaves_a_foreign_one`, `test_uninstall_leaves_a_hook_belonging_to_another_loadout_source` |
| a hook stays removable after a worktree makes its directory shared; an external directory refuses | `test_uninstall_still_removes_a_hook_after_a_worktree_is_added`, `test_uninstall_refuses_a_hooks_directory_outside_the_repository` |
| status reports each event and who owns it | `test_status_reports_each_event_and_who_owns_it` |
| missing installed CLI explains that checkout already completed | `test_missing_loadout_on_path_explains_completed_checkout` |
| the generated hook runs the hidden event command, carrying source and profile | `test_a_generated_hook_runs_the_hidden_event_command` |

## Cross-cutting

| id | behaviour | source | pinned by |
|---|---|---|---|
| `last-match-opencode` | denies emitted after the allows they refine | [README](README.md#order-independent-vs-last-match) | `test_opencode_emits_deny_after_allow_for_last_match_wins` |
| `last-match-pi` | deny emitted last | [pi](pi.md#resolution) | `test_pi_emits_deny_last_so_it_wins_under_last_match` |
| `pi-moves-key` | Pi deletes and reinserts so an overwritten key moves to the end | [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md) | `test_pi_reorders_cross_key_entries_so_later_category_wins_position` — **unit test only; unreachable through the pipeline**, see below |
| `opencode-keeps-key` | OpenCode assigns in place, so the key stays put — deliberately unlike Pi | [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md) | `test_opencode_does_not_reorder_cross_key_entries` |
| `dedupe-order` | `dedupe()` is order-preserving and never `set()` | [README](README.md#order-independent-vs-last-match) | `test_dedupe_preserves_order` |
| `glob-literal` | a trailing-`*` entry is kept literal on Claude, OpenCode, Pi | [README](README.md#globs) | `test_claude_pattern_keeps_a_glob_literal`, `test_opencode_keeps_a_glob_literal`, `test_pi_keeps_a_glob_literal` |
| `glob-skipped` | Codex and Droid cannot express a portable glob; Codex and Droid's `allow`/`ask` skip it with a report, and Droid's `deny` is refused outright because its blocklist has no fallback | [README](README.md#globs), [droid](droid.md#how-an-entry-matches-and-what-that-costs-a-glob) | `test_codex_skips_globs_and_lists_them_at_the_end`, `test_droid_skips_globs_its_literal_matcher_cannot_express`, `test_droid_reports_each_permission_glob_it_drops_but_not_the_one_it_refuses`, `test_droid_refuses_a_deny_glob_rather_than_rendering_a_weaker_blocklist`, `test_droid_still_drops_allow_and_ask_globs_and_keeps_the_rest` |
| `glob-block-absent` | no trailing skipped-block when there are no globs | [codex](codex.md#pattern-shape) | `test_codex_omits_the_skipped_block_when_there_are_no_globs` |
| `claude-colon-star` | Claude needs the `:*` suffix, which matches bare and with-args | [claude](claude-code.md#pattern-shape) | `test_claude_pattern_appends_colon_star_to_a_prefix` |
| `both-forms` | OpenCode and Pi need both `<entry>` and `<entry> *` | [README](README.md#bare-vs-with-arguments) | `test_opencode_emits_both_bare_and_argument_forms`, `test_pi_emits_both_bare_and_argument_forms` |
| `purity` | renderers are pure and read no files | [ADR 0001](../decisions/0001-render-never-reads-its-own-output.md) | `test_renderers_are_pure`, `test_claude_never_reads_a_file` |
| `no-base-mutation` | a renderer never mutates the base it was handed | [ADR 0001](../decisions/0001-render-never-reads-its-own-output.md) | `test_claude_does_not_mutate_its_base`, `test_opencode_does_not_mutate_its_base` |
| `dest-env-set` | a set `${VAR}` relocates the destination, mid-path as well as at the start | [ADR 0011](../decisions/0011-a-destination-follows-a-relocated-harness.md) | `test_a_set_variable_relocates_the_destination`, `test_a_permission_destination_expands_the_same_way`, `test_a_variable_substitutes_mid_path` |
| `dest-env-fallback` | `${VAR:-path}` takes the fallback when unset, and when set-but-empty | [ADR 0011](../decisions/0011-a-destination-follows-a-relocated-harness.md) | `test_an_unset_variable_falls_back_to_the_default_path`, `test_an_empty_variable_counts_as_unset` |
| `dest-env-required` | neither `${VAR}` with no fallback nor `${VAR:-}` may resolve to nothing | [ADR 0011](../decisions/0011-a-destination-follows-a-relocated-harness.md) | `test_a_variable_with_no_fallback_is_an_error_when_unset`, `test_an_empty_fallback_is_an_error_rather_than_expanding_to_nothing` |
| `dest-env-grammar` | a brace form outside `${VAR}` / `${VAR:-x}` is an error, never literal path text | [ADR 0011](../decisions/0011-a-destination-follows-a-relocated-harness.md) | `test_a_reference_the_grammar_does_not_cover_is_an_error` |
| `dest-absolute` | a resolved destination must be absolute with no `..`, checked after `~` | [ADR 0011](../decisions/0011-a-destination-follows-a-relocated-harness.md) | `test_a_destination_that_resolves_relative_is_an_error`, `test_an_expansion_containing_dot_dot_is_rejected`, `test_destination_with_dotdot_is_an_error` |
| `dest-render-time` | resolution is per render, so an unselected profile's variable need not be set | [ADR 0011](../decisions/0011-a-destination-follows-a-relocated-harness.md) | `test_an_unselected_profiles_variable_does_not_have_to_be_set`, `test_the_target_holds_the_template_and_resolution_happens_per_render` |
| `dest-collision-resolved` | collisions are detected on the resolved path, not the template | [README](../../README.md#loadouttoml) | `test_two_templates_that_expand_to_one_path_collide` |
| `dest-env-literal` | only `${...}` substitutes; a bare `$` is left alone | [README](../../README.md#loadouttoml) | `test_a_destination_with_no_reference_is_left_alone` |

| `module-config-shapes` | both Pi shapes land at their authored relative paths | [module-config](module-config.md#why-the-path-is-authored-never-derived) | `test_both_shapes_land_at_their_authored_relative_paths` |
| `module-config-verbatim` | bytes are copied, not reserialised, and the exec bit survives | [module-config](module-config.md#what-loadout-does) | `test_the_bytes_are_copied_rather_than_reserialised`, `test_an_executable_file_keeps_its_mode` |
| `module-config-automatic` | the directory is the declaration; `module-config = false` opts out | [module-config](module-config.md#what-loadout-does) | `test_the_directory_is_the_declaration`, `test_module_config_false_switches_it_off` |
| `module-config-collision` | undeclared source collisions and collisions with rendered destinations are refused | [module-config](module-config.md#what-loadout-does) | `test_a_path_offered_by_two_sources_is_refused`, `test_a_path_colliding_with_a_rendered_destination_is_refused` |
| `module-config-harnesses` | three harnesses carry three kinds of file, each reading only its own subtree | [module-config](module-config.md#other-harnesses) | `test_the_slice_is_not_pi_specific` |
| `module-config-opencode-plugin` | a vendored OpenCode plugin lands in `plugins/` beside the generated `loadout-hooks.js` | [module-config](module-config.md#other-harnesses) | `test_an_opencode_plugin_lands_beside_the_generated_hooks_plugin` |

| `json-surgery-identity` | applying loadout's keys to an already-applied file is a no-op, so `check` reports no drift | [servers](servers.md#claudes-global-entry-writes-claudejson) | `test_applying_what_is_already_there_is_identity` |
| `json-surgery-in-place` | an owned key is replaced where it sits, never moved to the end | [servers](servers.md#claudes-global-entry-writes-claudejson) | `test_an_owned_key_is_replaced_where_it_sits` |
| `json-surgery-delete` | an owned key the document drops is removed — add, update and delete from one mechanism | [servers](servers.md#claudes-global-entry-writes-claudejson) | `test_an_owned_key_the_document_drops_is_removed` |
| `json-surgery-foreign` | keys loadout does not own survive untouched | [servers](servers.md#claudes-global-entry-writes-claudejson) | `test_keys_loadout_does_not_own_survive_untouched` |
| `nothing-staged` | no global slice renders in-repo for an external merge step | [ADR 0004](../decisions/0004-loadout-is-render-only.md) | `test_no_global_slice_is_staged` |

| `written-third-variant` | an output rendered from an uncommitted source state that has since moved on is accepted | [ADR 0019](../decisions/0019-sync-records-what-it-wrote.md) | `test_editing_a_source_twice_before_committing_does_not_block`, `test_reverting_a_synced_source_edit_no_longer_blocks`, `test_two_sources_feeding_one_destination_do_not_block` |
| `written-copied` | a copied supporting file uses the same baseline, so editing one no longer blocks forever | [ADR 0019](../decisions/0019-sync-records-what-it-wrote.md) | `test_editing_a_copied_supporting_file_twice_does_not_block` |
| `written-degrades` | a missing, corrupt or future-version record reproduces the two-variant guard exactly | [ADR 0019](../decisions/0019-sync-records-what-it-wrote.md) | `test_without_a_record_the_revert_blocks_exactly_as_before`, `test_without_a_record_the_copied_edit_blocks_as_before`, `test_a_corrupt_record_reads_as_empty_rather_than_raising`, `test_a_future_version_reads_as_empty` |
| `written-still-blocks` | a real hand edit matches neither render nor record, and still blocks | [ADR 0019](../decisions/0019-sync-records-what-it-wrote.md) | `test_hand_edited_output_blocks_sync`, `test_a_hand_edited_copy_still_blocks` |
| `written-when` | `--force` records; `check` does not | [ADR 0019](../decisions/0019-sync-records-what-it-wrote.md) | `test_force_records_what_it_wrote`, `test_check_records_nothing` |

| `defaults-remove` | `$remove` owns a key in order to delete it, and strips a multi-line value's body with it | [README](../../README.md#loadouttoml) | `test_a_removed_key_is_owned_but_rendered_nowhere`, `test_a_removed_key_is_stripped_from_the_destination` |
| `defaults-remove-conflict` | a key both valued and removed is an error, not a silent preference | [README](../../README.md#loadouttoml) | `test_a_key_cannot_be_both_valued_and_removed`, `test_remove_must_be_a_list_of_names` |
| `toml-multiline` | a multi-line owned value is replaced or removed without orphaning its body | [ADR 0017](../decisions/0017-ownership-may-be-declared-instead-of-derived.md) | `test_a_multiline_destination_value_is_replaced_not_corrupted`, `test_a_multiline_owned_key_is_removed_when_no_value_is_rendered` |

## Claude

| id | behaviour | source | pinned by |
|---|---|---|---|
| `claude-owned-first` | owned keys precede hand-maintained ones inside `permissions` | [claude](claude-code.md#what-loadout-emits) | `test_claude_orders_owned_keys_before_hand_maintained_ones` |
| `claude-key-position` | `permissions` keeps its position in the base document | [claude](claude-code.md#what-loadout-emits) | `test_claude_keeps_the_permissions_key_at_its_position_in_the_base` |
| `claude-concat-order` | shell, then MCP, then extras | [claude](claude-code.md#what-loadout-emits) | `test_claude_concatenates_shell_then_mcp_then_extras` |
| `claude-ask-no-extras` | `ask` has no extras channel | — (test is the record) | `test_claude_ask_has_no_extras_channel` |
| `claude-empty-rules` | `rules = []` empties all three lists — the autonomous profile | [claude](claude-code.md#what-loadout-emits) | `test_claude_with_empty_rules_empties_all_three_lists` |
| `claude-stale-keys` | a generated key left in a base is discarded, not carried | [ADR 0001](../decisions/0001-render-never-reads-its-own-output.md) | `test_claude_discards_stale_generated_keys_from_a_base` |
| `claude-mcp-ascii` | `mcp-permissions.json` escapes non-ASCII; every other JSON output does not | — (test is the record) | `test_claude_mcp_serializes_with_ascii_escaping`, `test_every_other_json_renderer_keeps_unicode` |
| `claude-mcp-order` | three categories in fixed order | — (test is the record) | `test_claude_mcp_emits_three_categories_in_fixed_order` |
| `claude-project-order` | project variant emits allow, ask, deny — unlike the global one | — (test is the record) | `test_claude_project_emits_allow_ask_deny_unlike_the_global_renderer` |

## Pi

| id | behaviour | source | pinned by |
|---|---|---|---|
| `pi-schema` | `$schema` is pinned to the extension version | [pi](pi.md#config-file) | `test_pi_document_shape` |
| `pi-catch-alls` | `bash` and `mcp` are both seeded with a `*` catch-all first | [pi](pi.md#document-shape) | `test_pi_seeds_catch_alls_first` |
| `pi-default` | `bash`'s catch-all takes `[shell] default`, first; `mcp`'s stays `ask` | [pi](pi.md#document-shape) | `test_pi_seeds_bash_with_the_stated_default_and_leaves_mcp_alone` |
| `pi-mcp-derived` | MCP targets are `<server>_<tool>` and `<server>:<tool>`, not `server/tool` | [pi](pi.md#mcp-targets-are-derived-not-servertool) | `test_pi_mcp_patterns_for_a_single_tool` |
| `pi-mcp-serverwide` | `mcp_server_<s>` / `mcp_connect_<s>` only for a `server/*` entry | [pi](pi.md#mcp-targets-are-derived-not-servertool) | `test_pi_mcp_patterns_add_server_wide_targets_for_a_wildcard` |
| `pi-project-shape` | project variant omits `$schema` and the catch-alls | — (test is the record) | `test_pi_project_omits_schema_and_catch_alls` |
| `pi-project-no-default` | with no catch-all to seed, a stated default is inert at project scope | [pi](pi.md#document-shape) | `test_pi_project_has_no_catch_all_for_a_default_to_seed` |

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

## OpenCode

| id | behaviour | source | pinned by |
|---|---|---|---|
| `oc-catch-all-first` | `bash` seeded with a `*` catch-all first | [opencode](opencode.md#pattern-shape) | `test_opencode_seeds_the_bash_catch_all_first` |
| `oc-default` | the seed takes `[shell] default`, and rules after it still refine it | [reference](README.md#the-catch-all-default) | `test_opencode_seeds_the_bash_catch_all_with_the_stated_default`, `test_a_stated_default_does_not_displace_a_rule_that_refines_it` |
| `oc-project-default` | OpenCode has no project variant, so a project-stated default reaches it | [pi](pi.md#document-shape) | whole-document comparison (`expected/project/opencode.json`) |
| `oc-mcp-top-level` | MCP entries become top-level `permission` keys, `/` → `_` | [opencode](opencode.md#pattern-shape) | `test_opencode_mcp_entries_become_top_level_permission_keys` |
| `oc-extras` | `[opencode.extra]` toggles emitted verbatim | [opencode](opencode.md#what-loadout-emits) | `test_opencode_extra_toggles_are_emitted_verbatim` |
| `oc-permission-last` | `permission` is appended after the base's keys | — (test is the record) | `test_opencode_appends_permission_after_the_base_keys` |
| `oc-instructions-document` | global instructions are a document at `~/.config/opencode/AGENTS.md`, not the `instructions` key | [config](config.md#instructions) | `test_opencode_gets_its_own_agents_md`, `test_instructions_stay_out_of_opencode_json` |
| `oc-instructions-own` | the document is OpenCode's own; before the slice existed OpenCode fell back to Claude's `CLAUDE.md`, which loadout also writes | [config](config.md#instructions) | `test_the_document_is_opencodes_own_not_claudes` |

## Droid

| id | behaviour | source | pinned by |
|---|---|---|---|
| `droid-command-lists` | allow, ask, and deny map to allowlist, denylist, and hard blocklist respectively | [droid](droid.md#shell-permissions) | `test_droid_maps_ask_to_denylist_and_deny_to_blocklist` |
| `droid-settings-residual` | unrelated settings survive and stale owned lists do not | [droid](droid.md#shell-permissions) | `test_droid_preserves_settings_and_does_not_mutate_them` |
| `droid-runtime-trust` | global sync preserves Droid's live `trustedFolders` timestamps and refuses to take ownership through a settings fragment | [droid](droid.md#shell-permissions) | `test_droid_preserves_its_runtime_trusted_folders`, `test_droid_runtime_trusted_folders_cannot_be_a_settings_source` |
| `droid-hooks` | the native hook ABI passes through unchanged, accepts both native variable namespaces, and rejects another harness's namespace | [droid](droid.md#hooks) | `test_droid_render_is_the_abi_document_itself`, `test_droid_accepts_both_native_variable_namespaces`, `test_droid_rejects_a_foreign_harness_variable`, `test_droids_namespaces_are_foreign_to_the_other_harnesses` |
| `droid-hook-events` | the documented event set has nine entries, and an event outside it is reported | [droid](droid.md#hooks) | `test_droid_documented_event_set_has_nine_events`, `test_an_event_outside_droids_set_is_reported_and_one_inside_it_is_not` |
| `droid-mcp` | MCP definitions use the `mcpServers` wrapper and `${ENV}` bearer interpolation | [droid](droid.md#mcp-servers) | `test_droid_uses_the_mcpservers_wrapper_and_env_bearer_header` |
| `droid-plugins` | enabled plugins and only their declared marketplaces compose into two settings keys | [droid](droid.md#plugins) | `test_droid_renders_enablement_and_declared_marketplaces`, `test_droid_omits_an_unused_marketplace` |
| `droid-extraction` | permissions, hooks, plugins, and MCP definitions re-render what they read | [droid](droid.md#extraction-and-migration) | `test_droid_round_trips_the_fragment_unchanged`, `test_droid_re_renders_the_values_it_read`, `test_droid_server_round_trips` |

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
| `wiring-destination-env` | a `${VAR:-fallback}` destination reaches the renderer like a literal one | 
| `wiring-default` | `[shell] default` reaches the two renderers that seed one, and no others |
| `wiring-default-tiers` | strictest-stated-wins resolves across three real project tiers, and a silent tier does not vote |

## The catch-all default

| id | behaviour | source | pinned by |
|---|---|---|---|
| `default-strictest` | the strictest *stated* default wins across tiers, in either order | [reference](README.md#the-catch-all-default) | `test_the_strictest_stated_default_wins_regardless_of_tier_order` |
| `default-silence` | a tier that states none casts no vote and cannot tighten one that did | [reference](README.md#the-catch-all-default) | `test_a_tier_that_states_no_default_does_not_tighten_one_that_does` |
| `default-invalid` | a value that is not a decision, or not a string, is refused at parse time | [reference](README.md#the-catch-all-default) | `test_parse_rules_rejects_a_default_that_is_not_a_decision`, `test_parse_rules_rejects_a_default_that_is_not_a_string` |
| `default-no-star-entry` | a bare `*` shell entry is refused; the key is the only spelling | [reference](README.md#the-catch-all-default) | `test_parse_rules_refuses_a_bare_catch_all_entry`, `test_a_glob_entry_is_still_allowed_beside_the_refused_bare_star` |
| `default-resolved` | `Rules.catch_all` never hands a renderer `None` | — (test is the record) | `test_catch_all_resolves_unstated_to_the_seeded_verdict` |
| `default-carriers` | the declared carrier set matches what the renderers actually seed | — (test is the record) | `test_only_the_declared_renderers_seed_a_catch_all` |
| `default-unreached` | a stated default names the targets it does not reach, at sync time, at both scopes and for every selected harness rather than a named one | [ADR 0015](../decisions/0015-enablement-is-rendered-installation-is-reported.md) | `test_a_stated_catch_all_names_the_harnesses_it_misses`, `test_a_project_only_repo_reports_nothing_rather_than_failing` |
| `project-key-retirement` | a project slice that contributes nothing retires the keys it owns from a co-owned document instead of leaving them for `preserve_foreign` to read back | [ADR 0001](../decisions/0001-render-never-reads-its-own-output.md) | `test_removing_the_plugins_fragment_retires_the_keys_it_wrote` |
| `settings-routing` | a `settings` fragment reaches only the slices whose preset declares `takes_settings` | [README](../../README.md) | `test_a_settings_fragment_reaches_only_the_slices_whose_renderer_reads_one` |

## Extraction

Every row is pinned by `tests/test_extract_roundtrip.py`, `tests/test_extract_merge.py` or
`tests/test_extract_fixtures.py`. Source for all of them is [extraction](extraction.md).

| id | behaviour | pinned by |
|---|---|---|
| `x-rules-roundtrip` | `extract(render(rules))` recovers every rule the harness can carry | `test_extraction_recovers_every_rule_the_harness_can_carry` |
| `x-doc-roundtrip` | `render(extract(document))` is byte-identical wherever no loss is reported | `test_reextraction_reproduces_the_document_byte_for_byte` |
| `x-idempotent` | a second extraction of the re-rendered document finds the same rules | `test_extraction_is_idempotent` |
| `x-shipped-bytes` | every artifact in `tests/fixtures/expected/` round-trips, or is on a named list | `test_a_shipped_artifact_extracts_and_renders_back_to_itself`, `test_only_the_listed_artifacts_report_a_loss` |
| `x-every-renderer` | every inverted renderer has a capability and a projection, and round-trips an empty source | `test_every_inverted_renderer_round_trips_an_empty_source`, `test_every_inverted_renderer_declares_a_capability`, `test_every_inverted_renderer_has_a_declared_projection` |
| `x-named-gap` | a renderer inverted by neither `EXTRACTORS` nor `VALUE_EXTRACTORS` must be named in `NOT_INVERTED`, which holds the two adapters and nothing else — a third would fail | `test_no_renderer_lacks_an_inverse_without_being_named` |
| `x-base-residual` | `base` keeps keys other slices own, because it is the render-time residual and not a settings fragment | `test_the_base_keeps_keys_other_slices_own` |
| `x-codex-quoting` | a `shlex.split` token holding whitespace is re-quoted, so `echo "a b"` round-trips instead of splitting into three tokens | `test_a_token_holding_a_space_survives_the_codex_project_round_trip` |
| `x-pair-collapse` | `foo` and `foo *` collapse back to one entry — **only the rules property catches a miss**, since not collapsing renders identical bytes | `test_extraction_recovers_every_rule_the_harness_can_carry` |
| `x-seed-dropped` | a leading `*` in a map a renderer seeded is structure, not a source rule | `test_extraction_recovers_every_rule_the_harness_can_carry` |
| `x-default-carried` | a bash catch-all other than `ask` reads back as `[shell] default` | `test_extraction_recovers_every_rule_the_harness_can_carry` |
| `x-default-unseeded` | `pi-project` seeds nothing, so a leading `*` there stays a rule | `test_pi_project_keeps_a_leading_star_because_nothing_seeded_it` |
| `x-default-absent` | a carrier with no catch-all is reported, not assumed to have said `ask` | `test_a_carrier_without_a_catch_all_reports_rather_than_assuming_one` |
| `x-default-unrecognised` | an unrecognised catch-all verdict is reported, not swallowed | `test_an_unrecognised_catch_all_verdict_is_reported_not_swallowed` |
| `x-default-ask` | a stated `ask` renders what an unstated key renders, so it reads back unstated | `test_a_default_of_ask_is_the_same_document_as_no_default` |
| `x-codex-globs` | Codex's skipped-glob comment block carries no decision, so it is reported | `test_the_shipped_codex_rules_reports_the_globs_it_cannot_categorise` |
| `x-order-loss` | in-place assignment in `render_opencode` / `render_pi_project` loses which category a twice-listed rule came from; `render_pi`'s key-move does not | `test_reextraction_reproduces_the_document_byte_for_byte` |
| `x-no-union` | a command one harness denies is never emitted as allowed | `test_a_command_one_harness_denies_is_never_emitted_as_allowed`, `test_mcp_disagreement_is_reported_and_withheld` |
| `x-absence-is-drift` | a harness that could have stated a rule and did not is a divergence | `test_a_command_missing_from_one_harness_is_reported_as_drift`, `test_codex_silence_on_a_plain_command_is_divergence` |
| `x-capability-vote` | a harness that *cannot* express a rule does not vote on it | `test_codex_silence_on_a_glob_is_not_divergence`, `test_shell_only_harnesses_do_not_vote_on_mcp_targets`, `test_claude_and_codex_do_not_vote_on_the_catch_all_default` |
| `x-default-diverges` | carriers disagreeing on the catch-all settle on the strictest, and it is reported | `test_a_catch_all_the_two_carriers_disagree_on_settles_on_the_strictest`, `test_a_deny_catch_all_is_never_loosened_by_a_disagreement` |
| `x-default-withheld` | while a shell entry is withheld the catch-all is not stated looser than `ask` | `test_a_withheld_rule_stops_the_catch_all_being_stated_permissively`, `test_an_unwithheld_merge_still_states_a_permissive_catch_all` |
| `x-unknown-harness` | an undeclared harness is refused, never defaulted to a non-voter | `test_an_undeclared_harness_is_refused_rather_than_ignored` |
| `x-twice-listed` | a verdict is every category a harness listed the entry in, not the last one | `test_harnesses_agreeing_a_rule_appears_twice_keep_both_entries`, `test_a_rule_one_harness_lists_twice_and_another_once_is_reported` — **not reachable through the pipeline**, see below |
| `x-machine-merges-back` | a source rendered to all nine documents merges back to itself with no divergence | `test_a_machine_rendered_from_one_source_merges_back_to_it` |

## Skills

A skill is a tree, so the slice pins two things nothing else does: that the shared case survives
composition untouched, and that a file's *mode* survives a copy.

| id | behaviour | pinned by |
|---|---|---|
| `s-verbatim` | an unmarked `SKILL.md` renders byte-identical to its source but for the banner — the property 49 of 50 skills rely on | `test_an_unmarked_skill_is_reproduced_exactly_but_for_the_banner` |
| `s-banner-below` | the banner sits below the closing `---`; above it the frontmatter goes unparsed and the banner becomes the description | `test_the_banner_sits_below_the_frontmatter` |
| `s-marked-sections` | `::: <harness>` content reaches only the named harnesses | `test_marked_sections_go_only_to_the_named_harness` |
| `s-fence-is-content` | a `:::` inside a fenced code block is content, so a skill can document the syntax | `test_a_marker_inside_a_code_fence_is_content` |
| `s-marker-errors` | an unknown harness, an unclosed section and a stray close each fail loudly | `test_an_unknown_harness_in_a_marker_is_refused`, `test_an_unclosed_marker_is_refused`, `test_a_stray_close_marker_is_refused` |
| `s-frontmatter-override` | a harness block replaces shared values and every block is stripped, including for a harness with none | `test_frontmatter_overrides_replace_the_shared_value`, `test_every_harness_block_is_stripped_even_for_an_unnamed_harness` |
| `s-frontmatter-untouched` | frontmatter with no harness block passes through unchanged | `test_frontmatter_without_a_harness_block_is_untouched` |
| `s-artifacts-excluded` | `__pycache__` and friends are not skill content, so stale bytecode is not copied to five harnesses | `test_build_artifacts_are_not_skill_content` |
| `s-exec-bit` | a copied file keeps its mode — three `scripts/` files are executable and a mode does not survive a `str` | `test_copy_preserves_the_exec_bit` |
| `s-copy-bytes` | a copied file reproduces bytes exactly, including non-text | `test_copy_reproduces_bytes_exactly` |
| `s-copy-symlink` | a copy writes *through* a destination symlink rather than replacing it | `test_copy_writes_through_a_symlink` |
| `s-mode-drift` | matching bytes with a differing mode is drift — the case a text comparison cannot see | `test_drift_when_only_the_mode_differs` |
| `s-bundle-wheel` | the wheel carries the canonical loadout skill and its reference | `test_built_wheel_contains_the_complete_skill_tree` |
| `s-bundle-resource` | source checkouts and built wheels use the same package resource tree | `test_package_resource_is_used`, `test_source_checkout_uses_the_package_resource_tree` |
| `s-install-source` | install vendors into the active profile's sole skills-capable global source, or requires `--source` rather than choosing among several | `test_one_skills_source_is_selected_by_default`, `test_multiple_skills_sources_require_an_explicit_source`, `test_profile_selects_its_own_source`, `test_unknown_profile_is_refused_before_selecting_a_source`, `test_skill_install_requires_source_when_the_manifest_has_several` |
| `s-install-override` | status/update/uninstall use the declared skill winner; uninstall removes its inherited or local declaration and restores the earlier skill while preserving other entries, comments and mode | `test_skill_commands_manage_the_active_override`, `test_explicit_source_cannot_manage_a_shadowed_skill` in `test_skill_overrides.py` |
| `s-override-protection` | overrides retain ownership guards; a failed manifest write restores source, config and deployed output | `test_skill_override_preserves_source_ownership_guards`, `test_override_uninstall_rolls_back_a_failed_manifest_write` in `test_skill_overrides.py` |
| `s-install-owned` | an ownership hash distinguishes installed, updateable, modified and conflicting source copies; refresh never overwrites user edits | `test_an_existing_unowned_skill_is_a_conflict`, `test_install_vendors_the_bundle_as_a_normal_source_skill`, `test_install_refreshes_an_unmodified_older_bundle`, `test_install_preserves_a_modified_installed_copy`, `test_skill_install_conflict_is_reported_without_overwriting_or_syncing` |
| `s-install-rendered` | the marker stays source-only and ordinary sync deploys the skill to exactly the configured agents; no configured skill targets means install changes nothing | `test_normal_sync_deploys_the_installed_source_to_configured_agents`, `test_skill_install_vendors_into_the_global_source_and_syncs`, `test_skill_status_reports_the_source_copy_and_configured_agents`, `test_skill_install_with_no_configured_agents_does_not_choose_or_change_a_source` |
| `s-install-confirm` | mutating commands show the source destination, require confirmation or `--yes`, and a decline changes nothing | `test_skill_install_asks_before_writing_the_named_source`, `test_skill_install_requires_yes_without_interactive_input` |
| `s-uninstall-owned` | uninstall removes the owned source and unchanged rendered files, preserves unrelated files beside them, and treats an externally modified output as a whole-operation no-op | `test_uninstall_removes_owned_source_and_generated_outputs`, `test_uninstall_refuses_a_modified_generated_output`, `test_uninstall_preserves_unrelated_files_in_the_generated_skill_directory`, `test_skill_uninstall_removes_source_and_synced_outputs` |
| `s-config-contract` | one portable skill body resolves its reference, pins every scope mapping, and names only mappings backed by current renderers | `test_every_relative_skill_link_resolves`, `test_every_harness_receives_the_same_skill_body`, `test_capability_matrix_pins_every_scope_mapping`, `test_settings_capability_names_only_agents_whose_render_preserves_settings`, `test_project_skill_capability_names_only_agents_with_a_destination` |

## Templates

A template is a *source* referenced by name, so the slice pins two things nothing else does:
that a name resolves the same way a fragment name does one level up, and that a vendored copy is
source rather than generated output. See [templates](templates.md) and
[ADR 0014](../decisions/0014-a-vendored-template-is-source-not-output.md).

| id | behaviour | pinned by |
|---|---|---|
| `t-vendored-first` | a vendored copy stops resolution before the machine config is read — what lets a clone build without the template repo | `test_a_vendored_copy_resolves_with_no_machine_config_at_all`, `test_a_vendored_copy_wins_and_stops_resolution` |
| `t-add-modes` | `add --tracked` declares and resolves from a source; `add --vendored` copies the tree and records the hash; `add` on a declared template changes its mode in both directions | `test_add_tracked_declares_without_copying_anything`, `test_add_vendored_copies_the_tree_and_records_the_hash`, `test_add_changes_a_tracked_template_to_vendored`, `test_add_changes_a_vendored_template_to_tracked` |
| `t-add-interactive` | bare `template add` enumerates every resolvable template and prompts for a name and mode; without a TTY it requires both; `vendor` is not a command | `test_bare_add_lists_available_templates_and_takes_a_tracked_choice`, `test_bare_add_can_take_a_vendored_choice`, `test_bare_add_with_nothing_available_refuses`, `test_bare_add_refuses_without_a_tty`, `test_named_add_without_mode_refuses_without_a_tty`, `test_vendor_is_not_a_command` |
| `t-declared-source` | a declared name resolves through the machine config's global manifest, honouring each source's `use` | `test_a_declared_template_resolves_from_the_global_source`, `test_a_source_offering_no_templates_is_left_out_of_the_search` |
| `t-ambiguous` | two sources offering one name is an error, never a silent preference; `source/name` disambiguates | `test_two_sources_offering_one_template_name_is_refused`, `test_a_qualified_name_disambiguates_a_collision` |
| `t-search-reported` | an unresolvable name names every place searched, vendored path included | `test_an_unresolvable_template_names_every_place_searched` |
| `t-no-escape` | a directory slice needs the escape guard `is_file()` gave a document slice for free | `test_a_template_name_may_not_escape_its_source` |
| `t-lowest-tier` | a template merges beneath both project tiers, so a project deny beats a template allow | `test_a_project_deny_beats_a_template_allow` |
| `t-order` | declared order survives into emission order, which decides the winner on OpenCode and Pi | `test_templates_merge_in_declared_order`, `test_a_template_rule_is_emitted_before_the_projects_own` |
| `t-one-slice` | a template offering no permissions contributes no tier rather than failing — `railway`'s shape | `test_a_template_carrying_no_permissions_is_not_an_error` |
| `t-hash-relocatable` | the hash covers relative paths only, so vendoring does not change it | `test_the_same_content_hashes_the_same_from_a_different_directory` |
| `t-hash-boundary` | path, byte length and exec bit are all hashed, so a rename, a re-split or a chmod is a change | `test_moving_content_between_files_changes_the_hash`, `test_a_file_boundary_cannot_be_forged_by_rearranging_bytes`, `test_the_executable_bit_is_part_of_the_hash` |
| `t-hash-artifacts` | build output is excluded, or a fresh checkout would never compare equal | `test_build_artifacts_are_excluded_from_the_hash` |
| `t-no-path-in-config` | `[template.<name>]` accepts only `vendored`, so a local path cannot be committed | `test_a_template_block_may_not_carry_a_path` |
| `t-orphan-provenance` | provenance for an undeclared template is refused rather than left as dead state | `test_provenance_for_an_undeclared_template_is_refused` |
| `t-sync-refuses` | a modified copy is refused, the diff is shown, and nothing is written | `test_sync_refuses_a_modified_copy_and_changes_nothing`, `test_sync_shows_the_diff_it_refused_to_apply` |
| `t-sync-replaces` | sync carries an added file and drops a removed one, then re-records the hash | `test_sync_carries_a_file_the_upstream_added`, `test_sync_drops_a_file_the_upstream_removed`, `test_sync_rerecords_the_hash_so_the_copy_stays_clean` |
| `t-check-notes` | divergence is reported, never failed, and never swallows real drift (ADR 0014) | `test_check_notes_a_modified_vendored_template_without_failing`, `test_check_still_fails_on_real_drift_alongside_a_diverged_template` |
| `t-no-provenance` | a vendored copy with no recorded hash is refused by `sync` rather than overwritten — the gate requires proof the copy is *unmodified*, not proof that it is | `test_sync_refuses_a_copy_with_no_recorded_provenance_and_changes_nothing` |
| `t-provenance-self-heal` | matching the source is proof enough, so a clean copy re-records its hash instead of being refused — the only way back out of the state | `test_sync_still_self_heals_a_clean_copy_with_no_recorded_provenance` |
| `t-provenance-reported` | `check` reports the missing hash; divergence alone cannot, since it is the absence of the base divergence is measured against | `test_check_reports_a_vendored_copy_with_no_recorded_provenance` |
| `t-fixture-reach` | the project fixture carries a vendored template whose rules no other tier has | `test_the_project_fixture_carries_a_vendored_template`, `test_every_template_rule_is_absent_from_the_other_project_tiers` |
| `t-instructions-tier` | a template's `instructions.md` arrives as one unnamed block above the project's own fragments, without being named in the order | `test_a_template_contributes_instructions_without_being_named` |
| `t-fixture-instructions` | the fixture template contributes instructions, or the tier ordering between template and project is untested | `test_the_project_fixture_template_contributes_instructions` |
| `t-bundled-offline` | both packaged starters include useful instructions and empty categories and render from an actual wheel with networking disabled | `test_bundled_templates_resolve_offline_without_machine_config`, `test_installed_wheel_renders_bundled_templates_offline` |
| `t-catalog-isolation` | catalog parts stay inactive globally; paths and symlinks cannot reach active folders | `test_catalog_parts_do_not_activate_global_outputs`, `test_references_cannot_escape_catalog`, `test_catalog_rejects_symlink_to_active_skill` |
| `t-catalog-update` | all affected templates confirm together; local edits and edits during confirmation block writes; caught write failures restore prior files | `test_shared_update_prompts_for_all_affected_templates`, `test_shared_update_refuses_local_edits_in_other_affected_template`, `test_new_local_skill_file_during_confirmation_refuses_update`, `test_shared_update_rolls_back_a_write_failure`, `test_undeclared_catalog_sync_cannot_bypass_provenance` |
| `t-catalog-retirement` | removed shared-skill files retire; parts still referenced elsewhere remain | `test_shared_skill_sync_removes_retired_support_file`, `test_removing_reference_keeps_part_needed_by_other_template` |
| `t-catalog-toml` | qualified names and commented template lists remain valid | `test_qualified_catalog_name_vendors_valid_toml`, `test_commented_template_list_remains_valid` |

## Project scope

| id | behaviour | pinned by |
|---|---|---|
| `p-one-type` | both scopes describe a slice with one `SliceOutput` type, in two separate tables | `test_the_two_presets_agree_on_which_harnesses_exist`, `test_every_renderer_named_by_the_project_preset_exists` |
| `p-repo-relative` | a project slice sets `output` and never `destination`, so no machine path can reach a committed repo | `test_a_project_slice_is_written_relative_to_the_repo` |
| `p-one-order` | one instruction order per repo, not one per harness — four harnesses share `AGENTS.md` | `test_the_two_instruction_documents_are_byte_identical` |
| `p-instruction-order` | declared order reaches the document, below the template block and unsorted | `test_instruction_blocks_appear_in_declared_order_below_the_template` |
| `p-fixture-unsorted` | the fixture declares its fragments out of sorted order, or the ordering test would pass against a render that sorted | `test_the_project_fixture_declares_instructions_out_of_sorted_order` |
| `p-no-order-no-file` | a repo declaring no instructions generates neither document, so a permissions-only adopter keeps its own `CLAUDE.md` | `test_a_project_declaring_no_instructions_generates_neither_document` |
| `p-unknown-fragment` | an undeclared fragment name fails the render rather than rendering a short document | `test_an_unknown_instruction_fragment_fails_the_render` |
| `p-skills-per-harness` | every harness gets its rendered variant; byte-identical Codex, OpenCode and Pi variants may share `.agents/skills` | `test_each_harness_gets_its_own_flavour_of_a_skill`, `test_identical_convention_variants_share_the_agents_directory` |
| `p-skills-codex` | Codex receives project skills through `.agents/skills` | `test_codex_gets_project_skills_through_the_agents_convention` |
| `p-skills-tier` | a project skill replaces a template's of the same name, and the template's other skills still arrive | `test_a_project_skill_beats_a_template_skill_of_the_same_name` |
| `p-skills-copied` | a supporting file is named rather than decoded, so a mode survives | `test_a_supporting_file_is_copied_rather_than_rendered` |
| `p-skills-policy` | a skill targets every configured agent by default and may name a subset | `test_a_skill_policy_selects_only_its_named_agents` |
| `p-skills-unrendered` | migration's unrendered policy shares opaque document bytes without decoding them | `test_an_unrendered_skill_shares_opaque_document_bytes` |
| `p-opencode-race` | `check` reports the skills race when neither disabling variable is set, without moving the exit code | `test_check_reports_the_opencode_skills_race_without_failing`, `test_a_project_without_opencode_is_not_told_about_its_flag` |
| `p-opencode-both-flags` | the report reads both names, since OpenCode's flag is `broad \|\| direct` — a one-name check false-alarms at whoever set the broad switch | `test_either_flag_stops_the_opencode_skills_report` |
| `p-opencode-agents-race` | differing Codex and OpenCode variants require disabling OpenCode's external skill scan or narrowing the skill policy | `test_check_reports_the_codex_opencode_skill_race` |
| `p-opencode-off-is-off` | an explicit `=0` still reports, because the user has said the opposite of what silence would imply | `test_an_explicit_off_is_not_mistaken_for_on` |
| `p-claim` | two unrelated preset entries naming one document fail instead of silently overwriting each other | `test_two_agents_may_not_share_one_document` |
| `p-claim-exempts-instructions` | the shared `AGENTS.md` survives the guard, since its content is agent-independent by construction | `test_the_shared_instruction_document_is_still_allowed` |
| `p-gitignore-sync` | sync appends `.gitignore` entries for every configured harness's generated paths, idempotently on repeat syncs and without requiring a Git repository | `test_sync_extends_gitignore_when_a_harness_is_added_to_the_list`, `test_sync_maintains_gitignore_without_a_git_repository` in `tests/test_deployment.py` |
| `p-gitignore-stale` | removing a harness from the list leaves its `.gitignore` entry in place; entries are appended, never retired | `test_sync_leaves_gitignore_entries_of_removed_harnesses_in_place` in `tests/test_deployment.py` |

## Generated hook adapters

Everything below lives in emitted JavaScript, so a Python assertion can only inspect it as a
string. These are pinned by `tests/test_adapters_execute.py`, which runs the generated file under
node against real hook scripts, and skips when node is absent. All five mutants tried against the
adapters were caught by one of the tests named here.

| id | behaviour | source | pinned by |
|---|---|---|---|
| `ad-exact-matcher` | a simple matcher is an exact-string set, not a regex — so `Bash` does not guard `Bashful` | [hooks-adapters](hooks-adapters.md#what-is-not-reproduced) | `test_the_opencode_plugin_denies_mutates_and_survives_a_broken_hook` |
| `ad-exit-2` | exit 2 denies, carrying stderr as the reason | [hooks-adapters](hooks-adapters.md#exit-codes) | same |
| `ad-exit-other` | any other non-zero is a failed script, **not** a policy decision | [hooks-adapters](hooks-adapters.md#exit-codes) | same, and `test_the_pi_extension_blocks_mutates_and_survives_a_broken_hook` |
| `ad-updated-input` | `updatedInput` reaches the tool by in-place mutation on both harnesses | [hooks-adapters](hooks-adapters.md#the-mapping-table) | both execute tests |
| `ad-output-args` | OpenCode's `tool_input` comes from the *output* parameter, which is where the arguments are | [hooks-adapters](hooks-adapters.md#opencode) | `test_opencode_reads_tool_input_from_the_output_parameter`, `test_the_payload_the_hook_receives_is_abi_shaped` |
| `ad-unmapped` | an event with no mapping is named in a comment and not embedded | [hooks-adapters](hooks-adapters.md#the-mapping-table) | `test_an_unmapped_event_is_named_and_not_embedded` |
| `ad-prompt-skipped` | a `prompt` hook has no command to spawn, so it is filtered and reported in the file | [hooks-adapters](hooks-adapters.md#what-has-no-adapter) | `test_a_prompt_hook_is_reported_rather_than_dropped_in_silence` |
| `ad-omitted-fields` | `permission_mode` is omitted, never defaulted, on both | [hooks-adapters](hooks-adapters.md#payload-fidelity) | `test_pi_is_told_the_session_file_rather_than_a_run_mode`, `test_opencode_omits_the_two_fields_it_has_no_source_for` |
| `ad-reaches` | every mapping records the condition that would exercise it | [hooks-adapters](hooks-adapters.md#reachability) | `test_every_mapping_records_what_would_exercise_it` |

## Plugins

Four renderers off one fragment, and each states only the half of a reference it addresses by —
so most rows here are about what a harness *cannot* say. Source for all of them is
[plugins](plugins.md).

| id | behaviour | pinned by |
|---|---|---|
| `pl-addressing` | Claude, Codex, and Droid name a plugin `<name>@<marketplace>` | `test_claude_addresses_a_plugin_as_name_at_marketplace`, `test_codex_renders_enablement_and_the_marketplaces_its_plugins_reach`, `test_droid_renders_enablement_and_declared_marketplaces` |
| `pl-codex-quoting` | `@` in a Codex table header is quoted — `[plugins.nono@nolabs-ai]` is not valid TOML | `test_codex_quotes_the_at_sign_in_a_plugin_table_header` |
| `pl-pi-two-forms` | Pi renders a bare source string, and the object form only when the reference carries filters | `test_pi_renders_a_bare_source_string_when_nothing_filters_it`, `test_pi_renders_the_object_form_only_when_the_reference_carries_filters` |
| `pl-skip-unaddressable` | a reference the harness cannot name is skipped, not refused — a mixed set is the ordinary case | `test_claude_skips_a_reference_with_no_marketplace`, `test_pi_skips_a_reference_with_no_source`, `test_unaddressable_names_the_key_each_harness_needs` |
| `pl-marketplace-used` | a marketplace no rendered plugin names is not registered; one registered elsewhere still enables its plugin | `test_codex_omits_a_marketplace_no_rendered_plugin_names`, `test_codex_enables_a_plugin_whose_marketplace_is_registered_elsewhere` |
| `pl-marketplace-verbatim` | a `[marketplaces.<name>]` table's keys pass through — Codex owns that schema | `test_codex_carries_a_marketplaces_own_keys_through_untouched` |
| `pl-strict-reference` | `source` / `marketplace` are loadout's own vocabulary, so a typo is refused; nested blocks are not | `test_an_unknown_key_on_a_reference_is_refused`, `test_a_plugin_name_at_the_top_level_is_refused` |
| `pl-null-off` | a `null` overlay switches a plugin off without deleting the fragment that declares it | `test_a_null_overlay_switches_a_plugin_off` |
| `pl-compose` | `enabledPlugins` is the fourth slice in `settings.json` and displaces none of the other three | `test_claude_plugins_compose_with_permissions_and_the_settings_residual` |
| `pl-pi-runtime` | Pi's preset preserves its live `lastChangelogVersion` cursor through plugin rendering and refuses to take ownership of it through a settings fragment | `test_pi_preserves_its_runtime_changelog_cursor`, `test_pi_runtime_changelog_cursor_cannot_be_a_settings_source` |
| `pl-opencode-absent` | OpenCode has no enablement list, so naming the slice is an error | `test_opencode_rejects_a_plugins_key` |
| `pl-not-automatic` | unlike permissions and mcp, an absent `plugins` key means "not managed", not "none" | `test_an_agent_block_naming_no_plugins_renders_none` |
| `pl-x-projection` | each inverse recovers only the half its harness states | `test_claude_carries_the_marketplace_and_drops_the_source`, `test_codex_carries_the_marketplace_registration_as_well`, `test_droid_carries_enablement_and_marketplace_registration`, `test_pi_carries_the_source_and_its_filters` |
| `pl-x-off-reported` | a plugin the file marks off has no fragment representation, so it is reported rather than extracted | `test_a_plugin_switched_off_in_the_file_is_reported_not_extracted` |
| `pl-x-pi-name` | Pi's document carries no name; one is derived, a collision falls back to the source, and **every entry is reported** | `test_a_pi_name_comes_from_the_last_segment_ahead_of_the_pinned_ref`, `test_two_packages_deriving_one_name_keep_both_references`, `test_every_pi_entry_reports_the_name_it_had_to_invent` |
| `pl-x-pi-object` | `{"source": x}` with nothing filtering it renders as the string form, so the bytes change and it is reported | `test_an_object_entry_that_filters_nothing_is_reported_as_renormalised` |

`pi-plugins` is the one inverse whose document round trip closes while `notes` is non-empty —
re-rendering needs only the source, which survives exactly, but the derived name is an invention
and saying so is the point.

## MCP server definitions

One file (`servers.py`) parses `<source>/mcp.toml` and renders it through five per-harness
functions plus a project-scope variant for Claude — the same shape as `plugins.py`. Source for
all of them is [servers](servers.md).

| id | behaviour | source | pinned by |
|---|---|---|---|
| `srv-parse` | http and stdio servers both parse | [servers](servers.md#the-input-is-sourcemcptoml) | `test_http_and_stdio_servers_parse` |
| `srv-order` | declaration order is preserved | [servers](servers.md#the-input-is-sourcemcptoml) | `test_declaration_order_is_preserved` |
| `srv-bad-transport` | an unknown transport is refused | [servers](servers.md#the-input-is-sourcemcptoml) | `test_an_unknown_transport_is_refused` |
| `srv-http-needs-url` | an http server with no `url` is refused at parse time, not render time | [servers](servers.md#the-input-is-sourcemcptoml) | `test_http_without_a_url_is_refused` |
| `srv-stdio-needs-command` | a stdio server with no `command` is refused at parse time | [servers](servers.md#the-input-is-sourcemcptoml) | `test_stdio_without_a_command_is_refused` |
| `srv-no-file` | a missing `mcp.toml` is no servers, not an error | [servers](servers.md#the-input-is-sourcemcptoml) | `test_a_missing_file_is_no_servers_rather_than_an_error` |
| `srv-unknown-key` | a stray key is refused, not ignored | [servers](servers.md#the-input-is-sourcemcptoml) | `test_an_unknown_key_is_refused` |
| `srv-all-keys` | every documented key is accepted | — (test is the record) | `test_every_documented_key_is_accepted` |
| `srv-pi-bearer` | Pi names the auth variable `bearerTokenEnv` | [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md) | `test_pi_names_the_auth_variable_bearer_token_env` |
| `srv-stdio-shape` | a stdio server carries `command` and `args` | — (test is the record) | `test_a_stdio_server_carries_command_and_args` |
| `srv-no-secret` | `auth_env_var`'s value never reaches a rendered file, on any of the five | [ADR 0008](../decisions/0008-generated-files-carry-no-machine-state.md) | `test_no_renderer_emits_a_secret_value` |
| `srv-droid-shape` | Droid uses the `mcpServers` wrapper and environment-reference bearer header at both scopes | [droid](droid.md#mcp-servers) | `test_droid_uses_the_mcpservers_wrapper_and_env_bearer_header`, `test_droid_writes_global_mcp_json` |
| `srv-codex-table` | Codex emits one `[mcp_servers.<name>]` table per server | [servers](servers.md) | `test_codex_emits_a_table_per_server` |
| `srv-claude-env-always` | Claude's stdio entry always carries `env`, even empty | — (test is the record) | `test_claude_stdio_entry_always_carries_env_even_when_empty` |
| `srv-codex-env-omit` | Codex omits the `env` table when empty — unlike Claude | [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md) | `test_codex_omits_the_env_table_when_empty` |
| `srv-codex-env-sort` | Codex sorts `env` keys | — (test is the record) | `test_codex_sorts_env_keys` |
| `srv-opencode-array` | OpenCode's stdio server is one `command` array combining command and args, not separate fields | [servers](servers.md) | `test_opencode_stdio_command_and_args_are_one_array` |
| `srv-opencode-interp` | OpenCode's http auth uses `{env:VAR}` interpolation, not `${VAR}` | [servers](servers.md) | `test_opencode_http_auth_uses_env_interpolation` |
| `srv-project-claude` | a project renders `.mcp.json` for Claude | [servers](servers.md) | `test_a_project_renders_mcp_json_for_claude` |
| `srv-pi-no-project` | Pi gets no project destination — `.mcp.json` already serves it | [servers](servers.md#pi-has-no-project-destination) | `test_pi_gets_no_project_destination` |
| `srv-codex-no-project` | Codex gets no project destination yet — open question, not a gap | [servers](servers.md#codex-has-no-project-destination-yet) | `test_codex_gets_no_project_destination_yet` |
| `srv-template-tier` | a template contributes its servers, beneath the project | [templates](templates.md) | `test_a_template_contributes_its_servers` |
| `srv-claude-global` | Claude's global entry writes the owned `mcpServers` key in `.claude.json` | [servers](servers.md#claudes-global-entry-writes-claudejson) | `test_claude_global_writes_claude_json` |
| `srv-automatic` | `mcp` is automatic, like `permissions` — no per-agent authoring decision | [servers](servers.md#the-input-is-sourcemcptoml) | `test_a_global_source_renders_claude_servers_without_being_named` |
| `srv-codex-global` | Codex's global destination is `~/.codex/config.toml`, owning `mcp_servers` only | [servers](servers.md#ten-destinations-eight-built) | `test_codex_global_writes_config_toml` |
| `own-foreign-survives` | applying owned keys leaves comments, another tool's managed block, a multi-line string and `[projects."…"]` untouched | [codex](codex.md#configtoml-is-co-owned) | `test_content_loadout_does_not_generate_survives` |
| `own-in-place` | an owned block is replaced where it sits, so the harness appending a table of its own is not reported as drift | [codex](codex.md#configtoml-is-co-owned) | `test_the_harness_writing_its_own_table_is_not_drift` |
| `own-declared-not-derived` | removing every server strips the whole table tree — the case a set derived from what is written cannot express | [0017](../decisions/0017-ownership-may-be-declared-instead-of-derived.md) | `test_removing_every_server_removes_the_whole_table_tree` |
| `own-single-pass` | every slice writing one unowned file composes into a single application, so no slice reads another's output | [0017](../decisions/0017-ownership-may-be-declared-instead-of-derived.md) | `test_two_slices_become_one_application` |
| `own-key-clash` | two slices declaring one key is refused rather than silently letting the last win | [0017](../decisions/0017-ownership-may-be-declared-instead-of-derived.md) | `test_two_slices_declaring_one_key_is_refused` |
| `own-scalar-hoist` | a later slice's scalar stays above an earlier slice's table, so it is not read as a member of it | [codex](codex.md#configtoml-is-co-owned) | `test_a_later_slices_scalar_stays_above_an_earlier_slices_table` |
| `codex-one-table` | a server's definition and its approval policy share one `[mcp_servers.<name>]` table, from one renderer | [codex](codex.md#configtoml-is-co-owned) | `test_definition_and_policy_share_one_table` |
| `defaults-record` | a key removed from the `defaults` fragment is removed from `config.toml`, via the owned-key record | [codex](codex.md#configtoml-is-co-owned) | `test_a_key_removed_from_the_fragment_is_removed_from_config_toml` |
| `defaults-untouched` | a Codex setting the fragment never names is left alone | [codex](codex.md#configtoml-is-co-owned) | `test_a_settings_key_nobody_manages_is_left_alone` |
| `defaults-record-drift` | a hand-edited record is reported by `check` rather than silently changing what is stripped | [codex](codex.md#configtoml-is-co-owned) | `test_a_stale_record_is_reported_as_drift` |
| `defaults-nested` | nested defaults own leaves and preserve sibling fields, comments and per-skill overrides byte-for-byte | [codex](codex.md#nested-defaults) | `test_nested_defaults_preserve_skill_overrides_and_unrelated_bytes` |
| `defaults-nested-removal` | recorded removal and explicit `$remove` paths remove only the managed leaf | [codex](codex.md#nested-defaults) | `test_nested_removal_leaves_sibling_settings_and_array_tables`, `test_nested_default_updates_and_explicit_removal` |
| `defaults-nested-overlap` | present and recorded ownership cannot overlap another slice's root | [codex](codex.md#nested-defaults) | `test_overlapping_defaults_ownership_is_refused`, `test_recorded_nested_ownership_cannot_overlap_another_slice` |
| `defaults-key-paths` | quoted/dotted TOML paths remain distinct from literal dotted JSON keys | [codex](codex.md#nested-defaults) | `test_nested_keys_support_dotted_and_quoted_toml`, `test_literal_keys_are_quoted_in_the_ownership_record` |
| `defaults-statement-boundaries` | multiline strings and arrays cannot masquerade as assignments or table headers | [codex](codex.md#nested-defaults) | `test_table_and_assignment_lookalikes_inside_strings_are_untouched`, `test_replacing_multiline_values_removes_the_whole_owned_assignment` |
| `defaults-parent-refusal` | inline/scalar parents and array-element ownership refuse without claiming siblings | [codex](codex.md#nested-defaults) | `test_unrepresentable_parent_shapes_refuse_without_widening_ownership` |
| `defaults-array-identity` | managed array subtables stay attached to their element when the shape changes | [codex](codex.md#nested-defaults) | `test_array_subtables_stay_with_their_element_when_shape_changes` |
| `defaults-record-upgrade` | unversioned literal-key ownership migrates without claiming foreign nested settings | [codex](codex.md#nested-defaults) | `test_legacy_literal_ownership_does_not_claim_nested_overrides`, `test_new_records_distinguish_literal_names_from_nested_paths` |
| `defaults-record-version` | unknown or malformed ownership formats refuse before writes | [codex](codex.md#nested-defaults) | `test_unknown_or_invalid_record_formats_are_refused`, `test_unknown_ownership_format_refuses_before_destination_writes` |
| `defaults-comment-lines` | Unicode line separators inside comments cannot redirect managed assignments | [codex](codex.md#nested-defaults) | `test_unicode_separators_stay_inside_comments` |
| `defaults-multiline-scan` | complete string and container boundaries are scanned with linear parsing work | [codex](codex.md#nested-defaults) | `test_statement_scanner_preserves_string_and_container_boundaries`, `test_multiline_values_require_linear_parsing_work` |
| `defaults-file-newlines` | sync and check preserve unrelated CRLF bytes through addition, update and removal | [codex](codex.md#nested-defaults) | `test_file_backed_sync_preserves_foreign_crlf_bytes` |
| `defaults-first-array` | initial array insertion and scalar-to-array transitions are immediately idempotent | [codex](codex.md#nested-defaults) | `test_first_array_insertion_is_idempotent`, `test_first_array_sync_is_immediately_clean` |
| `srv-pi-global` | Pi's global destination is its own `mcp.json`, written directly | [servers](servers.md#ten-destinations-eight-built) | `test_pi_global_writes_its_own_mcp_json` |
| `srv-opencode-compose` | OpenCode's global `mcp` key composes with `permission` in the same `opencode.json`, the same shape project scope already proves | [servers](servers.md#ten-destinations-eight-built) | `test_opencode_global_composes_the_mcp_key_with_permission` |
| `srv-unpermitted` | a server defined but named by no `[mcp]` policy entry is reported | [servers](servers.md#a-server-defined-but-not-permitted-is-reported) | `test_a_defined_server_with_no_policy_is_reported` |
| `srv-wildcard-silent` | a `server/*` policy entry silences the notice | [servers](servers.md#a-server-defined-but-not-permitted-is-reported) | `test_a_server_covered_by_a_wildcard_is_silent` |
| `srv-one-tool-silent` | naming even one tool silences the notice — it is not a completeness check | [servers](servers.md#a-server-defined-but-not-permitted-is-reported) | `test_a_server_covered_by_one_tool_is_silent` |

### Extraction

| id | behaviour | pinned by |
|---|---|---|
| `srv-x-every-renderer` | every `-servers` renderer is named in `EXTRACTORS`, `VALUE_EXTRACTORS` or `NOT_INVERTED` | `test_every_definition_renderer_has_an_inverse` |
| `srv-x-unknown-server` | an unrecognised extractor name is an error, not a silent empty result | `test_an_unknown_name_is_an_error_rather_than_a_silent_empty` |
| `srv-x-claude-http` | a Claude http server round-trips | `test_claude_http_server_round_trips` |
| `srv-x-claude-stdio` | a Claude stdio server round-trips | `test_claude_stdio_server_round_trips` |
| `srv-x-claude-no-auth` | a Claude http server with no auth round-trips | `test_claude_http_server_without_auth_round_trips` |
| `srv-x-bad-bearer` | a header that is not a plain bearer-env reference is reported, never guessed at | `test_a_bad_bearer_header_is_reported_not_guessed` |
| `srv-x-claude-unowned` | a key `.mcp.json` doesn't own is reported — the file has no other owner | `test_an_unowned_key_in_the_mcp_json_file_is_reported` |
| `srv-x-claude-unrecognised` | an unrecognised server type is reported and dropped | `test_an_unrecognised_server_type_is_reported_and_dropped` |
| `srv-x-no-alias` | extraction never aliases the document it was handed | `test_extraction_does_not_alias_the_document` |
| `srv-x-opencode-http` | an OpenCode remote server round-trips | `test_opencode_http_server_round_trips` |
| `srv-x-opencode-stdio` | an OpenCode local server round-trips | `test_opencode_stdio_server_round_trips` |
| `srv-x-opencode-rest` | extraction says nothing about the rest of `opencode.json` — `permission` has its own owner | `test_opencode_does_not_note_the_rest_of_the_document` |
| `srv-x-opencode-missing` | a missing `mcp` key extracts empty rather than failing | `test_a_missing_mcp_key_extracts_empty_rather_than_failing` |
| `srv-x-opencode-unrecognised` | an unrecognised OpenCode server type is reported | `test_an_unrecognised_opencode_server_type_is_reported` |
| `srv-x-opencode-no-alias` | OpenCode extraction does not alias the document | `test_opencode_extraction_does_not_alias_the_document` |
| `srv-x-claude-global-http` | the global `mcpServers` value's http server round-trips | `test_claude_global_http_server_round_trips` |
| `srv-x-claude-global-stdio` | the global `mcpServers` value's stdio server round-trips | `test_claude_global_stdio_server_round_trips` |
| `srv-x-claude-global-owned` | the global document wraps `mcpServers` and leaves adjacent runtime keys unowned | `test_claude_global_wraps_in_mcpservers_and_owns_only_that_key` |
| `srv-x-pi-http` | a Pi http server round-trips | `test_pi_http_server_round_trips` |
| `srv-x-pi-stdio` | a Pi stdio server round-trips | `test_pi_stdio_server_round_trips` |
| `srv-x-pi-unowned` | a key Pi's `mcp.json` doesn't own is reported | `test_an_unowned_key_in_pis_mcp_json_is_reported` |
| `srv-x-pi-unrecognised` | an unrecognised Pi entry is reported and dropped | `test_an_unrecognised_pi_server_entry_is_reported_and_dropped` |
| `srv-x-codex-http` | a Codex http server round-trips | `test_codex_http_server_round_trips` |
| `srv-x-codex-stdio` | a Codex stdio server round-trips | `test_codex_stdio_server_round_trips` |
| `srv-x-codex-header` | the header comment lines are not mistaken for a server table | `test_codex_header_comment_lines_are_not_mistaken_for_servers` |
| `srv-x-codex-unrecognised` | an unrecognised Codex entry is reported and dropped | `test_an_unrecognised_codex_server_entry_is_reported_and_dropped` |
| `srv-x-not-inverted` | `codex-servers`, like `codex-plugins`, has a written and tested inverse that cannot be registered — a `DocumentTextSpec` producing TOML text, not the parsed document every `VALUE_EXTRACTORS` member takes | [servers](servers.md#two-renderers-whose-inverse-cannot-be-registered) | `test_every_definition_renderer_has_an_inverse` (via `NOT_INVERTED`) |

## Recorded but not testable at render time

| id | behaviour | why |
|---|---|---|
| `wrapper-bypass` | an allowlisted command taking another command as an argument voids every deny | A property of the rule *set*, not of rendering. The intended fix is a build-time `neverallow` ceiling that refuses to emit. Not implemented. |
| `ask-shadows-allow` | an `ask`/`deny` entry that is a strict prefix of an `allow` makes the allow unreachable on all five harnesses | Same class — generalises [ADR 0005](../decisions/0005-a-deny-cannot-carry-exceptions.md). loadout does not detect it today; found by hand in `~/ac` on 2026-08-08 (`gh api --method` shadowing `gh api --method GET`). Candidate for a source validator. |
| `oc-compound` | OpenCode takes the least-permitted verdict across a `;`/`&&`/`\|` chain | Harness runtime behaviour. |
| `config-dir-vars` | which variable relocates each harness's config dir | Upstream facts about the binaries, recorded in [README](README.md#relocating-the-config-directory) with the version each was verified against. loadout renders the same bytes either way, so nothing observable at render time distinguishes a right answer from a wrong one. Re-verify by inspecting the binaries, not by running the suite. |
| `dest-env-preserve` | with `preserve`, the environment selects which file's foreign keys are merged | Falls out of `preserve` reading the destination ([ADR 0011](../decisions/0011-a-destination-follows-a-relocated-harness.md)); pinned indirectly by `test_each_destination_preserves_its_own_foreign_keys`, which fixes the paths rather than varying the environment. |
| `dest-env-orphan` | a destination the environment has moved away from leaves the path set, so `check` cannot see the stale file | Inherent to render-time resolution; the fix is the orphan sidecar [0008](../decisions/0008-generated-files-carry-no-machine-state.md) defers. Nothing is rendered differently. |

## Reachable only by calling a renderer directly

`pi-moves-key` is a faithful port of upstream behaviour that the current data flow cannot
trigger. `render_pi` moves a key when the same entry appears in two categories of the `Rules` it
is handed — but every path into a renderer now runs `merge_rules` first, and its deny-wins
resolution guarantees no entry survives in two categories. `render_pi` also builds its `bash` and
`mcp` maps from scratch rather than from `base`, so a base document cannot reintroduce the case
either.

Demonstrated when global scope began merging: the fixture has `zeta` in both allow and deny, and
regenerating expected output changed Claude, Codex and OpenCode while **Pi's file was
byte-identical** — the pop-and-reassign had been producing the same result as a single insert.
OpenCode's entry did move, because assigning in place had been holding it at the earlier allow
position (ADR 0006's other half).

Keep the behaviour: it is a faithful port, it is pinned by a direct unit test, and it becomes
observable again if resolution ever changes or a renderer is called outside the pipeline. But do
not treat whole-document comparison as covering it.

**Extraction is the exception to this whole section, and it is worth stating plainly.** The
reachability argument above is about what loadout can *produce*. Extraction reads what is
already on disk — hand-maintained across five harnesses, written by other tools — so every shape
declared unreachable here is ordinary input to it.

That is why `x-twice-listed` and `x-order-loss` are pinned against constructed values and
synthetic documents rather than through the pipeline: `merge_rules` guarantees no entry survives
in two categories, so no rendered artifact can exercise them. It also means `pi-moves-key`'s
sibling difference is no longer merely a fidelity question — `render_pi` round-trips and
`render_opencode` / `render_pi_project` do not, so harmonising them breaks invertibility as well
as output. See [ADR 0013](../decisions/0013-a-renderer-change-is-checked-against-extraction.md)
and [extraction](extraction.md#what-each-renderer-loses).
