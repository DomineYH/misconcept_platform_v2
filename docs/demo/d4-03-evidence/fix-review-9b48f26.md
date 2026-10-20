> 독립 reviewer r205rev의 D6 수정 리뷰 원문 사본(2026-10-20). 본문이 언급하는 `fix_lock_order_check.py`, `fix-lock-order-result.txt`, `fix-pytest-diagnostic.log`, `_coord/reports/logs-wlock/`는 reviewer·coordinator 작업 공간에 있고 이 저장소에는 넣지 않았다.

# Fix review — PASS

- Reviewed SHA: `9b48f26c0366349a2a9fb70fb1cd6a651c353ce5`, branch `fix/lock-order`, PR #256.
- Base: `ba64b82ef74226094a5b51888b1bbcb1734bb062`.
- Reviewer: independent Codex session r205rev, original P1 reporter.
- Scope: the admission/visitor lock-order fix, its call-site audit, regression test, and normal-mode impact. This closes the original P1; it does not resolve the separate C27 ownership finding or grant release/G5 approval.
- HEAD matched the requested SHA; worktree status was clean before and after inspection. Product diff is only the lock reorder/comment in `src/services/analysis_runs.py`; the other changed file is `tests/test_lock_order.py`. No source/worktree writes or network access.

## Results

| Requested verification | Result | Evidence |
| --- | --- | --- |
| 1. Original deadlock resolved | PASS | `analysis_runs.py:166–171` now acquires admission → visitor guard → session resource lock. Fresh AST diagnostic reproduces the old deadlock and proves both fixed functions reach their DB bodies and release both locks under the identical queued-contention schedule. |
| 2. Audit correct; no other inversion | PASS, within the inspected source call graph | Enumerated every `execution_lock()`, `admit_call()`, `recheck_call()`, `readmit_call()` and `resource_lock()` site under `src/`, then traced their callers and enclosing guards. Findings below agree with report-wlock's audit. No remaining visitor/resource-lock → admission edge found. |
| 3. Meaningful Red/Green regression | PASS by inspection and reused execution evidence | `tests/test_lock_order.py:45–81` uses actual reservation functions, actual Lifecycle/VisitorRegistry and generation DB. It queues student then analysis behind occupied admission and requires both to finish with 404, proving they reached the DB body. The old order takes the visitor lock while queued; the new order cannot. Fresh standalone diagnostic independently confirms this difference. Direct pytest execution in this reviewer environment did not finish fixture setup; it is not counted as passed. |
| 4. Normal-mode regression | PASS by source review and reused targeted evidence | Normal `write_guard` is a no-op (`src/demo/lifecycle.py:336–341`); the session resource lock has only one production acquisition (`analysis_runs.py:170`). Therefore moving admission outside it cannot create a reverse edge elsewhere. Reservation validation, SQL, return values, exceptions and cleanup body are unchanged. Implementer logs show normal analysis runs/concurrency/retry 17/10/4 passed. |

No new actionable defect found in this fix.

## Lock audit

There are 12 direct admission-lock acquisitions, six calls to `admit_call` including readmission, two calls to `recheck_call`, and one production session-resource-lock acquisition. The table groups all of them.

| Sites (relative to repository) | Verified order / boundary |
| --- | --- |
| `src/services/analysis_runs.py:168`; `generation_runs.py:189`; `mentor_generation.py:48` | All take admission before demo visitor guard. Analysis alone additionally takes the session resource lock, before opening its DB. No outer guard is retained by the public callers. |
| `src/services/call_admission.py:196` | `admit_call` releases the preliminary `assert_current` transition before taking admission. Its DB reads do not retain a visitor write guard. |
| `src/services/call_admission.py:288` | `recheck_call` takes admission before `assert_current`'s visitor transition. |
| `src/api/routes/admin_ai_settings.py:26`, `admin_probes.py:67`, `admin_providers.py:318` | Dangerous demo operations are rejected by `demo_read_only` dependencies; normal paths have no visitor guard. Checked all three `commit_change` callers. |
| `src/services/comparison_conversations.py:179`, `:381`; `comparison_conversation_recovery.py:40`; `comparison_runs.py:54` | Public mutation callers refuse demo via route dependencies/preflight. These functions do not acquire a visitor guard before admission. `approve_call` is synchronous and does not recursively acquire admission. |
| `src/services/analysis_invocations.py:158` | `child_context` retains a lease but releases its transition before yielding (`lifecycle.py:304–312`). Analysis pipeline invocation occurs before the separate result-saving write guard (`analysis_runs.py:381–399`). Thus admission is not entered under a visitor guard. |
| `src/services/student_bot.py:112`; `probe_execution.py:48`; `provider_smoke_execution.py:130`; `src/api/routes/admin_catalog.py:57` | Callers close read sessions/release currentness transitions before admission. No enclosing write guard. Probe/catalog/smoke entry policies prevent demo execution. |
| `src/services/call_admission.py:346` (readmission), called by `call_execution.py:316` | Backoff/readmission occurs outside ledger guards. |
| `src/services/call_execution.py:220`; `comparison_stream.py:443` | Ledger `start_attempt` has returned, including exiting its guard (`invocation_ledger.py:39–67`), before call recheck. Comparison recheck has no enclosing visitor guard. |

