# D1-02 execution lifecycle evidence (#192)

Contract: `d0-03-v1-r3`, G0 approved 2026-10-19. Input base:
`427890033d206a4f043ffc946009bd8ce6a437c2` (confirmed ancestor of HEAD).
The implementation is on `feat/192-demo-lifecycle`; commits and final check
counts are recorded in the worker report. Current #191 integration target:
`12f8bf57d9e763c19022d41da00173df96108cbf`, merged without conflicts as
`9b21aec`; main `bca69cc` documentation is merged as `62d60a6`.
Reviewed main `0115b8c` is included via merge `dab9887`.
The earlier #191 target `79d2925` was merged as `d9fd026` before the restart.
#191-owned files match the current target exactly. The prepared merges were
fast-forwarded into the requested worktree after full access was restored.
The merged source candidate checked is `62d60a6`.

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

Demo integration wires the same objects in both directions:
`registry = Lifecycle(); visitors = VisitorRegistry(root, lifecycle=registry);`
`registry.visitors = visitors`. The concrete #191 types replace the earlier local
protocols. `transition(ctx.verified_visitor)` receives the issued capability;
registration captures it and a distinct #191 lease on the token. Retain/check/
release under a handle use synchronous handle methods without outer re-entry.
Pop uses the token's captured capability, including after role/reset retirement.
Independent child release uses `await visitors.release_lease(lease)`.
All demo errors are the shared #191 `DemoError`, including consumer propagation.

#191 exclusively allocates, validates and counts leases. Drain uses its public
`lease_count(scope)` and `wait_for_leases(scope, timeout)`, including request,
registry and independent child/shield leases. Execution busy state consists of
unremoved tokens plus live lease references retained by `Lifecycle.child_context`.
Plain request leases do not imply busy. Every independent execution/shield child
must use `child_context` for this classification; direct generic #191 retains
are counted by drain but have no execution-purpose marker. #198 must route its
children through this boundary. The reference set is not a second allocator or
lease counter. No private #191 state, TTL store, factory fallback or production
double is used; missing authority fails closed.

Contexts passed to the background launchers support
`dataclasses.replace(ctx, lease=...)`; all other fields, particularly
factory/generation/auth_epoch, stay fixed.

`child_context` retains an execution lease across analysis reservation and task
registration, so a role change cannot leave an ownerless committed running row.
Task completion callbacks retain registry ownership until pre-start cancellation
cleanup, producer cleanup, and owned final writes have ended. Cancellation is
idempotent and never sets done. Pop checks identity under the transition before
releasing once; retired-scope cleanup does not check currentness.

Drain bounds transition acquisition and completion waits, reaps completed tokens
between bounded #191 waits, and reads the public synchronous lease count again
on timeout. A contended pop remains owned until its token and lease are released. Timeout preserves
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
The original matrix below used local doubles. The Binding B section names the
real #191 matrix and supersedes its earlier integration gaps.

