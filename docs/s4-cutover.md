# S4 cutover and rollback hand-off (#86)

**Release status: `release_blocked`.** Offline technical checks and role probes
cannot approve educational quality. No real education approval record exists in
this hand-off. Operating database access, paid provider calls and deployment
were excluded from this implementation. This extends [S3](s3-cutover.md) and
[S2](s2-cutover.md); the contract is [spec #77](https://github.com/DomineYH/misconcept_platform_v2/issues/77).

## 1. Preserve the source and rehearse an isolated candidate

Obtain separate operating authorization, record the installed code revision,
schema/migration history, configuration and matching encryption master-key
version. Stop traffic and **all** writers, including detached analysis tasks,
student/mentor calls, probes, catalog refreshes and administrator edits. Store
private copies with restricted access; preserve encryption keys separately.
Use SQLite's backup API (or its `.backup` command) while the source connection
can see committed WAL pages. Copying only a live `.db` file loses WAL writes.
Never test migrations against the operating source.

The S4 expansion is **034_analysis_run.sql**. It rebuilds `generation_run`,
preserves existing child references, adds nullable plan/outcome/adoption fields
and enforces one running analysis per session. S3 owns 032/033. There are no
new S4-09 migrations; forward migration numbers 024–034 are unique. Older
pre-baseline duplicate numbers and historical down files are not new conflicts.
If a later change needs a migration, reserve 035 with the coordinator.

Point the reviewed official runner only at the isolated copy:

```bash
DATABASE_URL="${S4_CANDIDATE_URL:?Set the approved isolated candidate URL}" \
  uv run --frozen python -m src.db.migrations.migrate --through 34
```

Run it again to verify idempotence. Compare every original table/column/row and
SHA-256 of messages, classifications, reports, summaries, usage/cost, runs,
snapshots/config hashes, credentials and ACLs. Old fields must remain identical;
old runs' new plan/outcome/adoption columns must remain NULL. Check
`PRAGMA integrity_check` = `ok` and `PRAGMA foreign_key_check` = no rows.
Back up and restore the candidate again, compare its schema and all rows, and
verify v1/native/reconstructed readers, permissions and each CSV consumer.
Do not backfill v2 coverage, findings, provenance, token estimates or costs.

`tests/test_s4_cutover.py::test_s4_wal_copy_migration_restore_preserves_hashes_readers_and_acl`
rehearses committed uncheckpointed WAL → backup → restored S3 copy → 034 twice
→ restored S4 copy. It checks all original rows and hashes, actual run/message/
usage child references, NULL versus observed costs, integrity/FKs, old HTTP/CSV
and owner/admin ACLs, and confirms that the source stays unchanged.
`test_034_preserves_033_records_and_matches_fresh` additionally compares upgrade
and fresh schemas. These temporary-data tests do not certify the production
filesystem or its final stopped-writer copy; repeat against the authorized copy.

## 2. Review API and research consumers

| Contract | Response / action |
| --- | --- |
| `POST /sessions/{id}/analyze` | JSON `{request_id: <UUID>, plan_hash?: <hash>}`; authenticated owner and CSRF |
| `POST /admin/sessions/{id}/analyze_regenerate` | Same body; administrator, native ended session |
| Single / confirmed chunked request | 202 reservation with `run_id`, `actions.status`, `actions.cancel`; durable reservation precedes upstream |
| Unconfirmed chunked request | 200 `plan_required`; scope/estimates/chunks/calls/hash, no run or paid attempt |
| Confirm | Submit the displayed `plan_hash` and request ID; changed input/config/versions/limits produce 409 `plan_conflict` and a new plan, zero calls |
| Replay / concurrent request | Same request/fingerprint returns its run without generation; changed fingerprint or another active request returns 409 |
| `GET /sessions/{id}/analysis/runs/{run_id}` | 200 status and adoption/outcome; owner ACL; administrator equivalent under `/admin` |
| `POST /sessions/{id}/analysis/runs/{run_id}/cancel` | 202 explicit cancellation, CSRF; administrator equivalent under `/admin`; incurred provider cost is not refunded |
| `GET /sessions/{id}/analysis` | Accepted report and latest execution are separate; administrator equivalent under `/admin` |
| Teacher `POST /sessions/{id}/end` | Commits end, then reserves single or returns chunked/blocked plan; optional JSON analysis body. UI consumes this response and retransmits the same request ID after a lost response |
| Administrator `POST /admin/sessions/{id}/end` | Commits end, then reserves single or returns plan; optional JSON analysis body, existing HTML action still supported |

Teacher end requests without a JSON body derive a stable request ID from the
actor, session and committed end timestamp; repeated end requests replay it.
Leaving the page keeps a reserved task alive. Poll only active analysis runs and
stop after a terminal result/cancel. Restart changes unfinished runs to
`interrupted`, without rerunning. Teachers explicitly retry failed/degraded
results with a new ID; retransmission keeps the same ID. Admin regeneration may
replace ok/native results. Degraded results replace only absent/failed/degraded
reports; failed/cancelled/interrupted attempts preserve usable reports. Legacy
reconstructed sessions remain read-only; native pre-S4 sessions can regenerate
only with their frozen configuration and current role evidence.

All CSV variants append exactly five columns after the existing columns:
`analysis_schema_version`, `analysis_status`, `analysis_coverage_json`,
`misconception_findings_json`, `message_analysis_disposition`.
See [the CSV contract](s4-csv.md) for row placement, v1 unknown/empty semantics,
frozen labels, classification-off and access control. Whole-report JSON belongs
only to the summary row; disposition belongs to the corresponding teacher row.
Failure after an accepted result exports the accepted result, not latest failure.
A historical server fallback-only summary without a run has `failed` status.
Downstream research scripts must review the five-column expansion, Unicode,
quoted JSON and multiline CSV before reopening research exports.

## 3. Revalidate roles and understand planning limits

Install only the reviewed candidate with writers stopped. Use **one app instance
and one asynchronous worker**. Admission, task ownership and cancellation are
process-local; cross-worker deployments are unsupported. Start with production
configuration and retained secrets, then check health/login and historical
readers without generating calls.

Analysis contract is `s4-v2`; old analysis `s1-v1` success is stale. Student stays
`s1-v1`, mentor stays S3 `s3-v1`. Shared capability definition **v3** separately
makes all old role evidence stale, including student and mentor. Refresh the
stored capability through registration/edit/probe and explicitly reverify every
needed role; never relabel old evidence with SQL. Old reports remain readable.
The analysis probe uses at most two sequential synthetic calls: unified v2 and
evidence-subset merge, max 2500 output tokens each, stops on the first failure,
zero retries. A probe success authorizes format use, not educational release.
Paid reverification requires its own authorized scope; no startup paid retest.

The planner `utf8-v1-s4-20pct` estimates the actual serialized instruction/schema/
messages' UTF-8 bytes plus 20% framing. Output estimate is
`1200 + 160 * owned_teacher_count + 120 * owned_student_count`, plus thinking:
explicit native budget separately, automatic reasoning 50% of frozen output cap,
off/none zero. These are conservative planning estimates, **not measured tokens,
provider guarantees or prices**. Retain actual usage/cost separately and unknown
when unavailable. Real fixed-sample measurements must assess underestimation
before release. Input-only and combined context constraints differ; unknown
limits, missing output caps, smaller catalog conflicts or oversized indivisible
turn/overlap/merge block before admission. Selected model/options and frozen
caps are never raised or replaced automatically.

Single uses one structured generation. Long dialogue preserves whole turns and
one prior completed reference turn, assigns each message once, and needs explicit
confirmation. Maximum **8 sequential chunks + 1 merge**, whole-run cap **900 s**;
each call is bounded by current analysis timeout and remaining run time, including
one allowed transient retry/backoff. The retry operations are only
`analysis_unified`, `analysis_chunk`, `analysis_merge`. First non-ok chunk stops
later chunks and merge. Failed merge retains validated chunks as partial scope.
No recursive summarization, fan-out, queue, partial resumption or option fallback.

## 4. Educational release gate and actual candidate connection

Follow [S4-08 procedure](s4-quality-gate.md) and its immutable corpus/comparison
record template. `test_mock_comparison_records_same_frozen_inputs_without_approval`
now runs the isolated S2 pipeline in `tests/s2_analysis_baseline.py` against the
real S4 `analysis_pipeline.run_llm_pipeline` and
`analysis_chunks.run_chunk_pipeline`, through the existing pinned SDK mock seam.
The 12 fixed samples retain exact model/options/transcript hashes; long samples
use real plan/confirmation and 3/2/2/3 chunks. The comparison checks one single
call or the confirmed chunk+merge call count, validation and separate prompt/
schema/estimator/chunk versions. It creates temporary mock comparison records
with actual provider tokens/cost/time unknown and education approval pending.

The earlier placeholder statements in the protected #85 procedure are historical:
the executable candidate is connected by #83. They must not be taken as a paid
comparison result or approval. #86 preserves the #85 corpus/gate files.
S2 execution classes, permissive contracts and legacy normalizer are isolated
under `tests/s2_*`; no product fallback switch or legacy runtime caller remains.
Old prompt assets are retained for the protected baseline metadata reader.
The offline baseline shares current admission/ledger infrastructure; actual
baseline quality evaluation must use the recorded S2 code revision in a separate
synthetic environment, and record both exact code revisions.

Human gates, all still **open**:

1. Product owner (or explicitly designated education lead) reviews and freezes
   human expected evidence/allowed/forbidden interpretations and hashes before
   execution. Current expectations are agent drafts, not human approval.
2. A separate paid budget authorization records exact pilot model/options,
   samples/call bounds, currency/max budget, approver and date.
3. Actual isolated baseline/candidate execution records outputs, validation,
   calls/time, actual tokens/cost or unknown, code/prompt/schema/estimator/chunk
   policy and corpus hashes for **all 12 samples per intended configuration**.
4. Education lead signs the actual review: zero normally adopted bad references,
   zero major unsupported claims; each applicable evidence/classification/finding/
   feedback score at least 4/5; per-sample common-dimension drop at most 1 against
   baseline. N/A needs a reason and is not a zero; one failure blocks approval.
5. Attach approver/date/decision/record hash, redo affected approval on prompt,
   schema, estimator, chunk policy or intended model/options change, and obtain
   separate production access/cutover authorization.

No item is inferred complete from CI, a role probe or advance design approval.
Keep `release_blocked` until all applicable records and authorizations exist.

## 5. Rollback and write boundaries

Before any S4 write, close traffic/stop writers, retain the failed candidate,
restore the original consistent S3 backup with SQLite backup API, verify original
schema/migration history/rows/hashes/ACLs and start recorded S3 code/configuration
with matching keys. Never run old code against the S4 candidate or manually drop
its columns/constraints. No supported down migration is supplied.

After any S4 write, stop writers and back up the current S4 database consistently
first. Probe/ledger writes, role revalidation and administrator edits already
count; record the first new write and reopening time. Preserve new sessions,
messages, analyses, runs, attempts/cost, configuration/keys and permissions.
Prefer forward repair. An adjusted restore needs explicit reconciliation and
verification of every newer record. Restoring the S3 backup alone discards new
research data; a lossless downgrade is not promised. Educational release remains
blocked even when the technical candidate and recovery checks pass.

## 6. Deferred-item reconciliation

Coordinator `deferred.md` reviewed for this hand-off:

| Item | Current disposition |
| --- | --- |
| #85 candidate placeholder / synthetic planner budget | Executable candidate/real planner connected by #83; old protected procedure prose remains; real measurements/education review still open |
| #79 teacher degraded retry / merge caller / baseline dependency | Retry in #81, merge in #83; runtime dependency isolated in #86; no fallback switch |
| #80 request_id JSON contract | Explicitly documented above and in README; downstream callers must migrate |
| #78 evidence null checks / optional latest status | Still open hardening notes; validated S4 results exercised, unrelated malformed-screen cleanup excluded |
| #78 sessions.js sanitizeHTML event attributes | Pre-existing issue remains open; S4 generated text uses its existing safe renderer |
| #80 cross-worker reservation IntegrityError → 500 | Still open; one-worker topology required, no multi-worker claim |
| #80 superseded accepted_report_id without FK / unreachable preserve branch | Existing design/dead-path notes remain; all-report revision storage excluded |
| #81 legacy preservation wording “이전 정상 결과” | Still open wording distinction, usable legacy payload preserved |
| #84 fallback-only CSV unknown wording / 390px admin table overflow | CSV wording clarified in s4-csv.md; pre-existing table overflow still open |
| #82 catalog mismatch blocks even larger capacities | Existing conservative behavior, no smaller-only guarantee |

## 7. Spec acceptance traceability

Identifiers Dn.m follow each normative bullet in #77; D2-envelope covers its
required-field table. Test names below refer to committed tests, not evidence of
real educational quality. HG1–HG5 refer to the open human gates in §4; OG means
a separately authorized final operating-copy/deployment/recovery check. Check
execution totals belong to the final #86 report. Every row remains subject to
`release_blocked` for production.

| Spec criterion | Evidence / human gate |
| --- | --- |
| D1.1 native/frozen/current authority, historical reading separate | `test_analysis_checks_current_authority_and_native_provenance`; `test_unadoptable_run_keeps_no_report_and_never_reexecutes[revoke-baseline]`; `test_s4_concurrent_regeneration_interruption_preserves_report` |
| D1.2 full ordered teacher/student input, no mentor/reasoning/window truncation | `test_analysis_uses_frozen_inputs_and_one_model_option_set`; `test_analysis_ignores_recent_turn_window_and_excludes_reasoning_and_partial_generation` |
| D1.3 unanswered/unlinked ownership and response_missing | `test_unmatched_messages_attach_without_inventing_turn_links`; `test_single_analysis_saves_v2_evidence_and_server_statistics` |
| D1.4 no_dialogue/teacher-only/greeting-only | `test_dialogue_boundaries_have_honest_coverage_and_call_count` |
| D1.5 classification off retains feedback/findings | `test_classification_off_keeps_feedback_and_explicit_display`; `test_eight_confirmed_chunks_make_nine_calls_with_classification_on_or_off` |
| D2-envelope required v2 fields/types, extra fields forbidden | `test_damaged_envelope_has_no_usable_report`; `test_unified_contract_accepts_complete_teacher_only_feedback` |
| D2.1 Unicode/text/array limits, no bool/coercion/NaN/truncation | `test_unicode_text_limits_preserve_exact_quotes_and_do_not_clamp`; `test_section_limit_invalidates_whole_section_without_truncation`; `test_invalid_classification_never_becomes_a_normal_result` |
| D2.2 rubric/disposition/null confidence/server level | `test_rubric_ids_control_storage_display_and_levels`; `test_single_analysis_saves_v2_evidence_and_server_statistics` |
| D2.3 greeting/mixed/unknown/off semantic meaning | `test_dialogue_boundaries_have_honest_coverage_and_call_count`; `test_classification_off_discards_unrequested_rubric_output_as_a_violation`; HG1/HG4 for educational dispositions |
| D2.4 finding kinds/student/ordered changed evidence, no causal score | `test_changed_finding_requires_distinct_ordered_student_evidence`; `test_invalid_evidence_is_rejected_but_usable_feedback_survives`; HG1/HG4 |
| D2.5 empty supported sections, educational validity separate | `test_unified_contract_accepts_complete_teacher_only_feedback`; HG4 |
| D3.1 termination/envelope failure, no JSON repair | `test_analysis_records_every_failure_and_only_safe_retries`; `test_damaged_envelope_has_no_usable_report`; `test_runtime_rejects_legacy_repairs_and_nullable_envelope` |
| D3.2 exact references/quotes/roles/duplicates/section invalidation | `test_all_duplicate_classifications_are_invalidated_without_losing_other_ids`; `test_boundary_evidence_is_owned_or_declared_overlap_for_change_only`; `test_section_limit_invalidates_whole_section_without_truncation` |
| D3.3 valid-only partial, failed core, honest ok boundaries | `test_partial_reference_failure_keeps_only_valid_items_and_fails_ledger_validation`; `test_invalid_feedback_item_does_not_discard_other_valid_feedback`; `test_first_non_ok_chunk_stops_calls_and_preserves_only_validated_range` |
| D3.4 server metadata/ranges/errors/versions/public privacy | `test_single_call_records_estimator_separately_from_actual_usage`; `test_confirmed_chunks_merge_once_with_unique_ordered_classifications_and_ledger`; `browser_s4_privacy.mjs` |
| D3.5 valid denominator/zero/coverage/off | `test_partial_coverage_and_distribution_use_only_valid_classifications`; `test_dialogue_boundaries_have_honest_coverage_and_call_count`; `browser_s4_statuses.mjs` |
| D3.6 failed semantic ledger versus usable partial | `test_partial_reference_failure_keeps_only_valid_items_and_fails_ledger_validation`; `test_semantically_failed_result_is_a_failed_ledger_attempt` |
| D4.1 atomic classifications/report/summary/run | `test_storage_failure_never_adopts_or_reports_completion`; `test_partial_adoption_preserves_usable_reports_and_finalizes_run` |
| D4.2 durable run fields/unique actor request/one running | `test_analysis_constraints_keep_student_states_and_one_active_run`; `test_s4_start_to_reader_csv_and_replay` |
| D4.3 fingerprint/replay/concurrency/superseded | `test_changed_input_replay_conflicts_and_old_run_is_superseded`; `test_s4_concurrent_regeneration_interruption_preserves_report` |
| D4.4 teacher retry/admin regenerate/auth/CSRF | `test_teacher_retries_partial_as_new_request_and_preserves_it_on_failure`; `test_s4_start_to_reader_csv_and_replay`; `test_run_lookup_and_cancellation_keep_owner_and_admin_boundaries` |
| D4.5 reserve then recheck/adopt/preserve, no fake success | `test_s4_concurrent_regeneration_interruption_preserves_report`; `test_chunk_adoption_and_interruptions_preserve_accepted_report`; `test_storage_failure_never_adopts_or_reports_completion` |
| D4.6 detached 202/cancel/deadline/restart/no resume | `test_unadoptable_run_keeps_no_report_and_never_reexecutes`; `test_run_deadline_bounds_provider_wait_and_does_not_restart`; `browser_s4_lifecycle.mjs` |
| D4.7 analyze/plan/status/cancel/end response contracts | `test_end_commits_then_reserves_and_replays_same_run`; `test_s4_start_to_reader_csv_and_replay`; `test_confirmation_rechecks_current_plan_and_permission_with_zero_calls`; `test_run_lookup_and_cancellation_keep_owner_and_admin_boundaries` |
| D4.8 run-linked attempts and three-operation retry allowlist | `test_confirmed_chunks_merge_once_with_unique_ordered_classifications_and_ledger`; `test_transient_retry_is_a_separate_attempt_within_one_chunk_or_merge`; `test_removed_s2_operations_are_not_runtime_retry_paths`; `test_s4_retry_allowlist_is_limited_to_runtime_analysis` |
| D4.9 migration numbering/old data/no backfill | `test_s4_wal_copy_migration_restore_preserves_hashes_readers_and_acl`; `test_034_preserves_033_records_and_matches_fresh` |
| D5.1 official limits/catalog conflict/unknown | `test_shared_limits_record_official_source_and_check_date`; `test_smaller_openai_catalog_capacity_blocks_but_unknown_stays_unknown`; provider capability fixtures |
| D5.2 serialized UTF-8+20%, no paid count | `test_exact_input_budget_fits_and_overflow_is_never_truncated`; `test_single_call_records_estimator_separately_from_actual_usage`; HG3 for real estimator calibration |
| D5.3 output/thinking formula/version | `test_single_plan_records_frozen_cap_formula_and_version`; `test_native_thinking_reservation_and_frozen_options`; HG3 |
| D5.4 all input/context/output caps, no substitution | `test_unknown_and_oversized_units_are_blocked_with_zero_calls`; `test_analysis_uses_frozen_inputs_and_one_model_option_set` |
| D5.5 plan scope/estimate/chunks/calls/retry/hash/unknown price | `test_plan_is_readable_without_reserving_or_calling_before_confirmation`; `browser_s4_plan.mjs` |
| D5.6 both end flows/confirm/recheck/ended retained | `test_end_commits_then_reserves_and_replays_same_run`; `test_s4_start_to_reader_csv_and_replay`; `test_ending_session_waits_for_chunk_confirmation`; `test_confirmation_rechecks_current_plan_and_permission_with_zero_calls` |
| D6.1 greedy whole turns/unique ownership/one overlap | `test_chunked_plan_keeps_whole_turns_unique_ownership_and_one_turn_overlap`; `test_unmatched_messages_attach_without_inventing_turn_links` |
| D6.2 8+1 sequential / 900s including retry/current timeout | `test_eight_confirmed_chunks_make_nine_calls_with_classification_on_or_off`; `test_remaining_run_deadline_caps_each_call_and_prevents_adoption`; `test_analysis_does_not_retry_when_backoff_exceeds_remaining_deadline` |
| D6.3 oversized unit/8+ blocked/evidence-only merge | `test_unexecutable_plans_reject_with_zero_provider_calls`; `test_confirmed_chunks_merge_once_with_unique_ordered_classifications_and_ledger` |
| D6.4 conservative max merge and actual serialization preflight | `test_large_merge_is_blocked_before_any_chunk_call`; `test_executor_checks_real_merge_serialization_before_admission` |
| D6.5 owned classifications/evidence/dedup/conflicts | `test_boundary_evidence_is_owned_or_declared_overlap_for_change_only`; `test_identical_observations_are_deduplicated_and_conflicting_kinds_remain` |
| D6.6 one merge/no classifications/new evidence/subset | `test_merge_cannot_create_new_quotes_even_when_they_are_verbatim`; `test_merge_failure_keeps_verified_chunks_as_partial_analysis`; `test_confirmed_chunks_merge_once_with_unique_ordered_classifications_and_ledger` |
| D6.7 first non-ok stops, merge failed retains partial, ok preserved | `test_first_non_ok_chunk_stops_calls_and_preserves_only_validated_range`; `test_merge_failure_keeps_verified_chunks_as_partial_analysis`; `test_chunk_adoption_and_interruptions_preserve_accepted_report` |
| D6.8 no automatic resize/replan/option mutation | `test_analysis_records_every_failure_and_only_safe_retries`; `test_first_non_ok_chunk_stops_calls_and_preserves_only_validated_range`; `test_analysis_uses_frozen_inputs_and_one_model_option_set` |
| D7.1 pinned provider strict structured transports | `test_analysis_subcalls_use_selected_provider_options_and_capacity`; `test_claude_structured_role_probe_validates_existing_contract`; `test_structured_google_probe_uses_server_validation_and_stops_on_failure` |
| D7.2 strict schema/server semantics, no JSON downgrade | `test_recursive_schema_is_rejected_before_sdk_construction`; `test_unsupported_structured_schema_is_rejected_before_sdk`; `test_invalid_classification_never_becomes_a_normal_result` |
| D7.3 safe provider termination/error fixtures | `test_nonstream_never_promotes_refusal_incomplete_or_empty`; `test_claude_failures_stop_bundle_preserve_usage_and_hide_bodies`; `test_google_failure_is_safe_and_never_retries` |
| D7.4 s4-v2 stale/read/reverify/S3 mentor/shared v3 | `test_s4_legacy_reader_survives_stale_roles_then_explicit_probe_allows_v2`; `test_shared_definition_change_stales_every_role` |
| D7.5 unified+merge synthetic two-step runtime validator/zero retry | `test_analysis_probe_validates_existing_classification_and_synthesis`; `test_role_failure_stops_bundle_and_records_safe_error`; `test_s4_legacy_reader_survives_stale_roles_then_explicit_probe_allows_v2`; HG2 for actual probes |
| D8.1 shared projection/stable IDs/evidence/keyboard/XSS/privacy | `test_saved_results_use_shared_projection_on_every_http_surface_and_keep_permissions`; `browser_s4_analysis.mjs`; `browser_s4_privacy.mjs`; OG for final production connection |
| D8.2 accepted/latest state/coverage categories/no empty success | `test_s4_concurrent_regeneration_interruption_preserves_report`; `test_partial_coverage_and_distribution_use_only_valid_classifications`; `browser_s4_statuses.mjs`; `browser_s4_records.mjs` |
| D8.3 retry IDs/replay/poll/cancel/cost notice | `browser_s4_lifecycle.mjs`; `test_teacher_retries_partial_as_new_request_and_preserves_it_on_failure`; `test_s4_concurrent_regeneration_interruption_preserves_report` |
| D8.4 v1/summary unchanged/unknown/reconstructed read-only/native retry | `test_historical_results_share_projection_without_inventing_coverage`; `test_native_v1_partial_result_keeps_existing_teacher_retry_permission`; `test_s4_wal_copy_migration_restore_preserves_hashes_readers_and_acl` |
| D8.5 all CSV rows/columns/frozen labels/summary JSON/disposition/ACL | `test_all_csv_variants_append_exact_columns_and_place_json_on_summary_only`; `test_csv_json_and_multiline_cells_round_trip_across_bulk_sessions`; `test_s4_start_to_reader_csv_and_replay`; OG downstream script review |
| D9.1 WAL/restore/hashes/FK/permissions/CSV/no operating access | `test_s4_wal_copy_migration_restore_preserves_hashes_readers_and_acl`; OG final authorized copy |
| D9.2 isolated S2 baseline, removed reachable legacy runtime | `test_s4_runtime_excludes_s2_execution_and_normalization`; `test_mock_comparison_records_same_frozen_inputs_without_approval`; HG3 |
| D9.3 single worker/no queue/rewrite/revisions/model selection/options UI | §3 topology; source runtime guard and scope review; OG deployment topology |
| D10.1 fixed 12 Korean math/science/off/length/boundary samples | `test_fixed_corpus_covers_d10_and_has_no_human_approval`; `test_long_corpus_has_real_bounded_chunk_plans_without_padding` |
| D10.2 frozen transcript/config/hash/human expectations/synthetic privacy | `test_changed_transcript_is_rejected_before_execution`; `test_invalid_draft_evidence_is_rejected_even_with_updated_hash`; HG1 still open |
| D10.3 same exact model/options/inputs and findings vs human evidence | `test_mock_comparison_records_same_frozen_inputs_without_approval`; `test_mock_comparison_rejects_model_or_option_substitution`; HG1/HG3/HG4 |
| D10.4 zero bad refs/claims, every score ≥4, drop ≤1/N/A reasons | [Quality gate](s4-quality-gate.md) and record template; HG4 still open, mock scores never evidence |
| D10.5 complete version/output/usage/approver record and invalidation | `test_mock_record_keeps_quality_scores_and_approval_pending`; `test_mock_comparison_records_same_frozen_inputs_without_approval`; HG3/HG4/HG5 |
| D10.6 separate paid authority/actual education approval/release blocked | §4 HG1–HG5 open; `test_mock_record_keeps_quality_scores_and_approval_pending` |
| Testing.1 approved HTTP/service/temp SQLite/SDK + small pure seams | `test_s4_start_to_reader_csv_and_replay`; `test_s4_legacy_reader_survives_stale_roles_then_explicit_probe_allows_v2`; plan/contract tests |
| Testing.2 extend failure/snapshot/provider/migration/browser with v1/v2 split | `test_s4_concurrent_regeneration_interruption_preserves_report`; `test_s4_wal_copy_migration_restore_preserves_hashes_readers_and_acl`; `test_historical_results_share_projection_without_inventing_coverage` |
| Testing.3 single/boundaries/off/invalid envelope/evidence/zero denominator | D1–D3 test rows above |
| Testing.4 chunks/overlap/caps/zero calls/hash/options/merge subset/failure | D5–D6 test rows above |
| Testing.5 replay/concurrency/late result/permission/cancel/restart/storage/preservation/ledger/deadline | D4 test rows above; `test_analysis_total_timeout_is_finalized_without_retry`; `test_cancel_during_analysis_backoff_does_not_create_another_attempt` |
| Testing.6 mock then connected browser/evidence/keyboard/coverage/confirm/retry/history/CSV | `browser_s4_analysis.mjs`, `browser_s4_lifecycle.mjs`, `browser_s4_plan.mjs`, `browser_s4_privacy.mjs`, `browser_s4_records.mjs`, `browser_native_analysis.mjs`; OG actual operating connection, no paid calls in CI |
| Testing.7 targeted development/final full checks/no paid or operating DB | Final #86 report: full pytest, ruff, black, browser, localhost live once after coordinator branch merge; HG2/HG5 excluded |

#77 User Stories 1–22 map respectively to D1–D10 above: 1→D5.5;
2→D2.3/D3.5; 3–4→D1.3; 5→D8.1; 6→D2-envelope; 7→D1.5;
8→D3.3/D3.5; 9→D5.6; 10→D6.7; 11→D4.4; 12→D4.3;
13→D4.5; 14→D3.2; 15→D7.3; 16→D7.4–5; 17→D8.4;
18→D8.5; 19→D1.1/D8.1; 20→D10.3/HG1–4;
21→D10.6/HG4–5; 22→D5.2/D10.5/HG3.