Indirect boundaries checked: `request_analysis` commits under its guard and exits it before acquiring a child lease/reserving (`analysis_runs.py:578–600`); teacher `guarded_db` and admin `analysis_db` supply plain visitor sessions rather than retaining a request-session write guard. Student/mentor route registration releases its transition before reservation. Finish/failure/ledger/probe/generation lifecycle and reset guards do not call admission while held. `src/demo/` has no admission-lock calls. Reset can temporarily delay a reservation now holding admission, but does not wait for admission while retaining its transition; this is not a lock cycle. Currentness is still checked after acquiring the visitor guard.

## Regression test assessment

- The test exercises the actual deadlocking lock prologues, not an assertion about source order. Synthetic seed and missing session are appropriate: the original failure happens before session validation. Both 404 assertions ensure progress beyond the locks.
- `queued()` (`tests/test_lock_order.py:36`) observes the actual admission wait queue; it does not rely on a wall-clock delay to arrange the race. The five-second bound detects noncompletion, and pending reservation tasks are cancelled and awaited.
- It intentionally runs under the normal pytest process configuration with real demo contexts/guards. It does not claim real HTTP/demo-startup coverage or successful generation/analysis; neither is necessary to reproduce this specific pre-DB deadlock.
- Known test limitation: `_waiters` is a private asyncio implementation detail. Its failure would be visible rather than silently skipping the race. The bounded queue helper could provide better task cleanup if queue formation itself fails, but that is not an observed failure or a release blocker for this patch.

## Fresh and reused evidence

Fresh, completed:

- `python3 fix_lock_order_check.py` → exit 0; `fix-lock-order-result.txt` records:
  - `RED: original functions deadlock under queued admission contention`
  - `GREEN: fixed functions both reach DB bodies; both locks released`
- This adapts the original `lock_order_check.py`: it loads unchanged function ASTs from the original and fixed worktrees, uses asyncio locks with a controlled admission queue, and substitutes sentinel DB bodies. No DB/network/product writes. Unlike the original reproducer, it waits on admission queue formation rather than expecting analysis to acquire the visitor lock before admission is released, since that expectation is exactly what the fix removes.
- Read-only diff, status, call-site enumeration and source tracing described above.

Attempted, not passed:

- Direct new-test run using the implementer's Python 3.12.3 venv, `PYTHONDONTWRITEBYTECODE=1`, `APP_MODE=normal`, explicit source/test PYTHONPATH, `-p no:cacheprovider` and a workspace-local basetemp stalled without completing; reviewer interrupted it (exit 130).
- One bounded diagnostic rerun used `timeout --signal=INT --kill-after=5s 40s ... python -m pytest -vv -s -p no:cacheprovider -o faulthandler_timeout=20 --basetemp=<workspace>/fix-diagnostic-tmp <checkout>/tests/test_lock_order.py`. Exit 137 after timeout/kill. `fix-pytest-diagnostic.log` shows an async-generator fixture still in setup, an idle aiosqlite worker and an event loop waiting in select; the test body had not begun. The underlying setup stall is undiagnosed. It supplies no evidence against or for the fixed reservation behavior.

Reused, explicitly not fresh reviewer execution:

- `_coord/reports/logs-wlock/test_lock_order.log`: 1 passed in 8.79 s.
- Normal analysis logs: runs 17 passed, concurrency 10 passed, retry 4 passed; demo reset log: 25 passed. Read these logs and the summary.
- `_coord/reports/logs-wlock/summary.txt` records the other affected suites: demo calls 148, demo analysis 21, G3 4, student lifecycle 8, mentor lifecycle 11, all passed. Revision/clean-tree attribution is from `report-wlock.md`, not independently embedded in these pytest logs.
- The implementer reports the new test failed Red on ba64b82 with both tasks pending and passed Green on the fix. No separate Red log was present in the supplied log directory, so that exact pytest Red execution remains an implementer attestation; the fresh AST diagnostic independently reproduced the old failure.

Not checked: full pytest, browsers/live, CI status, fresh normal-suite execution, or real HTTP contention. No application or source modifications made.
Remaining risk: this is PASS for the P1 fix only; full release acceptance still needs its other checks/ownership resolution, and the direct reviewer pytest setup stall remains unexplained.
