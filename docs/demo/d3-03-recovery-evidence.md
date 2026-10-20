# D3-03 browser recovery evidence (#201 PR3)

Contract: `d0-03-v1-r3` semantics (wire `contract_version` stays
`d0-03-v1-r3`), §10, §11, §24, §24.1, §25, plus #190 change record 3
(`demo:state-check`). This page is unit evidence for #201 PR3 only. Every
check below runs against the normal browser fixture server
(`tests/browser_server.py`, `TESTING=true`) with `#demo-config`, error
envelopes, SSE frames and CSV responses injected by Playwright. **No real demo
backend, CSRF middleware or `demo-tour.js` was involved**, so nothing here
marks G4 (#202) or G5 (#204) integration done.

## What PR3 changes

- `display-language.js`: one shared recovery helper on `uiLanguage`.
  - `recovery(detail)` / `responseRecovery(response)` turn a demo envelope or
    SSE terminal `recovery` key into its `demo.*` hint, shown in the nav
    status region. Unknown keys fall back to `demo.refresh_state`.
  - For `DEMO_SESSION_REQUIRED`, `DEMO_EXPIRED`, `DEMO_STALE_GENERATION` and
    `DEMO_STALE_ROLE` it stops registered pending work first, then dispatches
    `demo:state-check` `{reason:code}` once until the next authoritative
    event. It never dispatches the five authoritative events.
  - `registerStale(handler)`: pending work stops on a newer namespace
    (`demo:state`/`demo:role-changed`/`demo:reset-completed`),
    `demo:expired` or `demo:reset-started` of the same scope.
  - `current(captured, frame)`: work captures `storageKey('')` at start; a
    newer namespace or an SSE frame of another namespace is discarded (a
    foreign frame counts as `DEMO_STALE_GENERATION`).
  - `observe(kind, sessionId, captured, requestId)`: `demo:tour-observed` only
    for `tour_facts.journey_session_id` in the captured namespace.
  - `afterState(callback)`: after BFCache `pageshow`, demo waits for
    `demo:state` before the saved-run lookup.
  - htmx error responses get `detail.demoRecovery` before page handlers run.
- `chat.js`: the stale handler locks the chat with the hint (no login button,
  no `/login` navigation), closes the analysis modal and stops polling. A
  stale envelope on `ui.fetch` stops callers through their existing
  `AUTH_EXPIRED` path.
- `student-stream.js`, `mentor-stream.js`: abort controllers and recovery
  timers on stale; SSE terminal `recovery` hints; non-stale demo refusals show
  their hint and offer only an explicit resend of the same `request_id`.
  `pageshow` lookup waits for `demo:state` in demo.
- `analysis-result.js`: discards stale results and polling; refusals show the
  hint; `history_viewed` after a journey result renders (not comparisons).
- `sessions.js`: `admin_record_viewed` for the journey record; selected export
  sends `X-Demo-Export-Request-ID` and emits `csv_received` only after the
  echo, row count, UTF-8, RFC 4180 parse, 27+5 header, single journey
  session, provenance and summary-row checks pass. The download itself is
  unchanged.
- `ai-connections.js`, `admin-scenarios.js`, `scenario-editor.js`,
  `analysis-toggle.js`: demo refusals (`DEMO_ACTION_FORBIDDEN` etc.) show the
  hint; no retry or new UI path.
- Normal mode (no `#demo-config` or `mode:"normal"`): every helper returns
  null/true or does nothing, so names, headers, storage keys, 401 handling and
  language switching are unchanged.

## Checks

Base: `origin/main` `5e78f49`. Tested tree: the JS and test files of the
commit that adds this page; the worktree did not change during the runs.
Environment: WSL2 Linux, Node from `npm ci`, Python 3.12 venv for the fixture
server, Chromium (Playwright cache), local, serial, one worker.

| Check | Command | Result |
| --- | --- | --- |
| Syntax | `node --check` on the 10 changed JS files and the test | passed |
| New test, Red | same test against `origin/main` `static/js` | failed as expected at the first new case (410 hint not shown) |
| New test, Green | `npm run test:browser -- browser_demo_d3_03_recovery` | 1/1 passed |
| Ordered filter | `npm run test:browser -- demo_d3_03 student_recovery mentor_recovery chat sessions` | 10/10 passed, 180 s |
| Wide filter | `npm run test:browser -- student_stream mentor_stream s4_ ai_ s2_ s5_ scenario_editor admin_scenarios language i18n s6_delete` | 76/76 passed, 1046 s |
| Full pytest, full browser, live | not run locally | CI on the PR head (coordinator rule H1) |

`tests/browser_demo_d3_03_recovery.mjs` cases added in PR3:

1. Stream POST 410 `DEMO_EXPIRED`: chat locked with `demo.expired`, exactly
   one `demo:state-check {reason:"DEMO_EXPIRED"}`, no other `demo:*` event,
   no `/login`, no login button, one POST, no run lookup.
2. SSE `run.failed` with `recovery:"demo.limit_reached"`: hint shown, retry
   disabled, no state-check.
3. SSE terminal of an older generation: discarded, chat locked with
   `demo.stale_generation`, one state-check `DEMO_STALE_GENERATION`.
4. `demo:role-changed` during a running lookup: timer stopped, no further
   `/runs` request, `demo.stale_role` shown.
5. BFCache `pageshow`: no lookup until `demo:state`, then one GET lookup and
   no POST.
6. `history_viewed` for the journey analysis (exact detail, UUID
   `request_id`); none for another session.
7. `admin_record_viewed` from the admin sessions modal.
8. Selected export: `csv_received` with the echoed request ID for a valid
   one-session CSV; none for echo mismatch, wrong row count, another session
   or a broken quote.
9. `/admin/ai` 403 `DEMO_ACTION_FORBIDDEN`: hint shown, one request, no
   retry, no `demo:*` event.

## Review repair r1 (PR #243 review, 3× P1, 2× P2)

- Shared: `uiLanguage.capture()` returns a token `{prefix, serial, signal}`.
  Any invalidation bumps the serial and aborts every captured signal, so
  `current(token)` also fails after `demo:reset-started`/`demo:expired`,
  which keep the namespace. `receipt(token, data, requestId)` checks JSON
  run receipts: not historical, current namespace, and the request ID that
  was sent.
- P1-1 `analysis-result.js`: an invalidated view is dead. Its buttons are
  disabled, its requests aborted, nothing is sent, rendered, polled or
  observed afterwards, and the notice stays.
- P1-2: `chat.js` (`fetchWithAuthGuard` and its end/close callers),
  `ai-connections.js`, `scenario-editor.js`, `admin-scenarios.js` and
  `sessions.js` (modal, selected export) abort on invalidation and reject
  late successes before any DOM change, navigation, follow-up fetch or
  download.
- P1-3 `student-stream.js`, `mentor-stream.js`: JSON replays and lookups pass
  `receipt()` before every snapshot is applied.
- P2-4: the `analysis_busy` follow-up GET goes through `responseRecovery`
  (one state-check), clears the resend state and ends the view.
- P2-5 `sessions.js`: `csv_received` needs the full ordered 32-column header,
  and every row needs `prepared_example`, the current `demo_config` content
  and fixture versions, `external_ai_called=false` and `visitor_generated`.

Regression cases added: dead analysis view (no POST after role change),
first GET voided by reset-started, nested `analysis_busy` → 410, end POST
held across a role change, selected export held across a role change, AI
state GET held across a role change, student JSON receipts (other request,
other namespace, historical), mentor JSON receipt for another request, and
CSV negatives for prefix header, source, content and fixture version. The
positive CSV now carries `prepared_example` and the configured versions.

| Check (repair r1) | Command | Result |
| --- | --- | --- |
| New test, Red | same test against the pre-repair JS | failed as expected (`prefix header` CSV accepted) |
| New test, Green | `npm run test:browser -- browser_demo_d3_03_recovery` | 1/1 passed |
| Filtered suites | `npm run test:browser -- demo_d3_03 student_recovery mentor_recovery chat sessions student_stream mentor_stream s4_ ai_ s2_ s5_ scenario_editor admin_scenarios language i18n s6_delete` | 85/85 passed, 953 s |

## Review repair r2 (PR #243 review r2, P1 + P2)

- P1 `chat.js`: a close or end/analyze whose body is aborted by invalidation
  returns silently (no alert, no navigation), and the delayed close
  navigation checks `demoStopped` before it runs.
- P2 `student-stream.js`, `mentor-stream.js`: the post-decode attempt and
  receipt guards run only for demo captures (`capture()` has a signal only in
  demo); normal applies the decoded JSON exactly as before.

Regression cases: close body aborted after the headers arrived, close
invalidated while its 500 ms navigation is pending, and a normal-mode mentor
JSON result that settles as the chat locks (still applied). Each one failed
with only its own fix reverted.

| Check (repair r2) | Command | Result |
| --- | --- | --- |
| Red, per case | recovery test with HEAD `chat.js` / timer guard removed / HEAD stream files | 3 expected failures (alert, `/scenarios`, mentor result not applied) |
| Green and modules | `npm run test:browser -- demo_d3_03 student_stream mentor_stream student_recovery mentor_recovery` | 5/5 passed, 141 s |

## Not covered here

- Real demo server, CSRF middleware, `demo-tour.js` answering
  `demo:state-check`, `BroadcastChannel`: G4 #202.
- Screen captures: none taken in this PR, so `docs/demo/d3-03-evidence/` is
  not created yet.
- Stale-draft "previous draft" display with explicit copy (§25): old-namespace
  drafts are simply not restored (keys are namespaced since PR2); no copy UI
  was added.