| Criteria | Unit / regression evidence | Scope and limits |
|---|---|---|
| U01 | namespace_identity_and_retired_cleanup; reset_cancel_does_not_touch_same_id_in_other_visit_or_generation; invalid_scope_is_rejected; missing_demo_context_fields_are_invalid_requests; invalid_demo_namespace_never_enters_normal_scope; resource_reservations_are_scoped_and_serialized | all six kinds, int/string UUID/UUID, A/B and g/g+1 |
| U02/U11 | registration_retains_execution_after_request_release; prestart_cancel_waits_for_callback_cleanup | Event cancellation does not complete; actual analysis/probe completion callback barriers |
| U03 | concurrent_registration_has_one_token_and_one_child_lease; register_rolls_back_retained_lease; authority_errors_preserved_and_no_lease_retained | one token/one child, duplicate adds zero, failed registration rolls back |
| U04 | namespace_identity_and_retired_cleanup | unfinished pop BUSY, identity, duplicate pop, retired cleanup |
| U05 | register_and_role_change_use_same_transition; analysis_reservation_keeps_execution_lease_until_task_handoff | busy/current same-role check and cancelled_analysis_launch_cleans_reservation_under_retained_child via double; real role policy/CSRF #193 |
| U06 | cancel_is_idempotent_without_completion; reset_cancel_does_not_touch_same_id_in_other_visit_or_generation | exact target counts and no early completion |
| U07/U10 | request_child_and_shield_leases_prevent_drain; child_context_changes_only_lease_and_releases_after_retirement; child_release_finishes_under_repeated_cancellation_and_contended_transition | request/child/shield counts and captured factory/epoch |
| U08 | resource_reservations_are_scoped_and_serialized; drain_waits_without_holding_transition; drain_timeout_includes_waiting_for_transition | independent resources and bounded wait |
| U09/U12 | write_guard_serializes_commit_with_reset; role_change_first_rejects_old_write_guard_without_writes; write_guard_first_commits_before_role_change; late_owned_callbacks_never_open_factory; authority_errors_preserved_and_no_lease_retained | role-first rejects STALE_ROLE409/write0; guard-first commits before role change; owned callback guard rejects before factory; real SQL/reset/role matrix #191/#194/#198 |
| U13/U14 | reset_handoff_releases_own_request_but_not_other_live_request | consumes retired scope/early release; real mark/handoff/rollback/receipt/disconnect #193/#194 |
| U15/U16/U17 | authority_errors_preserved_and_no_lease_retained; namespace_identity_and_retired_cleanup; drain_timeout_includes_waiting_for_transition; existing orphan/probe regressions | authority error and release semantics; actual TTL/shutdown/budget #191/#194 |
| U18 | authority_errors_preserved_and_no_lease_retained; invalid_scope_is_rejected; namespace_identity_and_retired_cleanup | lifecycle BUSY409/INVALID422; authority errors propagated; HTTP/SSE terminal mapping #198/#201 |
| U19 | probe/comparison/analysis/student/mentor/transaction regressions; normal_never_calls_demo_authority; legacy_view_is_normal_only_and_shares_token_registry | normal factory, guards, cancellation, reservations, analysis retention |
| U20 | existing test_analysis_runs.py, test_analysis_concurrency.py and chunk lifecycle regressions | durable replay/body conflict unchanged; full demo student/mentor retry #198 G3 |
| U21 | existing test_transactions.py, test_student_lifecycle.py, test_mentor_lifecycle.py, test_deleted_session_access.py | helper preserves end-before-analysis; real demo URL/CSV #200/#203 |
| U22 | demo_cleanup_cli_rejects_before_database_open; demo_cli_process_rejects_without_importing_application_database; cleanup_cli_invalid_mode_fails_closed; existing test_comparison_retention.py | engine-open zero; normal ro/rw/count/JSON tests retained |
| U23 | allowlisted diff; guarded demo error logging; no production stubs | no visitor metrics added; other-owner requests below |
| U24 | gate table below | unit evidence never promotes integration gates |

## Historical execution record (before Binding B)

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
| #192 unit/normal regression | passed: 2262 full; 287 combined targeted; 108 impact | current merged source 62d60a6 |
| #191 real binding | passed at 12f8bf5; 124 unchanged authority cases plus real #192 matrix | capability/guard/retain/release/public counts |
| #194 G1 | not run | inject lifecycle into resolver/middleware/lifespan; no normal startup orphan scan in demo; configure drain timeout |
| #198 G3 | not run | pass ctx/registry through call admission, student/mentor ASGI cleanup, pipeline/chunk/ledger/shield, all final DML; scope cancel_connection |
| #193 reset/role | not run | same-handle busy_locked; mark then handoff then release_request_db then cancel_scope/drain; retain receipt Task |
| #200 guards/admin | not run | forbid demo comparison/probe direct calls; migrate admin registry consumers and pass fixed context |
| #203/#204/G5 | not run | independent security/browser on integrated fixed candidate |

Skipped/unverified: G1/G3/G5 and browser/live checks (CI).
Risk: this branch supplies lifecycle boundaries; it does not yet make the whole
application safe to serve in demo mode.

