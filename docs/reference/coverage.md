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

## Migration planning

CLI integration is covered in `tests/test_init_workflow.py` and `tests/test_cli.py`:

| behaviour | pinned by |
|---|---|
| metadata-only unresolved preview, explicit scope and approval | `test_unresolved_json_preview_does_not_mutate`, `test_noninteractive_apply_requires_explicit_approval`, `test_explicit_project_scope_does_not_adopt_global_live_state` |
| project adoption, checkpoint/staging and nested repeat no-op | `test_project_preview_and_apply_adopt_and_stage` |
| global cwd default, actual source registration and explicit replacement | `test_global_defaults_to_cwd_and_registers_actual_manifest`, `test_existing_source_registration_is_an_explicit_transaction` |
| duplicate source choices are independent of approval | `test_global_copy_conflict_requires_selection_even_with_yes` |
| interactive choice/decline and cancellation before mutation | `test_interactive_scope_and_agent_choices_then_decline`, `test_global_prompt_cancellation_with_yes_does_not_mutate` |
| preview values stay private; malformed mapping shapes are usage errors | `test_preview_never_prints_secret_values`, `test_invalid_mapping_shape_is_usage_error` |
| resume/recover JSON results and preserved recovery conflicts | `test_cli_resume_and_recover_use_the_reported_journal`, `test_cli_recovery_conflicts_report_paths_and_nonzero_status` |
| native harness-add avoids false membership | `test_native_harness_add_refuses_without_creating_false_membership` |

Native bundled-skill lifecycle is covered in `tests/test_native_skill_installation.py`:

| behaviour | pinned by |
|---|---|
| distinct/shared active trees, bytes/assets and receipt-coherent uninstall | `test_native_install_update_uninstall_and_receipts` |
| all-target conflict preflight and modified-source/output preservation | `test_one_conflicting_target_preserves_every_target`, `test_new_occupied_output_is_preflighted_before_source_changes` |
| native hashing includes intentional supporting assets and additions | `test_native_hash_preserves_intentional_assets_and_detects_all_additions` |
| failed deployment rolls back source, outputs and receipts | `test_native_failure_rolls_back_sources_outputs_and_receipts` |
| rollback failure retains protected recovery outside deployable trees | `test_failed_rollback_retains_private_recovery_outside_native_trees` |
| native/legacy targets work together, no-target state is explicit | `test_native_and_legacy_routes_install_together`, `test_artifact_manifest_without_skills_reports_no_routes` |
| mixed CLI changes finish normal global sync and reject changed routing | `test_native_cli_finishes_global_sync_for_pending_legacy_outputs`, `test_native_configuration_changes_invalidate_inspection` |
| mixed update/uninstall checks frozen legacy preimages at each mutation and rolls back native sources, outputs and receipts while preserving concurrent files | `test_legacy_race_preserves_user_output_and_rolls_back_native_changes`, `test_missing_legacy_output_cannot_be_occupied_during_native_deployment` |
| missing native sources retain external ownership for reinstall and uninstall | `test_missing_native_source_keeps_marker_ownership_for_reinstall_and_uninstall` |
| missing-source ownership metadata remains guarded against invalid content and changes after inspection | `test_missing_native_source_rejects_marker_changes_after_inspection`, `test_missing_native_source_does_not_claim_invalid_ownership_metadata` |
| CLI status reports actual source trees and consumers | `test_native_cli_reports_each_source_and_installs` |
| package includes onboarding reference | `test_built_wheel_contains_the_complete_skill_tree` in `tests/test_bundled_skill.py` |

All rows below are exercised in `tests/test_migration.py`; the contract is
[migration.md](migration.md).

