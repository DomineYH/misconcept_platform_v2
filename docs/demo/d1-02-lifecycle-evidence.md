# D1-02 execution lifecycle evidence (#192)

Contract: `d0-03-v1-r3`, G0 approved 2026-10-19. Input base:
`427890033d206a4f043ffc946009bd8ce6a437c2` (confirmed ancestor of HEAD).
The implementation is on `feat/192-demo-lifecycle`; commits and final check
counts are recorded in the worker report. No #191 branch was imported.

## Public boundary and ownership

`src/demo/lifecycle.py` exports `ExecutionScope`, `ExecutionKey`, `TaskControl`,
`ExecutionToken`, `NormalContext`, `NORMAL_SCOPE`, `Lifecycle`, and a normal
singleton `lifecycle`. Module functions delegate to that singleton:

- `register(ctx, kind, id, task)` / `pop(scope, kind, id, token)` are async.
- `lookup(scope, kind, id)`, `cancel(scope, kind, id)`, `cancel_scope(scope)`,
  and `busy_locked(handle, scope)` are synchronous.
- `busy(scope, kind=None, id=None)`, `drain(scope, timeout_seconds)`, and
  `assert_current(ctx)` are async.
- `resource_lock(scope, kind, id)`, `write_guard(ctx)`, and
  `Lifecycle.child_context(ctx)` are async context managers.

Demo integration injects `Lifecycle(visitors, lease_count=counter)`.
`VisitorLifecycle` and `TransitionHandle` are typing protocols: transition,
check, retain_lease, release_lease, and write_guard belong exclusively to #191.
The counter receives `(held_handle, scope, execution_only)` and must read #191's
accounting, including retired scopes and independent request/child/shield leases.
It must count retained execution children for role busy checks, excluding plain
request leases. There is no local visitor state, lease allocator, TTL store,
factory fallback, or production double. Missing demo authority fails closed.

#191 binding still needs a supported scoped lease-count projection and the adapter
from server-owned visitor identity to its transition reference. Contexts passed
to the owned background launchers must support `dataclasses.replace(ctx, lease=...)`;
all other context fields, particularly factory/generation/auth_epoch, stay fixed.
The final source candidate is `ea2ba63` (full SHA in the report).

`child_context` retains an execution lease across analysis reservation and task
registration, so a role change cannot leave an ownerless committed running row.
Task completion callbacks retain registry ownership until pre-start cancellation
cleanup, producer cleanup, and owned final writes have ended. Cancellation is
idempotent and never sets done. Pop checks identity under the transition before
releasing once; retired-scope cleanup does not check currentness.

Drain bounds both transition acquisition and completion waits. On timeout while
transition is unavailable, remaining is a conservative positive count; it never
reports a zero/complete result without observing all leases. Timeout preserves
tokens and files. Settings validation and passing DEMO_DRAIN_SECONDS (5, 1..30)
belong to #194; zero-second checks in unit tests are immediate observations.

## Consumer migration

| Consumer | Implemented boundary | Remaining owner |
|---|---|---|
| generation_lifecycle | canonical registry normal view; guarded orphan writes | #198 stream ownership, #194 startup |
| analysis_runs | scoped reservation/launch/cancel; guarded reservation, own success/failure writes; cleanup-before-pop | #198 pipeline/chunk/ledger propagation |
| probe_execution/probe_lifecycle | scoped launch; rejected permit release; pre-start cleanup; guarded terminal/orphan writes | #200 route rejection; #198 admission |
| session_helpers | guarded commit/rollback, then scoped cancellation; end commit survives analysis failure | #198/#200 pass context |
| comparison stream | normal synchronous constructor retained; response owns done/pop after child/recovery/send cleanup | #200 forbids demo execution |
| comparison stop/history/recovery | scope-aware token cancellation/lookup; SQL receives resource IDs only; synchronous SQL builder retained | #200 route policy/context |
| cleanup CLI | demo dry-run/execute rejected before parsing/opening DB | #194 startup mode wiring |