## Historical Repair R1 — reserved scope and role/write ordering

Coordinator order `wo-192-repair1.md` repairs reviewed candidate `9f55275`.
The demo branch now rejects the reserved NORMAL_SCOPE before normal dispatch;
normal scope-only APIs and normal contexts retain their existing behavior.
`test_invalid_demo_namespace_never_enters_normal_scope` covers seven malformed
namespaces across register/assert_current/write_guard/child_context and cancellation
via scope_for(ctx), always preserving the existing normal token and cancel signal.
The focused red run reproduced all five sentinel bypasses: 5 failed, 32 passed.

U09 now explicitly covers role-first → DEMO_STALE_ROLE409/write0 and
write-guard-first → commit before role change, using the local authority double
and an Event barrier. The old reset-only test name was corrected. Real SQL and
#191/G1/G3 role/reset integration remain pending.

With `UV_PROJECT_ENVIRONMENT=/tmp/w192-demo-lifecycle-venv`:
- Requested three-file targeted command: **147 passed**, 17 warnings, 59.29 seconds.
- `uv run --frozen ruff check .`: passed.
- `uv run --frozen black --check .`: passed, 355 files unchanged.
- Full suite: not rerun, as ordered. Reuse the source-candidate `ea2ba63` / evidence
  `9f55275` result of 2085 passed. This repair changes only demo sentinel validation,
  its dedicated tests, and this evidence; the normal-context branch is unchanged.

R1 deferred P2 to Binding B; the capability mismatch is resolved by the concrete
#191 binding described above and verified below.

## Binding B — real #191 authority (pre-restart source 037241c)

Integrated #191 `79d29259e27761ffe75968517cc036d3df31c1f9` via merge
`d9fd0264da30a17929a3adc07e319ab5ce4af33f`. P2 is resolved using the issued
`VerifiedVisitor`, concrete `DemoContext`/`Lease`/`TransitionHandle`, shared
`DemoError`, and public total-lease accounting. The same lifecycle supplies
`cancel_scope` and `drain` to #191; the drain result is its supported
`dict(drained=bool, remaining=int)` shape. No #191 file was changed.

The dedicated fixture copies only the authorized tiny-seed construction pattern,
runs the official migrations on temporary synthetic data, and wires a real
`DatabaseRoot` / `VisitorRegistry` / `Lifecycle`. It imports no #191 tests.
The following tests now provide real authority evidence (U01–U18); the retained
doubles additionally exercise forced construction failures, cancellation and
consumer callback barriers without claiming application integration:

| Criteria | Real #191 test | Observations |
|---|---|---|
| U01/U04/U06/U13 | real_namespaces_isolate_equal_ids_and_allow_retired_pop | six kinds × int/string UUID/UUID; A-only cancellation; other generation/normal absent; retired pop releases A once and preserves B |
| U02/U03/U07/U10 | real_visitor_capability_registration_and_request_release | request+execution count 2; request release 1; done/pop 0; distinct lease and identical captured capability |
| U03/U18 | real_concurrent_registration_has_one_token_and_child; real_registration_failure_releases_retained_lease | duplicate BUSY; one token/count 2; failed construction leaves token 0/count 1 |
| U05/U10 | real_busy_excludes_request_leases_and_includes_execution_child; real_registration_first_blocks_role_change_but_same_role_is_noop | two plain requests are idle; execution child is busy; current same-role no-op; different role BUSY; after child exit role changes |
| U07/U08/U10/U16/U17 | real_drain_waits_for_every_lease_kind; real_drain_reaps_token_completed_during_lease_wait | live request/generic/execution lease times out with remaining 1; release gives drained/0; token completion during wait is reaped, no transition held while waiting |
| U09/U12/U13/U14 | real_write_guard_and_reset_serialize_sql_without_late_writes | guard-first commit precedes mark; mark-first denies stale SQL; captured factory remains fixed; new generation stays fictional before/after late-write denial |
| U05/U09/U12/U18 | real_role_and_write_guard_serialize_sql_without_stale_dml | role-first STALE_ROLE; guard-first commit precedes role transition; stale SQL rejected and stored body preserved |
| U12/U18 | owned_execution_propagates_original_demo_error | analysis/probe/cleanup propagate the identical shared error; no generic failure write; probe permit released |