| behaviour | pinned by |
|---|---|
| explicit scope and shared membership ambiguity | `test_shared_instructions_do_not_invent_scope_or_harnesses` |
| native categories, ordered literals, no original mutation | `test_project_plan_reconstructs_composite_native_data_without_mutation` |
| verified portable conversion and silent-loss fallback | `test_permission_extraction_is_verified_after_serialization`, `test_silent_extractor_losses_use_native_fallback` |
| opaque subtree bytes, modes and relative dependencies | `test_nested_instructions_and_program_dependency_layout`, `test_static_hook_dependency_is_inventoried_and_reconstructed` |
| runtime exclusions depend on root roles, not ancestor names | `test_workspace_parent_names_are_not_runtime_roles`, `test_symlink_into_known_runtime_root_is_excluded` |
| tracked nested instructions under dependency/build directories | `test_tracked_nested_instructions_survive_build_walk_filters` |
| credentials and personal source never enter checkpoint paths | `test_credentials_and_personal_sources_are_excluded_from_checkpoints`, `test_gitignored_authored_configuration_remains_personal` |
| global relocation and mixed runtime documents | `test_global_live_roots_honor_relocation_and_partial_runtime_ownership`, `test_default_claude_mcp_is_sibling_of_default_harness_directory`, `test_live_roots_expand_using_supplied_home` |
| explicit dotfile roles and conflicting source selection | `test_dotfile_directory_names_require_confirmed_mapping`, `test_source_and_live_conflict_requires_explicit_selection` |
| external symlinks require mappings and keep original targets | `test_symlink_external_target_requires_mapping_and_is_never_written` |
| unknown and unmaterialized native input blocks completion | `test_unknown_native_and_materialization_inputs_block_complete_migration`, `test_unmapped_external_hook_dependency_blocks_completion` |
| initialized source produces an idempotent no-migration result | `test_existing_source_is_not_reinterpreted` |
| serialized source mutation fails isolated full comparison | `test_validation_rejects_actual_serialized_source_changes` |
| dormant routes and first-entry activation | `test_empty_routes_do_not_suppress_fallback_instructions`, `test_dormant_document_parts_activate_on_first_entry`, `test_originally_empty_instruction_file_is_recreated` |
| skill first entry, edit, rename and deletion | `test_skill_tree_first_entry_edit_rename_and_delete` |
| synthetic public dotfile layouts | `test_global_synthetic_dbochman_and_anaiis_shapes` |
| partial output mode policy and private clone behavior | `test_partial_output_mode_policy_preserves_adopted_destination_mode`, `test_private_routes_are_optional_in_a_checkout_without_personal_files` |
| ignored generated links preserve tracked source privacy | `test_ignored_symlink_output_keeps_tracked_canonical_source_public` |
| shared skill membership and directory preconditions | `test_shared_agents_tree_keeps_its_actual_consumers`, `test_directory_precondition_detects_a_new_authored_input` |
| canonical runtime roles through symlinked harness roots, with unchanged crawl limits | `test_symlinked_harness_root_excludes_canonical_runtime_aliases`, `test_symlinked_harness_root_does_not_expand_inventory_boundary`, `test_symlink_cycle_in_harness_root_is_reported` |
| private canonical originals and future private tree entries stay excluded | `test_private_instruction_excludes_canonical_original_and_aliases`, `test_private_tree_namespace_ignores_future_entries` |
| retirement accounts for mapped trees, duplicate copies and symlink targets | `test_mapped_tree_retirement_accounts_for_every_original`, `test_duplicate_and_symlink_sources_have_explicit_retirement`, `test_mapped_private_and_runtime_originals_have_retained_dispositions` |
| retirement uses final output topology and preserves required destinations | `test_ancestor_spelling_does_not_retire_a_generated_destination`, `test_an_original_required_by_another_output_is_not_retired`, `test_symlinked_harness_directory_uses_final_retirement_topology` |
| dependency mappings agree in original and deployed coordinates | `test_mapped_relative_dependencies_preserve_source_and_deployed_layout`, `test_absolute_dependency_on_relocated_original_stays_blocked`, `test_relative_dependency_with_disagreeing_mapping_stays_blocked` |
| dependency checks and retirement share final symlink topology | `test_symlinked_harness_absolute_dependency_on_retired_source_is_blocked`, `test_symlinked_harness_dependencies_follow_final_destinations` |
| direct harness-root sources preserve HOME/relocated destination links and ancestor aliases while blocking stale source references | `test_direct_harness_root_absolute_source_dependencies_are_blocked`, `test_direct_harness_root_dependencies_keep_active_destinations` |
| recognizable unsupported global harness trees block migration | `test_global_unsupported_harness_tree_is_unresolved` |
| instruction discovery preconditions every traversed directory | `test_instruction_search_records_each_traversed_directory`, `test_instruction_search_preconditions_canonical_root_listing` |

## Migration transactions