The standalone CLI snapshots process APP_MODE at entry, validates normal/demo,
and explicitly passes it to `run(..., app_mode=...)`. Config has no mode field at
this base; config.py was not changed. Empty/unknown mode exits 1 with
DEMO_CONFIGURATION_INVALID. Demo exits 1 with DEMO_ACTION_FORBIDDEN.

Normal-only compatibility views retain Event/Task values for unmigrated callers
(student_stream, mentor_stream, admin_users, admin_probes, etc.) using the same
canonical token registry. No demo lookup falls back to normal. Missing context/namespace/lease/epoch is
DEMO_INVALID_REQUEST422, rather than an attribute error. The single
`shortcut:` comment fixes removal at #198/#200 migration. Existing synchronous
start_analysis/start_probe/response constructors and SQL builders remain usable.
Comparisons remain normal-only; no demo comparison execution was added.

## Acceptance map

Tests below are in `tests/test_demo_d1_02_lifecycle.py` unless otherwise stated.
Visitor/lease/role/reset state is a local test double, never real #191 evidence.

| Criteria | Unit / regression evidence | Scope and limits |
|---|---|---|
| U01 | namespace_identity_and_retired_cleanup; reset_cancel_does_not_touch_same_id_in_other_visit_or_generation; invalid_scope_is_rejected; missing_demo_context_fields_are_invalid_requests; resource_reservations_are_scoped_and_serialized | all six kinds, int/string UUID/UUID, A/B and g/g+1 |
| U02/U11 | registration_retains_execution_after_request_release; prestart_cancel_waits_for_callback_cleanup | Event cancellation does not complete; actual analysis/probe completion callback barriers |
| U03 | concurrent_registration_has_one_token_and_one_child_lease; register_rolls_back_retained_lease; authority_errors_preserved_and_no_lease_retained | one token/one child, duplicate adds zero, failed registration rolls back |
| U04 | namespace_identity_and_retired_cleanup | unfinished pop BUSY, identity, duplicate pop, retired cleanup |
| U05 | register_and_role_change_use_same_transition; analysis_reservation_keeps_execution_lease_until_task_handoff | busy/current same-role check and cancelled_analysis_launch_cleans_reservation_under_retained_child via double; real role policy/CSRF #193 |
| U06 | cancel_is_idempotent_without_completion; reset_cancel_does_not_touch_same_id_in_other_visit_or_generation | exact target counts and no early completion |
| U07/U10 | request_child_and_shield_leases_prevent_drain; child_context_changes_only_lease_and_releases_after_retirement; child_release_finishes_under_repeated_cancellation_and_contended_transition | request/child/shield counts and captured factory/epoch |
| U08 | resource_reservations_are_scoped_and_serialized; drain_waits_without_holding_transition; drain_timeout_includes_waiting_for_transition | independent resources and bounded wait |
| U09/U12 | write_guard_serializes_commit_with_reset_and_role; late_owned_callbacks_never_open_factory; authority_errors_preserved_and_no_lease_retained | owned callback guard rejects before factory; real SQL/reset matrix #191/#194/#198 |
| U13/U14 | reset_handoff_releases_own_request_but_not_other_live_request | consumes retired scope/early release; real mark/handoff/rollback/receipt/disconnect #193/#194 |
| U15/U16/U17 | authority_errors_preserved_and_no_lease_retained; namespace_identity_and_retired_cleanup; drain_timeout_includes_waiting_for_transition; existing orphan/probe regressions | authority error and release semantics; actual TTL/shutdown/budget #191/#194 |
| U18 | authority_errors_preserved_and_no_lease_retained; invalid_scope_is_rejected; namespace_identity_and_retired_cleanup | lifecycle BUSY409/INVALID422; authority errors propagated; HTTP/SSE terminal mapping #198/#201 |
| U19 | probe/comparison/analysis/student/mentor/transaction regressions; normal_never_calls_demo_authority; legacy_view_is_normal_only_and_shares_token_registry | normal factory, guards, cancellation, reservations, analysis retention |
| U20 | existing test_analysis_runs.py, test_analysis_concurrency.py and chunk lifecycle regressions | durable replay/body conflict unchanged; full demo student/mentor retry #198 G3 |
| U21 | existing test_transactions.py, test_student_lifecycle.py, test_mentor_lifecycle.py, test_deleted_session_access.py | helper preserves end-before-analysis; real demo URL/CSV #200/#203 |
| U22 | demo_cleanup_cli_rejects_before_database_open; demo_cli_process_rejects_without_importing_application_database; cleanup_cli_invalid_mode_fails_closed; existing test_comparison_retention.py | engine-open zero; normal ro/rw/count/JSON tests retained |
| U23 | allowlisted diff; guarded demo error logging; no production stubs | no visitor metrics added; other-owner requests below |
| U24 | gate table below | unit evidence never promotes integration gates |

