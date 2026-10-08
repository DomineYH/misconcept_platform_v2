# Sequential refactoring verification

## #1: baseline

Ownership/group authorization, committed teacher messages after bot failure,
and preservation of existing analysis after regeneration failure are tested
using file-backed temporary SQLite and fake LLM responses. CI uses the lockfile.

Known reproductions to resolve in subsequent issues:

- #2: run the historical migration runner on an empty DB (missing user table),
  or after ORM create_all (duplicate scenario.chat_model).
- #3: admin end flushes ended_at, then waits for analysis; an independent SQLite
  writer in that wait observes a write lock. End state can be rolled back too.
- #4: an ended session with a fallback SessionSummary bypasses the pipeline on
  POST /sessions/{id}/analyze because existence is treated as success.

## #2: database installation and upgrades

The frozen `src/db/migrations/baseline.sql` (revision 023) defines the application
schema, matching the ORM's columns, nullability, keys, checks and indexes.
`uv run --frozen python -m src.db.migrations.migrate` is the official command.
`src.db.init_schema` and development startup delegate to it; production startup
never changes schema. Historical 001–022 files and recorded filenames remain
unchanged; 023 adopts their final schema without claiming they ran. Future SQL
upgrades start at 024. Down scripts are never applied automatically.

Supported inputs: empty databases, the captured ORM/SQL initializer schemas,
and revision 018–022 schemas (the optional report/UI tables and nullable
operation/category/grade columns may be absent). Older or unknown columns are
rejected atomically; upgrade those on a backup to 018 first. Legacy chatbot
configuration/audit tables remain untouched for data preservation; they are
not part of the application schema or created in new installs. Existing label
JSON is retained because both old and new formats are supported. Constraint
violations abort the upgrade; they must be repaired explicitly on a copy.

With the app stopped, back up using SQLite's backup API (it includes WAL data):

```sh
python - <<'PY'
import sqlite3
with sqlite3.connect('dialogue_sim.db') as source:
    with sqlite3.connect('dialogue_sim.backup.db') as backup:
        source.backup(backup)
PY
# Copy backup to upgrade-check.db, then validate the copy:
DATABASE_URL=sqlite+aiosqlite:///./upgrade-check.db uv run --frozen python -m src.db.migrations.migrate
```