All rows below are exercised in `tests/test_migration_transaction.py`; the contract is
[migration.md](migration.md#applying-a-resolved-plan).

| behaviour | pinned by |
|---|---|
| project checkpoint, staging, source edits and normal sync/check | `test_project_migration_checkpoints_stages_and_uses_normal_lifecycle` |
| global partial fields, adopted modes and explicit registration | `test_global_migration_preserves_foreign_fields_and_registers_explicit_write`, `test_partial_document_symlink_freezes_foreign_bytes_before_replacement` |
| existing-source no-op retains explicit registration | `test_existing_source_noop_can_write_explicit_registration` |
| new baseline includes ordinary source and excludes private namespaces | `test_new_repository_checkpoint_filters_private_namespaces_and_ignored_files`, `test_new_repository_cannot_start_at_home` |
| enclosing root, detached HEAD and linked worktrees | `test_nested_global_source_uses_enclosing_repository_only`, `test_existing_checkpoint_changes_only_adopted_index_entries`, `test_linked_worktree_preserves_other_checkout_index` |
| unrelated partial staging and unstaged ignore hunks survive | `test_unrelated_partial_index_and_ignore_hunks_survive` |
| symlink topology, runtime visibility, external originals and retirement | `test_directory_symlink_retains_runtime_visibility_and_external_targets`, `test_file_and_directory_symlinks_checkpoint_link_entries_and_retire_only_originals` |
| ambiguous staging and unmerged index refuse preflight | `test_partially_staged_adopted_files_require_resolution_even_when_private`, `test_unmerged_index_is_rejected_before_preimages` |
| input/index/HEAD and Git privacy changes invalidate preparation | `test_stale_preparation_is_rejected_before_journal`, `test_source_privacy_change_in_info_exclude_blocks_preparation` |
| unresolved plans cannot reach application | `test_unresolved_plan_cannot_be_prepared` |
| hook, identity and signing failures preserve original inputs | `test_hook_failure_has_journal_and_does_not_write_source`, `test_identity_failure_keeps_initial_files_and_can_resume_after_configuration`, `test_signing_failure_preserves_requested_signing_and_original_files` |
| first entry, edit and last deletion use the adopted receipt | `test_first_skill_entry_edit_and_last_deletion_use_adopted_receipt` |
| literal Git paths and representable ignore patterns | `test_literal_metacharacter_paths_do_not_expand_staging_or_ignore_rules`, `test_newline_output_is_rejected_before_git_initialization` |
| interrupted filesystem/index writes resume without duplicate checkpoint | `test_interrupted_deployment_resumes_frozen_bytes_and_normal_receipt`, `test_interrupted_final_index_replacement_resumes_without_duplicate_commit` |
| checkpoint survives refresh failure and recovery | `test_failure_after_checkpoint_commit_records_and_refreshes_success`, `test_recovery_after_final_staging_keeps_baseline_and_restores_original_entries` |
| recovery preserves concurrent files and subsequent user commits | `test_recovery_preserves_conflicting_edits_and_successful_baseline`, `test_recovery_after_user_commit_preserves_committed_source_and_index` |
| resume revalidates inputs and refuses intervening new-repo commits | `test_resume_after_hook_failure_refuses_changed_checkpoint_content`, `test_new_repository_resume_refuses_an_intervening_user_commit` |
| resume rechecks original, canonical and new-source Git privacy at checkpoint and staging boundaries | `test_resume_rechecks_privacy_after_failed_checkpoint`, `test_resume_rechecks_privacy_after_checkpoint_and_before_staging`, `test_resume_rechecks_symlinked_exclude_file_contents` |
| unchanged output bytes, modes and absences remain guarded across deployment, retirement and final index interruptions | `test_resume_validates_unchanged_outputs_at_every_completion_boundary`, `test_unchanged_outputs_validate_before_retiring_originals`, `test_final_index_resume_validates_dormant_outputs` |
| ancestor aliases, including nested source directories in enclosing repositories, preserve physical checkpoint paths, terminal link entries, source staging and private directory modes | `test_source_ancestor_alias_preserves_checkpoint_paths_and_link_entries`, `test_nested_source_alias_preserves_enclosing_repository_index_and_privacy`, `test_aliased_source_keeps_private_namespaces_protected` |
| preimages stay private through hooks; malformed state is refused | `test_untrusted_recovery_state_is_rejected_before_checkpoint`, `test_journal_is_ignored_during_the_real_checkpoint_hook`, `test_loaded_journal_rejects_invalid_shape_before_recovery` |

## Git integration

The contract is [git-integration.md](git-integration.md). These cases run in
`tests/test_git_integration.py`, with real Git events and isolated HOME/harness roots.

| behaviour | pinned by |
|---|---|
| actual pre-commit checks partial staging and preserves HEAD on invalid source | `test_real_precommit_validates_partial_index_not_worktree` |
| supplied alternate index, relative index and missing-index refusal | `test_real_commit_honors_alternate_index_and_relative_index_from_nested_cwd` |
| unborn, empty, corrupt and unmerged indexes cannot silently validate nothing | `test_unborn_empty_corrupt_and_unmerged_indexes` |
| HEAD ownership survives declaration/config/source removal; untracking outputs is allowed | `test_real_precommit_uses_head_ownership_after_declaration_removal` |
| migrating absolute/escaping old sources validates only index dependencies; HEAD and index outputs remain protected | `test_head_source_migration_keeps_head_and_index_output_ownership` |
| selected profile controls instruction/permission ownership before destination expansion | `test_staged_ownership_selects_profile_before_resolving_destinations` |
| staged dependency deletion never uses the working replacement | `test_required_source_deletion_does_not_read_unstaged_replacement` |
| bare bundled and machine template names require staged vendoring | `test_bare_templates_never_fall_back_to_machine_or_bundled_source` |
| nested global source includes sibling dependencies and isolates global destinations | `test_nested_global_snapshot_contains_sibling_sources_and_isolates_destinations` |
| external paths, source symlink escapes and submodule dependencies fail | `test_external_and_submodule_dependencies_fail_without_reading_live_sources` |
| optional private files stay absent and programs stay data | `test_optional_private_source_is_absent_and_programs_are_not_executed` |
| local custom paths, exact managed identity, other-source/occupied/shared preservation | `test_hook_installation_preserves_occupied_shared_and_other_source_hooks` |
| linked worktree index and local hooks are independent; common hooks are preserved | `test_linked_worktree_validates_supplied_index_and_preserves_common_hooks` |
| main checkout preserves default and absolute custom hooks shared with linked worktrees; exclusive paths still install | `test_main_checkout_preserves_hooks_shared_with_linked_worktrees` |
| sharing is checked again after standalone preview and at migration apply/resume/recovery boundaries | `test_standalone_install_rechecks_hook_sharing_after_preview`, `test_migration_rechecks_hook_sharing_at_mutation_boundaries` |
| explicit init preview, baseline before hooks and recovery retain Git metadata | `test_init_hook_preview_and_new_git_metadata_are_transactional` |
| hook write interruption resumes, external hook edits survive recovery | `test_hook_failure_can_resume_and_external_hook_is_preserved_by_recovery` |
| global init records matching registered source/profile | `test_global_init_hooks_keep_selected_source_and_registered_profile` |
| checkout/merge regenerate with receipts and explain failure after Git completed | `test_actual_checkout_and_merge_regenerate_or_preserve_edits_after_git_completed` |
| file checkout does not regenerate | `test_post_checkout_file_event_does_not_regenerate` |
| missing installed CLI explains that checkout already completed | `test_missing_loadout_on_path_explains_completed_checkout` |
| modified hook journal bytes and pre-init failure are recoverable without hook execution | `test_hook_journal_rejects_tampered_script_and_recovers_before_git_init` |

## Cross-cutting

| id | behaviour | source | pinned by |
|---|---|---|---|
| `last-match-opencode` | denies emitted after the allows they refine | [README](README.md#order-independent-vs-last-match) | `test_opencode_emits_deny_after_allow_for_last_match_wins` |
| `last-match-pi` | deny emitted last | [pi](pi.md#resolution) | `test_pi_emits_deny_last_so_it_wins_under_last_match` |
| `pi-moves-key` | Pi deletes and reinserts so an overwritten key moves to the end | [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md) | `test_pi_reorders_cross_key_entries_so_later_category_wins_position` — **unit test only; unreachable through the pipeline**, see below |
| `opencode-keeps-key` | OpenCode assigns in place, so the key stays put — deliberately unlike Pi | [ADR 0006](../decisions/0006-faithful-ports-reproduce-upstream-quirks.md) | `test_opencode_does_not_reorder_cross_key_entries` |
| `dedupe-order` | `dedupe()` is order-preserving and never `set()` | [README](README.md#order-independent-vs-last-match) | `test_dedupe_preserves_order` |
| `glob-literal` | a trailing-`*` entry is kept literal on Claude, OpenCode, Pi | [README](README.md#globs) | `test_claude_pattern_keeps_a_glob_literal`, `test_opencode_keeps_a_glob_literal`, `test_pi_keeps_a_glob_literal` |
| `glob-skipped` | Codex cannot express a glob, so it is skipped | [README](README.md#globs) | `test_codex_skips_globs_and_lists_them_at_the_end` |
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
| `module-config-collision` | two sources offering one path, or a path on a rendered destination, are refused | [module-config](module-config.md#what-loadout-does) | `test_a_path_offered_by_two_sources_is_refused`, `test_a_path_colliding_with_a_rendered_destination_is_refused` |
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
| `default-unreached` | a stated default names the targets it does not reach, at sync time | [ADR 0015](../decisions/0015-enablement-is-rendered-installation-is-reported.md) | `test_a_stated_catch_all_names_the_harnesses_it_misses` |

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

## Native artifacts

The opt-in [artifact routes](artifacts.md) share the existing output boundary. These tests live
in `tests/test_artifacts.py` and `tests/test_deployment.py`; legacy whole-document fixtures remain unchanged.

| id | behaviour | pinned by |
|---|---|---|
| `a-literal-json` | null, false, empty values and native permission order survive composition | `test_json_composition_preserves_literal_values_and_permission_order`, `test_literal_json_rejects_duplicate_keys_and_non_json_values` |
| `a-literal-toml` | nested maps and arrays preserve values and explicit top-level order | `test_toml_composition_keeps_nested_maps_arrays_and_top_level_order`, `test_toml_arrays_of_maps_preserve_key_order` |
| `a-one-owner` | explicit owners reserve dormant keys; overlaps and extra fields fail | `test_explicit_owners_collide_even_when_one_is_empty`, `test_settings_cannot_take_a_dormant_category_key`, `test_explicit_key_list_never_silently_discards_extra_fields` |
| `a-empty-slots` | empty sources stay dormant; first entries activate output; explicit empty presence survives | `test_empty_documents_are_dormant_and_first_entries_activate_them`, `test_empty_document_presence_is_explicit`, `test_empty_copy_presence_is_explicit`, `test_missing_sources_require_explicit_optional_declarations` |
| `a-rules-adapters` | portable adapters reproduce existing renderers and reject incompatible ownership | `test_permission_json_adapters_match_existing_renderers`, `test_permission_text_adapters_match_existing_renderers`, `test_incompatible_or_whole_file_renderers_are_rejected`, `test_empty_renderer_reserves_its_keys_without_creating_an_output` |
| `a-native-trees` | per-agent instructions and skill trees retain bytes and modes | `test_native_per_agent_instructions_and_skill_tree_copy_bytes_and_modes` |
| `a-authored-modes` | full opaque modes survive a real Git-index reconstruction; invalid mode/path declarations are rejected | `test_staged_source_reconstructs_full_opaque_mode`, `test_invalid_authored_modes_are_rejected` in `test_migration_corpus_regressions.py` |
| `a-migration-scaling` | sibling privacy probes are batched without crossing nested repositories or caching later decisions; cursor saves retain durable payloads; v1 recovery and corrupt-payload refusal remain supported | `test_many_siblings_share_privacy_probe`, `test_batched_privacy_keeps_nested_repositories_and_rechecks`, `test_cursor_saves_keep_payload_and_metadata_changes_replace_it`, `test_version_one_journal_still_recovers`, `test_journal_payload_corruption_blocks_recovery` in `test_migration_corpus_regressions.py` |
| `a-path-safety` | escaped paths, symlinks, overlapping routes and source/output collisions fail | `test_paths_cannot_escape_their_roots`, `test_symlinks_are_refused_before_native_deployment`, `test_declared_routes_reject_duplicate_and_ancestor_collisions`, `test_sources_and_outputs_cannot_overlap`, `test_outputs_cannot_replace_their_configuration` |
| `a-coexistence` | explicit and legacy routes cannot overwrite each other or authored inputs | `test_legacy_preset_and_artifact_cannot_claim_the_same_path`, `test_global_artifacts_collide_with_legacy_destinations`, `test_global_artifacts_cannot_overwrite_legacy_inputs`, `test_project_artifacts_cannot_overwrite_legacy_permission_sources`, `test_empty_global_and_project_routes_cannot_overlap` |
| `a-routing` | explicit project mode rejects ignored declarations; agents and source roots are validated | `test_explicit_project_mode_refuses_ignored_legacy_inputs`, `test_project_config_validates_direct_presets_and_agent_membership`, `test_global_artifacts_follow_destination_templates_and_expose_skill_agents`, `test_artifact_reference_is_relative_to_owning_config_even_when_nested` |
| `a-dependencies` | cross-scope outputs protect source/config dependencies, inherited profiles and resolved templates | `test_project_artifacts_protect_global_source_dependencies`, `test_global_artifacts_protect_project_source_dependencies`, `test_artifacts_protect_declared_template_dependencies` |
| `a-source-only` | composition ignores live output and supports a fresh source root | `test_standalone_composition_is_pure`, `test_rendering_never_uses_destination_content`, `test_artifact_configuration_can_be_reconstructed_under_a_fresh_root` |
| `a-receipt-guard` | previous/current bytes and full modes permit source changes while manual edits block | `test_source_changes_sync_against_receipt_and_current_desired_bytes`, `test_manual_output_edits_block_then_force_explicit_target`, `test_committed_baseline_does_not_override_artifact_receipt`, `test_relative_root_uses_artifact_receipts` |
| `a-adoption` | missing receipts never authorize overwriting occupied conflicting output | `test_new_occupied_path_requires_adoption_without_git_baseline`, `test_matching_existing_output_can_be_adopted_and_missing_receipt_is_conservative` |
| `a-receipt-private` | versioned private receipts stay Git-ignored; tracked, symlinked or invalid state blocks | `test_receipt_is_versioned_private_and_gitignored`, `test_bad_receipt_blocks_even_force`, `test_artifacts_cannot_consume_or_write_metadata` |
| `a-retirement` | rename/delete/last-entry retirement preserves unowned neighbors and rejects modified output | `test_rename_and_last_route_removal_retire_only_receipted_files`, `test_tree_deletion_and_last_file_cleanup`, `test_force_does_not_delete_modified_retirement`, `test_deleted_artifact_reference_still_retires_previous_outputs` |
| `a-partial-json` | runtime fields survive authored changes, last-key and whole-route removal | `test_json_partial_preserves_runtime_and_removes_authored_keys`, `test_partial_route_removal_strips_ownership_and_keeps_runtime`, `test_partial_manual_owned_changes_block_but_force_preserves_foreign` |
| `a-partial-adoption` | new key ownership must match or be absent; dormant optional parts do not create runtime files | `test_partial_first_sync_refuses_occupied_owned_keys`, `test_partial_new_owned_key_must_be_absent_or_match_source`, `test_empty_partial_source_stays_absent_then_activates`, `test_emit_empty_partial_explicitly_creates_runtime_file`, `test_optional_partial_source_removal_retires_last_owned_key` |
| `a-partial-toml` | foreign TOML bytes survive syntax-aware changes and typed fingerprints distinguish dates from strings | `test_partial_toml_preserves_exact_foreign_bytes_through_changes_and_removal`, `test_toml_syntax_aware_removal_handles_dotted_and_array_tables`, `test_toml_fingerprints_preserve_types_and_nonfinite_values`, `test_nested_toml_temporal_value_becoming_string_is_applied` |
| `a-partial-order` | authored JSON/TOML key order is applied and guarded while foreign fields and runtime-only changes survive | `test_partial_explicit_order_changes_sync_and_preserve_runtime`, `test_partial_manual_owned_order_changes_block_sync_and_retirement`, `test_interrupted_partial_order_guards_previous_and_pending_ownership` |
| `a-pending-retirement` | retirement validates every key in previous and pending ownership after an interrupted expansion | `test_interrupted_ownership_expansion_retirement_guards_every_removed_key` |
| `a-scope-lifecycle` | scopes remain isolated, profiles reuse fingerprints, and relocated roots detach without cleanup | `test_receipts_stay_scope_local_and_global_removal_keeps_project`, `test_removed_receipt_owner_cannot_collide_with_new_scope_output`, `test_profile_switch_uses_previous_deployment_without_committed_source`, `test_moving_checkout_preserves_detached_old_outputs`, `test_relocated_global_root_keeps_old_deployment` |
| `a-frozen-apply` | frozen bytes/modes and preimage checks protect application, including interrupted writes and retirement | `test_frozen_plan_copies_original_bytes_and_mode`, `test_plan_rechecks_destinations_and_receipts_before_mutation`, `test_interrupted_sync_resumes_then_accepts_a_new_source_revision`, `test_interrupted_retirement_can_finish`, `test_symlinks_never_followed_even_with_force` |

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
| `s-artifacts-excluded` | `__pycache__` and friends are not skill content, so stale bytecode is not copied to four harnesses | `test_build_artifacts_are_not_skill_content` |
| `s-exec-bit` | a copied file keeps its mode — three `scripts/` files are executable and a mode does not survive a `str` | `test_copy_preserves_the_exec_bit` |
| `s-copy-bytes` | a copied file reproduces bytes exactly, including non-text | `test_copy_reproduces_bytes_exactly` |
| `s-copy-symlink` | a copy writes *through* a destination symlink rather than replacing it | `test_copy_writes_through_a_symlink` |
| `s-mode-drift` | matching bytes with a differing mode is drift — the case a text comparison cannot see | `test_drift_when_only_the_mode_differs` |
| `s-bundle-wheel` | the wheel carries the canonical loadout skill and its reference | `test_built_wheel_contains_the_complete_skill_tree` |
| `s-bundle-resource` | source checkouts and built wheels use the same package resource tree | `test_package_resource_is_used`, `test_source_checkout_uses_the_package_resource_tree` |
| `s-install-source` | install vendors into the active profile's sole skills-capable global source, or requires `--source` rather than choosing among several | `test_one_skills_source_is_selected_by_default`, `test_multiple_skills_sources_require_an_explicit_source`, `test_profile_selects_its_own_source`, `test_unknown_profile_is_refused_before_selecting_a_source`, `test_skill_install_requires_source_when_the_manifest_has_several` |
| `s-install-owned` | an ownership hash distinguishes installed, updateable, modified and conflicting source copies; refresh never overwrites user edits | `test_an_existing_unowned_skill_is_a_conflict`, `test_install_vendors_the_bundle_as_a_normal_source_skill`, `test_install_refreshes_an_unmodified_older_bundle`, `test_install_preserves_a_modified_installed_copy`, `test_skill_install_conflict_is_reported_without_overwriting_or_syncing` |
| `s-install-rendered` | the marker stays source-only and ordinary sync deploys the skill to exactly the configured agents; no configured skill targets means install changes nothing | `test_normal_sync_deploys_the_installed_source_to_configured_agents`, `test_skill_install_vendors_into_the_global_source_and_syncs`, `test_skill_status_reports_the_source_copy_and_configured_agents`, `test_skill_install_with_no_configured_agents_does_not_choose_or_change_a_source` |
| `s-install-confirm` | mutating commands show the source destination, require confirmation or `--yes`, and a decline changes nothing | `test_skill_install_asks_before_writing_the_named_source`, `test_skill_install_requires_yes_without_interactive_input` |
| `s-uninstall-owned` | uninstall removes the owned source and unchanged rendered files, preserves unrelated files beside them, and treats an externally modified output as a whole-operation no-op | `test_uninstall_removes_owned_source_and_generated_outputs`, `test_uninstall_refuses_a_modified_generated_output`, `test_uninstall_preserves_unrelated_files_in_the_generated_skill_directory`, `test_skill_uninstall_removes_source_and_synced_outputs` |
| `s-config-contract` | one portable skill body resolves its reference, pins every artifact/scope route, and names only mappings backed by current renderers | `test_every_relative_skill_link_resolves`, `test_every_harness_receives_the_same_skill_body`, `test_capability_matrix_pins_every_scope_mapping`, `test_settings_capability_names_only_agents_whose_render_preserves_settings`, `test_project_skill_capability_names_only_agents_with_a_destination` |

## Templates

A template is a *source* referenced by name, so the slice pins two things nothing else does:
that a name resolves the same way a fragment name does one level up, and that a vendored copy is
source rather than generated output. See [templates](templates.md) and
[ADR 0014](../decisions/0014-a-vendored-template-is-source-not-output.md).

| id | behaviour | pinned by |
|---|---|---|
| `t-vendored-first` | a vendored copy stops resolution before the machine config is read — what lets a clone build without the template repo | `test_a_vendored_copy_resolves_with_no_machine_config_at_all`, `test_a_vendored_copy_wins_and_stops_resolution` |
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
| `t-bundled-priority` | named sources precede the bundle, duplicates still fail, vendored copies stop machine reads, and fallback works without machine state | `test_named_sources_and_vendoring_keep_priority_over_bundle`, `test_duplicate_named_sources_still_refuse_bundle_fallback`, `test_bundle_is_used_when_declared_sources_have_no_match` |
| `t-native-prefix` | native template tiers preserve original per-agent body bytes and modes; only explicit instruction routes receive contributions | `test_plan_freezes_starter_and_preserves_agent_bodies_and_modes`, `test_explicit_routes_leave_nested_instructions_and_commands_intact`, `test_starter_has_an_explicit_instruction_route_for_each_agent` |
| `t-native-refusal` | missing routes and populated unsupported template categories fail before add/vendor/sync or migration writes | `test_native_template_commands_refuse_missing_route_before_mutation`, `test_unsupported_template_contribution_refuses_before_migration`, `test_populated_native_template_command_preflights_before_mutation`, `test_native_template_sync_refuses_unsupported_upstream_without_changes` |
| `t-starter-private` | init refuses private, credential-bearing or symlinked overrides before promotion and rechecks changed template input | `test_starter_overrides_cannot_promote_private_or_external_source`, `test_template_changes_after_preview_block_preparation` |
| `t-starter-privacy-guards` | upstream ignore policies and effective exclude-file targets remain guarded at prepare/apply and interrupted resume, with recovery preserved | `test_starter_upstream_privacy_remains_guarded` |
| `t-starter-inventory` | additions to the template root or existing empty directories invalidate approval; a file-free root is recorded but remains unresolved | `test_starter_inventory_additions_invalidate_approval`, `test_file_free_starter_records_its_root_but_remains_unresolved` |
| `t-native-sync-symlinks` | native sync rejects vendored/upstream file and directory symlinks before reading content, including modified or missing provenance refusals | `test_native_template_sync_rejects_symlinks_before_content_reads` |
| `t-starter-transaction` | selection/provenance/source/outputs are previewed, staged, reconstructible and recoverable; default none, scope errors and cancellation leave expected state | `test_cli_starter_is_staged_reconstructs_and_allows_first_source_edits`, `test_starter_source_and_output_participate_in_resume_and_recovery`, `test_global_activation_and_unresolved_project_do_not_mutate`, `test_interactive_starter_then_cancel_never_writes` |
| `t-starter-repeat` | explicit selection on an initialized source names usable template commands; modified vendored copies retain refusal safety | `test_repeat_init_reports_usable_template_command_and_safe_sync` |

## Project scope

| id | behaviour | pinned by |
|---|---|---|
| `p-one-type` | both scopes describe a slice with one `SliceOutput` type, in two separate tables | `test_the_two_presets_agree_on_which_harnesses_exist`, `test_every_renderer_named_by_the_project_preset_exists` |
| `p-repo-relative` | a project slice sets `output` and never `destination`, so no machine path can reach a committed repo | `test_a_project_slice_is_written_relative_to_the_repo` |
| `p-one-order` | one instruction order per repo, not one per harness — three harnesses share `AGENTS.md` | `test_the_two_instruction_documents_are_byte_identical` |
| `p-instruction-order` | declared order reaches the document, below the template block and unsorted | `test_instruction_blocks_appear_in_declared_order_below_the_template` |
| `p-fixture-unsorted` | the fixture declares its fragments out of sorted order, or the ordering test would pass against a render that sorted | `test_the_project_fixture_declares_instructions_out_of_sorted_order` |
| `p-no-order-no-file` | a repo declaring no instructions generates neither document, so a permissions-only adopter keeps its own `CLAUDE.md` | `test_a_project_declaring_no_instructions_generates_neither_document` |
| `p-unknown-fragment` | an undeclared fragment name fails the render rather than rendering a short document | `test_an_unknown_instruction_fragment_fails_the_render` |
| `p-skills-per-harness` | each harness gets its own skills directory, because `render_skill` varies output by harness — the opposite answer to instructions, for a reason in the renderer signatures | `test_each_harness_gets_its_own_flavour_of_a_skill` |
| `p-skills-no-codex` | Codex gets no skills entry — verified negative, not an omission | `test_codex_gets_no_project_skills` |
| `p-skills-tier` | a project skill replaces a template's of the same name, and the template's other skills still arrive | `test_a_project_skill_beats_a_template_skill_of_the_same_name` |
| `p-skills-copied` | a supporting file is named rather than decoded, so a mode survives | `test_a_supporting_file_is_copied_rather_than_rendered` |
| `p-opencode-race` | `check` reports the skills race when neither disabling variable is set, without moving the exit code | `test_check_reports_the_opencode_skills_race_without_failing`, `test_a_project_without_opencode_is_not_told_about_its_flag` |
| `p-opencode-both-flags` | the report reads both names, since OpenCode's flag is `broad \|\| direct` — a one-name check false-alarms at whoever set the broad switch | `test_either_flag_stops_the_opencode_skills_report` |
| `p-opencode-off-is-off` | an explicit `=0` still reports, because the user has said the opposite of what silence would imply | `test_an_explicit_off_is_not_mistaken_for_on` |
| `p-claim` | two preset entries naming one path is an error at project scope as it is at global — `.agents/skills` is the edit the convention table invites and `opencode.md` forbids | `test_two_agents_may_not_share_one_skills_directory`, `test_two_agents_may_not_share_one_document` |
| `p-claim-exempts-instructions` | the shared `AGENTS.md` survives the guard, since its content is agent-independent by construction | `test_the_shared_instruction_document_is_still_allowed` |

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

Three renderers off one fragment, and each states only the half of a reference it addresses by —
so most rows here are about what a harness *cannot* say. Source for all of them is
[plugins](plugins.md).

| id | behaviour | pinned by |
|---|---|---|
| `pl-addressing` | Claude and Codex both name a plugin `<name>@<marketplace>`, arrived at independently | `test_claude_addresses_a_plugin_as_name_at_marketplace`, `test_codex_renders_enablement_and_the_marketplaces_its_plugins_reach` |
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
| `pl-x-projection` | each inverse recovers only the half its harness states | `test_claude_carries_the_marketplace_and_drops_the_source`, `test_codex_carries_the_marketplace_registration_as_well`, `test_pi_carries_the_source_and_its_filters` |
| `pl-x-off-reported` | a plugin the file marks off has no fragment representation, so it is reported rather than extracted | `test_a_plugin_switched_off_in_the_file_is_reported_not_extracted` |
| `pl-x-pi-name` | Pi's document carries no name; one is derived, a collision falls back to the source, and **every entry is reported** | `test_a_pi_name_comes_from_the_last_segment_ahead_of_the_pinned_ref`, `test_two_packages_deriving_one_name_keep_both_references`, `test_every_pi_entry_reports_the_name_it_had_to_invent` |
| `pl-x-pi-object` | `{"source": x}` with nothing filtering it renders as the string form, so the bytes change and it is reported | `test_an_object_entry_that_filters_nothing_is_reported_as_renormalised` |

`pi-plugins` is the one inverse whose document round trip closes while `notes` is non-empty —
re-rendering needs only the source, which survives exactly, but the derived name is an invention
and saying so is the point.

## MCP server definitions

One file (`servers.py`) parses `<source>/mcp.toml` and renders it through four per-harness
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
| `srv-no-secret` | `auth_env_var`'s value never reaches a rendered file, on any of the four | [ADR 0008](../decisions/0008-generated-files-carry-no-machine-state.md) | `test_no_renderer_emits_a_secret_value` |
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
| `srv-claude-staged` | Claude's global entry is staged (`output` set, `destination` unset) | [servers](servers.md#claudes-global-entry-is-staged-not-written) | `test_claude_global_is_staged_rather_than_written` |
| `srv-no-toml-no-output` | no `mcp.toml` anywhere means no servers output, at global scope too | [servers](servers.md#the-input-is-sourcemcptoml) | `test_no_mcp_toml_means_no_servers_output` |
| `srv-automatic` | `mcp` is automatic, like `permissions` — no per-agent authoring decision | [servers](servers.md#the-input-is-sourcemcptoml) | `test_a_global_source_renders_claude_servers_without_being_named` |
| `srv-codex-global` | Codex's global destination is `~/.codex/config.toml`, owning `mcp_servers` only | [servers](servers.md#eight-destinations-six-built) | `test_codex_global_writes_config_toml` |
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
| `srv-pi-global` | Pi's global destination is its own `mcp.json`, written directly | [servers](servers.md#eight-destinations-six-built) | `test_pi_global_writes_its_own_mcp_json` |
| `srv-opencode-compose` | OpenCode's global `mcp` key composes with `permission` in the same `opencode.json`, the same shape project scope already proves | [servers](servers.md#eight-destinations-six-built) | `test_opencode_global_composes_the_mcp_key_with_permission` |
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
| `srv-x-claude-global-http` | the staged global document's http server round-trips | `test_claude_global_http_server_round_trips` |
| `srv-x-claude-global-stdio` | the staged global document's stdio server round-trips | `test_claude_global_stdio_server_round_trips` |
| `srv-x-claude-global-flat` | the staged document has no `mcpServers` wrapper, unlike `.mcp.json` | `test_claude_global_document_has_no_mcpservers_wrapper` |
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
| `claude-afk-delta` | the two Claude bases differ only by `CLAUDE_AFK_TIMEOUT_MS` | A property of `~/ac`'s own base documents, not of loadout. Pinned by `test_base_drift_guard`, which is meaningless against a synthetic fixture. |
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