## Execution record

Environment: Python 3.12.3, frozen uv dependencies, isolated pytest DBs with
outbound sockets blocked by existing conftest. No paid calls, production DB,
.env edits, browser, migration of real data, or network AI execution.

- Setup: `uv sync --frozen --extra dev --python 3.12` succeeded.
- Red: lifecycle idempotent-cancel test failed (1 failed, 1 passed); repaired by
  recording cancellation once on the immutable token's private state.
- Initial unit run: 50 passed; later unit runs: 75 and 79 passed.
- Owned three-file regression: 77, then 100, then final 106 passed (17 warnings).
  The final missing-context delta separately passed 4 checks after a red run
  (2 failed, 2 passed). Full suite below includes those four cases.
- Intermediate targeted run was interrupted after a failure and stuck shutdown
  teardown. Its stack identified stop_probes spinning on a completed cleanup
  whose done callback had not run. Shutdown now explicitly consumes gathered
  cleanup tasks; two regression cases cover this for analysis and probe.
- Slow mounted-drive runs were replaced with the same frozen dependencies in
  `/tmp/w192-demo-lifecycle-venv` using UV_PROJECT_ENVIRONMENT. Subsequent commands
  use that environment variable; dependency versions and test scope are unchanged.
- Four preliminary full-suite launches were interrupted for boundary repairs or
  environment replacement; none produced a completed full-suite result.
- Targeted impact matrix (all twelve named files exist): 108 passed, 66 warnings.
- Final full suite at source candidate `ea2ba633e454d4cae4e00b1881da14c6af9b2e8d`:
  `uv run --frozen python -m pytest -q` → **2085 passed**, 1340 warnings,
  2661.74 seconds. No failures or unrelated-test reruns.
- Final missing-context delta: 4 passed; the full suite includes all 85 dedicated
  lifecycle cases (110 combined with the unchanged probe/comparison regressions).
- Ruff: `uv run --frozen ruff check .` passed. Black:
  `uv run --frozen black --check .` passed (355 files unchanged).
- Browser `npm run test:browser`: not run (CI).
- Live `uv run --frozen python tests/check_student_live.py`: not run (CI).

## Gates and handoff

| Gate | Status | Owner / required follow-up |
|---|---|---|
| #192 unit/normal regression | passed: 2085 full; 106 owned + 4 delta; 108 impact | this branch, local doubles only |
| #191 real binding | not run; dependency unmerged | bind real check/guard/retain/release/scoped counts, rerun same matrix |
| #194 G1 | not run | inject lifecycle into resolver/middleware/lifespan; no normal startup orphan scan in demo; configure drain timeout |
| #198 G3 | not run | pass ctx/registry through call admission, student/mentor ASGI cleanup, pipeline/chunk/ledger/shield, all final DML; scope cancel_connection |
| #193 reset/role | not run | same-handle busy_locked; mark then handoff then release_request_db then cancel_scope/drain; retain receipt Task |
| #200 guards/admin | not run | forbid demo comparison/probe direct calls; migrate admin registry consumers and pass fixed context |
| #203/#204/G5 | not run | independent security/browser on integrated fixed candidate |

Skipped/unverified: real #191 binding, G1/G3/G5, browser/live checks (CI).
Risk: this branch supplies lifecycle boundaries; it does not yet make the whole
application safe to serve in demo mode.