Real #191 database/visitor/lifecycle tests are also run unchanged, supplying its
TTL/state/error priority, lease validation, reset/expiry/shutdown and budget
coverage. #192 does not duplicate that authority. HTTP receipt handoff, CSRF,
SSE revocation and application shutdown gates still belong to #193/#194/#198.

Binding B checks use the same frozen environment described above:
- Requested lifecycle/probe/comparison command: **163 passed**, 17 warnings,
  40.09 seconds (138 dedicated cases; 18 redundant double namespace cases
  replaced with stronger real-authority cases).
- Requested 12-file impact command: **108 passed**, 66 warnings, 271.26 seconds;
  all requested filenames exist.
- Unchanged three-file #191 matrix plus existing probe/comparison regressions:
  **133 passed**, 17 warnings, 81.26 seconds (108 #191 cases).
- `uv run --frozen ruff check .`: passed.
- `uv run --frozen black --check .`: passed, 360 files unchanged.
- Independent read-only worker review found no concrete binding bug.

## Binding B resume — 12f8bf5 receipts and main documentation

Coordinator order: `/mnt/d/dev/mp2-wt/_coord/wo-192-bind.md`.
The real authority now includes body-aware receipt replay and accepted-failure
receipts. Its transition/check/lease/write-guard/lifecycle APIs consumed by #192
remain compatible. Main's `d0-03-v2` correction changes C14/C27 ownership for
#198; #191–#193 retain the approved `d0-03-v1-r3` semantics.
No lifecycle behavior, tests or #191-owned source needed modification for this
update. The existing real matrix above is rerun on the merged candidate.
The interrupted pre-restart full run has no completed result and is not counted
as verification. Resume check counts are recorded below and in the worker report.

The restored environment is Python 3.12.3 with frozen dependencies installed by
`UV_PROJECT_ENVIRONMENT=/tmp/w192-resume-venv uv sync --frozen --extra dev --python 3.12`
(79 packages). Targeted checks run in the requested worktree. The completed full
suite runs in `/tmp/w192-checks`, a registered detached Git worktree at the same
source commit, to avoid the mounted drive's per-file test delay. Both trees have
identical tracked Python, tests, migrations, fixtures, configuration and lockfile.

Resume verification results (all exit 0):

- `uv run --frozen python -m pytest -q tests/test_demo_d1_02_lifecycle.py tests/test_probe_lifecycle.py tests/test_comparison_lifecycle.py tests/test_demo_d1_01_database.py tests/test_demo_d1_01_lifecycle.py tests/test_demo_d1_01_visitors.py`:
  **287 passed**, 17 warnings, 146.28 seconds (163 owned/normal plus 124 #191).
- Requested 12-file impact command (the same complete list in the report):
  **108 passed**, 66 warnings, 303.38 seconds. No missing filenames.
- `uv run --frozen ruff check .`: passed.
- `uv run --frozen black --check .`: passed, 360 files unchanged.
- One completed final `uv run --frozen python -m pytest -q`:
  **2262 passed**, 1340 warnings, 1822.48 seconds (30m22s).
  No failures, skipped tests or unrelated-failure reruns.
- `git fetch origin` and `git merge --no-edit origin/main` (with required footer)
  include `0115b8c` via `dab9887`. Its committed tree is identical
  to checked source `62d60a6` (tree 61d3b4acf8be5255daaeb55a905bdea5f9699cf1).
  The final evidence commit changes only this Markdown; test inputs stay fixed.
- Browser and live streaming: not run (CI). G1/G3/G5 remain other-owner work.

The final report records the evidence commit HEAD. No dependency API changes,
paid calls, production data, configuration changes or source edits followed the
completed full suite. The earlier restart/sandbox interruption is resolved;
the original branch and coordinator report directory are writable.