Check `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, row counts, and a
login/session on the copy before scheduling the real upgrade. Stop all app
workers for the real command. On failure, explicit BEGIN/rollback keeps DDL,
rows and history together. Retain the untouched backup; to restore, stop all
writers, use the backup API from backup into the destination (do not replace
just a live DB file while WAL/SHM sidecars exist), then restart the old code.
The refactoring run does not upgrade the real database.

## #3: transaction ownership

- User close/end and admin end commit ended_at through mark_session_ended.
  Analysis failure never reopens a session.
- Message processing commits the teacher row before StudentBot for polling;
  all remaining bot/usage writes occur after external calls and are committed
  by the request dependency. A bot failure rolls those back, retaining teacher.
- Initial analysis and regeneration load inputs, commit the read transaction,
  call LLMs, then persist the result in one short commit. Regeneration reserves
  SQLite's writer only for delete/insert replacement, never during LLM waits.
- Request dependency owns final commit/rollback for ordinary CRUD and handles
  propagated persistence errors. The admin end route catches analysis errors,
  so it explicitly rolls back and eagerly reloads every template relationship.
  User analysis captures label names before a failed flush can expire ORM state.

The file-backed SQLite WAL regression performs an independent write during the
admin analysis wait, verifies ended_at is already visible, and injects both
LLM and constraint errors. Partial analyses and failed replacements roll back.

## #4: analysis result policy

| Stored state | User analyze request | Admin regeneration |
| --- | --- | --- |
| ok | Reuse | Replace with ok only |
| degraded | Reuse (usable partial report) | Replace with ok/degraded |
| failed | Run pipeline again | Retry; failed output preserves prior rows |
| legacy, no report | Reuse | Treat as good; preserve on failed/degraded output |
| legacy fallback, no report | Retry | Retry |

Only pre-status legacy rows use the exact historical fallback sentence to
recognize failure. All new failures store a report with status=failed and
return feedback_status, retryable=true and error=analysis_failed. The chat
keeps the retry action available on that response even when HTTP is 200.

All result writers use one BEGIN IMMEDIATE window: recheck latest persisted
state, preserve better results, delete old question/summary/report rows,
insert replacements and commit. Late fallback/concurrent retries cannot
replace a completed success. LLM calls remain outside the writer lock.

## #5: shared result assembly and admin operations

Both analysis viewers call analysis_results.load_analysis_response; user
ownership/end checks and admin dependencies stay at the route boundary.
Scenario/framework update/delete routes call their existing operations modules.
The scenario service now matches the live router (the previously unused copy
differed): omitted and explicit-null fields preserve values; empty optional
problem/greeting/name/subject strings clear them; tutor -1 disables, null
preserves; group null preserves and [] clears. Problem/greeting fields remain
supported. Framework category_name distinguishes omitted from explicit null.

Active scenario references still block framework deletion. Scenario deletion
is soft and retains session data; deleting a framework with only soft-deleted
scenarios retains the previous cascading cleanup behavior. The unused admin
backup was removed after preserving it in baseline commit 8b22614.

## #6: chat extraction and browser verification

The extraction and state changes are separate commits. Jinja sends a tojson
configuration block; chat.js initializes once and retains HTMX and plain JS.
Composer/analysis controls share state updates. Poll and POST responses remove
duplicates by server ID in either arrival order; cursor advances after DOM swap.
Auth expiry preserves the in-flight draft and cannot restart polling. The
existing About overlay was moved before its event binding to fix a page-load
null-element error exposed by the browser check.

Repeat with a Playwright Page and the isolated fixture server:

```sh
uv run --frozen python tests/browser_server.py
# In a Playwright script (Playwright is a test tool, no app dependency):
# import checkChat from './tests/browser_chat.mjs';
# await page.goto('http://127.0.0.1:8765/chat');
# console.log(await checkChat(page));
```

Chromium checks use the real Jinja layout, CSS, HTMX and chat.js, with HTTP
responses intercepted: 1280px desktop / 390px mobile panels, Ctrl+B,
Enter/Shift+Enter, duplicate script loading, held POST crossed with polling,
late duplicate poll, restored input after HTTP 500, end → failed analysis →
retry → modal, Escape/button close, ended reload, auth expiry during POST and
polling, draft restoration/login navigation, CSRF on HTMX/fetch. Expected
500/401 responses are injected; uncaught page errors must be zero.

## #7: LLM policy and ownership

The locked OpenAI Python SDK is 2.7.1. Its installed _constants.py defaults to
two SDK retries; _base_client.py retries connection failures and HTTP
408/409/429/5xx. Application-created clients now set max_retries=0. The one
shared create_response call retries those transient errors up to three total
attempts (2s/4s backoff), then re-raises the original exception. HTTP 400/401/403/
422, local input errors, and response parsing/validation errors do not retry.
Retries wrap only HTTP, so tutor state and prompt work are not repeated.
Existing greeting/tutor fallback handling runs after attempts are exhausted.

Services accept client= for tests; injected real SDK clients must also set
max_retries=0 and are closed by the caller. Services own default-created clients
and expose async context management. The analysis pipeline uses AsyncExitStack
for its analyzer/synthesizer, including exceptions; the message route closes
all SessionManager bots in finally, including partial initialization/failure.
Model names, reasoning efforts, token limits and prompt files are unchanged.
Usage from a received synthesis response is retained even when its JSON fails.
The existing response-parser compatibility branches remain intact.

Tests use fake responses and the real SDK over httpx.MockTransport, without
API credentials or outbound network. They verify response contracts, model/
reasoning/token settings, transient/permanent attempt counts, tutor side effects,
owned/injected lifetimes, and greeting/classification/synthesis usage logs.

## Final integration check

The real ASGI HTTP routes are exercised with signed test session cookies:
unauthenticated redirect, foreign-owner denial, teacher denial on admin actions,
failed → successful analysis, ended-session message rejection, and admin modal
rendering. Test sockets are blocked. The initial refactoring suite contains 37 passing
tests; browser checks above run separately with intercepted LLM/API responses.
No live API calls, real database upgrades, deployment or PR merge were performed.

## PR review follow-up: seed compatibility and reasoning settings

Plan:

1. Reproduce seeding failure on fresh and legacy temporary databases.
2. Make raw seed INSERTs supply the baseline's required timestamps and tutor
   sensitivity; remove writes to the obsolete chatbot configuration table.
3. Include the reasoning validator fix and tests in the PR, retaining earlier
   models' `minimal` value and rejecting unknown values.
4. Run regression tests, repeat the seed CLI, verify login/app startup, and
   review the final commit and remote CI before judging merge readiness.

The seed regression checks both fresh installation and legacy upgrades,
framework/scenario/template references, administrator password verification,
foreign-key integrity, and repeated seeding without changing existing rows
or passwords. Legacy chatbot configuration rows remain untouched; fresh
databases do not create that obsolete table. SQL defaults in the frozen
baseline are unchanged; the seed writer now supplies the required values.

The shared reasoning validator accepts `xhigh` and `max` for all three settings.
The GPT-5.6 values are `none`, `low`, `medium`, `high`, `xhigh`, and `max`, as
documented in the [official model page](https://developers.openai.com/api/docs/models/gpt-5.6-sol).
The validator accepts the union of supported API values, including `minimal`
for older models; the selected model still determines which subset is valid.
Tests cover environment values, the reported `max/xhigh/xhigh` dotenv input,
and invalid values. Model defaults, prompts, and token limits are unchanged.

Follow-up validation completed:

- `uv run --frozen python -m pytest -q`: **50 passed**.
- The actual `python -m src.db.seed` command succeeded twice on a temporary DB.
- Uvicorn started with `TESTING=false` and `max/xhigh/xhigh`; HTTP checks passed
  for seeded administrator login, scenario listing, and the scenario chat page.
- Standards and issue/spec reviews found no remaining blocking findings.

These checks used temporary databases and synthetic credentials, with no live
LLM calls or changes to the local application database.

## S0 Phase A: integration and pilot cutover (#33)

The contract is [spec #24](https://github.com/DomineYH/misconcept_platform_v2/issues/24),
especially D9/D10 and its Testing Decisions. This work rehearses the procedure
on synthetic SQLite databases; it does not authorize an operating database
upgrade, paid API calls, deployment, push or merge. Execute the operating steps
below only under separate approval, with the actual deployment paths supplied.

### Cutover order

1. Announce the maintenance window, the end of existing sessions, and the need
   to reload previously opened chat pages. The retired write endpoint returns
   `410 reload_required`; old pages cannot bypass the new generation contract.
2. End all existing active sessions through the existing teacher/admin close
   paths before stopping the old app. Verify `SELECT count(*) FROM session
   WHERE ended_at IS NULL AND deleted_at IS NULL` is zero. Preserve historical
   messages, analyses and NULL turn links; do not infer or backfill old turns.
3. Stop **every writer**: app workers, scheduled jobs, seed/admin scripts and
   shell sessions that write SQLite. Keep maintenance access closed. Never let
   old and new versions write the same database concurrently.
4. Use SQLite's backup API as in section #2, with explicit source/backup paths.
   It includes committed WAL pages. Do not copy only the main `.db` file or
   remove live WAL/SHM files. Retain the untouched backup and the old code/config
   revision. Make the upgrade rehearsal copy with the backup API too:

   ```sh
   # Paths below are examples; supply the approved maintenance paths.
   python - <<'PY'
   import sqlite3
   with sqlite3.connect('/maintenance/pre-phase-a.backup.db') as backup:
       with sqlite3.connect('/maintenance/upgrade-check.db') as copy:
           backup.backup(copy)
   PY
   DATABASE_URL=sqlite+aiosqlite:////maintenance/upgrade-check.db uv run --frozen python -m src.db.migrations.migrate
   ```

5. On the copy, run the official migration command **twice**. Compare schema
   with a fresh install (frozen baseline 023 followed by 024), check
   `PRAGMA integrity_check` = `ok` and empty `PRAGMA foreign_key_check`, and
   compare row counts and existing message IDs/role/body/timestamp/metadata.
   Verify preserved normal analysis, teacher/admin CSV columns and actual
   stored ordering, ownership/group denial, and legacy video values remaining
   in storage without appearing in HTML/API/provider input. Old message
   turn/run links must stay NULL. Repair blank public problems explicitly in
   the administrator UI before starting new sessions; never copy transcripts
   automatically or substitute the private student prompt.
6. Review `CONTEXT_WINDOW_TURNS` with the administrator: N now means **completed
   teacher–student pairs**, with default 10. Previously it counted individual
   messages, including mentor messages. Explicit existing values are preserved,
   so an old value of 20 now allows 20 complete pairs. Do not silently halve
   values. Record the chosen value before reopening traffic.
7. With writers still stopped, apply the same official migration command to
   the approved destination and repeat integrity/data checks. Do not edit 023,
   historical SQL, legacy video columns or stored `tutor` roles. Production
   startup (`ENV=production`) does not install the schema automatically.
8. Start **one app instance, one asynchronous worker**, with `TESTING=false`,
   production secrets and the reviewed configuration:
   `ENV=production TESTING=false uv run --frozen uvicorn src.main:app --workers 1`.
   Do not use reload or multiple replicas. Startup marks remaining running
   executions interrupted without generating again. Check health/login,
   historical readers and the blank-problem gate; reopen traffic for new
   sessions only after those checks. Paid generation smoke tests need their
   own approval. Retain the backup through pilot acceptance.

### Restore and data-loss boundary

Close traffic and stop **all** new-version writers. Restore the retained backup
into the destination with SQLite's backup API (section #2), rather than
replacing a database underneath WAL/SHM sidecars. Check integrity, foreign keys,
row counts, IDs, metadata, analysis and migration history, then restart the
recorded old code/config as a single worker. This restores the pre-024 schema
as well as its data; no automatic down migration is used. **Any messages,
sessions, coaching, analysis or configuration written after the backup are
lost on restore.** Decide whether that loss is acceptable before restoration.

`test_wal_backup_upgrade_readers_and_restore` in
`tests/test_phase_a_cutover.py` holds a synthetic 023 connection open with
committed WAL pages, backs it up, upgrades a separate copy twice, verifies all
old rows plus the real authenticated readers/exports, introduces a new write,
and restores the backup. It proves both the old schema/data recovery and loss
of the later write. `test_024_preserves_legacy_and_matches_fresh` separately
compares upgrade/fresh schemas; no operating database is read or copied.

### Coverage of A1–A8

The numbered criterion groups below cover every child-ticket acceptance item;
the Python tests use file SQLite, signed login and fake providers, with network
blocked. Browser checks use the isolated section #6 fixture server.

| Ticket / acceptance items | Evidence |
| --- | --- |
| A1 #25, 1–5: video-free teacher/admin screens, public problem/greeting, blank problem, desktop/mobile/keyboard, preserved storage/no product mock | `test_scenario_screens.py`, `browser_scenarios.mjs`, unchanged baseline/legacy column checks |
| A2 #26, 1–5: retired-field 422, text CRUD/non-exposure, missing-problem gate/history/edit, data preservation, permissions | `test_scenario_api.py`, `test_phase_a_cutover.py`, `browser_scenarios.mjs` |
| A3 #27, 1–7: optimistic/durable identity, exclusive events, failure/drafts/retry, parser/XSS, JSON/auth, zero normal polling, keyboard/mobile/EOF | `test_student_sse.py`, `browser_student_stream.mjs`, `browser_student_errors.mjs`, `browser_student_recovery.mjs` |
| A4 #28, 1–10: 024/ORM/constraints/fresh, DB reservation/idempotency/ordering, one student/no classifier/mentor/retries, settings/failures/resources/deadlines, real mount/410, races/security, integration before release | `test_generation_migration.py`, `test_student_generation.py`, `test_student_concurrency.py`, `test_student_security.py`, `test_student_failures.py`, `check_student_live.py`, all browser checks; no deployment |
| A5 #29, 1–8: public recovery/auth, bounded lookup/storage/EOF, explicit resend/retry, disconnect/partial isolation, startup, all end races, unknown DB outcomes, analysis/draft preservation | `test_student_lifecycle.py`, `test_student_failures.py`, `test_student_security.py`, `test_transactions.py`, `browser_student_recovery.mjs`, `browser_student_errors.mjs`, `browser_chat.mjs` |
| A6 #30, 1–6: N completed pairs/default/transition, bounded student/mentor context, excluded partial/mentor/greeting, LIMIT/index plan, unchanged full readers | `test_config.py`, `test_turn_context.py`, `test_phase_a_cutover.py`; README and cutover step 6 |
| A7 #31, 1–6: original-turn coaching, final-only/no-intervention, explicit failure/busy retry/obsolete/end, independent input/focus/scroll, NULL legacy order/terminology, desktop/mobile | `browser_mentor_stream.mjs`, `browser_mentor_recovery.mjs`; shared admin history additionally checked by `test_phase_a_history.py` and `browser_phase_a_history.mjs` |
| A8 #32, 1–9: completion-trigger/validation, unchanged judgment/deadlines, durable replay/no-intervention, independent rights/busy, counters, obsolete/late target, linkage/context/end, enabled setting, integrated races | `test_mentor_generation.py`, `test_mentor_policy.py`, `test_mentor_security.py`, `test_mentor_lifecycle.py`, `browser_mentor_stream.mjs`, `browser_mentor_recovery.mjs` |

The live check uses actual localhost uvicorn, active CSRF and the installed SDK
over fake upstream transport. It holds upstream completion until a delta is
received and the stored run is still running. It prints persisted first-output
and completion durations and proves one student call, zero misconception calls
and zero mentor calls in that path even with mentor enabled. The slow-mentor
HTTP test completes the next student while coaching is held; browser tests
count zero normal message/recovery polls. These are structural checks, not a
p95 release target or measurements of actual provider latency.

**Unverified:** operating reverse-proxy buffering and provider billing cessation
after disconnect. Phase A remains single-worker, with no automatic generation
retry or mentor queue; busy skipped turns require explicit requests. Memory-only
partial tokens may be lost on process failure, and unavailable DB cleanup is
best effort. No S1/S3/S5 feature or legacy-column deletion is part of this work.
