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
rendering. Test sockets are blocked. The final Python suite contains 37 passing
tests; browser checks above run separately with intercepted LLM/API responses.
No live API calls, real database upgrades, deployment or PR merge were performed.
