# D3-02 admin guard and export evidence (#200 PR4)

Contract: `d0-03-v1-r3` semantics (wire `contract_version` `d0-03-v1-r3`),
§8, §20–§23, §24.1, §25, §26. Policy table: [d3-02-endpoint-policy.md](d3-02-endpoint-policy.md).

This page records the PR4 re-run on the real demo stack, after #194 S1–S4
and #197 were merged. It is #200 evidence only; the final G4 acceptance
belongs to #202.

## 1. Tested revision and stack

- Tested SHA: `881d56311084f139e5d0f05d394b39a5ba683204` for all code and
  every suite, plus `e4f349f0280b38f87d14942b6c1e865cffa32da7` for the
  guards file. `e4f349f` changes only that file: a stub in the stand-in
  seed analysis case (§8).
  - Base: main `fbbcad7`, which includes #198 PR4 (#251) and #250 (D2 seed
    provenance).
  - On top of that base: `tests/test_demo_d3_02_real.py`, the D3 fix (§7)
    and the D4 fix (§8).
  - The tree was clean during the runs, and the docs commits change no code
    or tests.
  - Real-server runs used an ext4 copy of the same commit (`git clone
    --shared`).
- Earlier runs:
  - `56b14d4`, after the D3 fix.
  - `aa6f172`, the first PR4 run.
  - `f571d9d`, after repair r1 added the seed-session deletion block to the
    `[exports]` case.
- Environment: CPython 3.12.3, own uv venv, `TMPDIR` outside `/tmp`, serial
  pytest, 1 worker.
- The real stack in `tests/test_demo_d3_02_real.py`:
  - The seed is built by #197's CLI, `python -m src.demo.seed build --out <tmp>/seed`.
  - The app boots in a clean subprocess: `APP_MODE=demo`, `TESTING=false`,
    `ENV=production`, its own demo secrets, and no provider keys in the env.
  - `demo_seed()` → `load_seed(DEMO_SEED_DIR)` → `validate_seed` is the real
    code path, with no stand-in module. The case asserts
    `app.state.demo_seed.path` and `demo_request_db.seed_refs == manifest.seed_refs`.
  - Every request goes through the real `/demo/start` bootstrap, the demo
    session cookie, the CSRF check (`demo_csrftoken` + `x-csrf-token` +
    `Origin`), namespace query parameters, and a per-visitor DB file.
  - No FastAPI dependency overrides and no monkeypatched config.
- One config value differs from the default: `DEMO_REQUESTS_PER_MINUTE=600`,
  the config maximum. Each case sends more than the default 60 requests a
  minute, and with the default the per-visitor limiter answered 429.

### Seed receipt (build at `e4f349f`)

| field | value |
| --- | --- |
| `source_commit` | `e4f349f0280b38f87d14942b6c1e865cffa32da7` |
| `manifest_sha256` | `60cc355c4a5cee0cf1316bb97d8c52b09a03e39bb242514f66062e34159c0516` |
| `db_schema_version` | `037_demo_provider.sql` |
| `logical_data_sha256` | `93808c612ae6496a31f100dde1ce5c6780a3a6fd8cd136b82bc871eb6d746141` (#250 changed the seed provenance; it was `8d3fa9dd…` before #250) |
| `seed_sha256` | `4111bdf108301047c8c8d501c6b12a5f78aeb6d44d9a8b232d8b2108b1c33842` (changes on every build because password hashes are volatile) |
| `seed_refs` | admin 1, teacher 2, group 1, scenario 1, completed_session 1, model 1, connection 4 |
| versions | content `d0-02-v1`, fixture `demo-fixture-v1`, seed `demo-seed-v1` |

The receipt's `command` field holds a host path and is left out here.

## 2. Commands and counts

Command per file: `<venv>/bin/python -m pytest -q -p no:cacheprovider tests/<file>.py`.
`test_demo_d3_02_real.py` ran under `flock /tmp/mp2-heavy.lock`, with
`D3_02_EVIDENCE_DIR` set so that it also saved the CSV samples.

| file | revision | fresh/reused | collected | passed | failed/skipped | pytest time |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `tests/test_demo_d3_02_real.py` | `881d563` | fresh, ext4 | 3 | 3 | 0 | 26 s |
| `tests/test_demo_d3_02_guards.py` | `e4f349f` | fresh, ext4 | 15 | 15 | 0 | 67 s |
| `tests/test_demo_d3_02_exports.py` | `881d563` | fresh, ext4 | 39 | 39 | 0 | 32 s |
| `tests/test_analysis_runs.py` | `881d563` | fresh, ext4 | 17 | 17 | 0 | 37 s |
| `tests/test_analysis_concurrency.py` | `881d563` | fresh, ext4 | 10 | 10 | 0 | 25 s |
| `tests/test_analysis_retry.py` | `881d563` | fresh, ext4 | 4 | 4 | 0 | 8 s |
| `tests/test_regressions.py` | `881d563` | fresh, ext4 | 5 | 5 | 0 | 8 s |
| `tests/test_admin_logic.py` | `881d563` | fresh, ext4 | 1 | 1 | 0 | 4 s |
| `tests/test_deleted_session_access.py` | `881d563` | fresh, ext4 | 24 | 24 | 0 | 25 s |
| `tests/test_legacy_session_history.py` | `881d563` | fresh, ext4 | 22 | 22 | 0 | 53 s |
| scenario suites (`test_scenario_api/drafts/publication/screens/retirement`) | `56b14d4` | reused: the scenario route is unchanged since | 198 | 198 | 0 | see D3 run |
| `tests/test_demo_d1_04_app.py` | `aa6f172` | reused: the file is unchanged; the admin analysis routes are not exercised there | 23 | 23 | 0 | 349 s |

- On `881d563`, the guards file failed 1 case, `[analysis]`: the stand-in
  seed module has no `INPUTS`, and the admin routes now build the demo
  adapter. `e4f349f` fixes the test (§8), and guards then passed 15/15.
- On `e4f349f`, `ruff check .` passed and `black --check .` reported 392
  files unchanged.
- The CSV samples written by the `881d563` run are byte-identical to the
  ones committed here.

## 3. Acceptance → test → result

Key: `R:guards` = `test_demo_d3_02_real.py::test_demo_d3_02_on_real_seed_and_middleware[guards]`,
`R:exports` = the `[exports]` case, `R:admin_analysis` = the `[admin_analysis]` case. Only rows whose test id is `R:guards`,
`R:exports` or `R:admin_analysis` were proven on the CLI-built product seed with real
middleware. The last three rows give their own scope: synthetic seed,
in-process unit, or patched config.

| acceptance (§) | real-stack check | test id | result |
| --- | --- | --- | --- |
| §20 refused POSTs | all 24 `FORBIDDEN` paths (provider key/enabled/delete/catalog ×3, models, model update, probes, AI settings, comparisons, comparison retry, conversations create/turn/adopt/mentor/end/child retry), each with `{}` and a plausible body → 403 `DEMO_ACTION_FORBIDDEN`. The API key is never echoed. Row counts of `provider_connection`, `provider_audit_log`, `model_config`, `model_probe`, `comparison_run`, `generation_run`, `api_usage_log`, `app_setting` are unchanged. 0 non-UNIX socket connects, 0 reauth limiter/password calls | R:guards | pass |
| CSRF before guard/CRUD | `POST /admin/groups` and a forbidden POST are sent with: no token, a forged token, a foreign `Origin`, and no `Origin`. All four get 403 `DEMO_CSRF_INVALID`, and the group count is unchanged. A control request with valid headers creates a group (201) | R:guards | pass |
| §20 reads | `/admin/ai/state` returns 200 with §19 provenance (`seed_prepared`). Comparison and conversation lists are empty. Missing probe, comparison and conversation IDs → 404 `DEMO_RESOURCE_NOT_FOUND` | R:guards | pass |
| §20 stop/cancel/delete | probe cancel, comparison stop/panel stop, conversation stop, and both DELETEs on absent IDs → 404 `DEMO_RESOURCE_NOT_FOUND` (no foreign-scope cancel) | R:guards | pass |
| §20 scenario API model injection | three injected selections on all three roles: an unregistered model 99 on the openai connection; the demo row relabelled `openai/gpt-5.5`; the demo model on the openai connection. Each is sent with `save_draft` and with `publish`, to both `POST /admin/scenarios` and `/admin/scenarios/1/update`, and all 12 requests → 422 `DEMO_UNSUPPORTED_CONFIGURATION`. The scenario rows (id, version, config) and the 8 run/ledger/usage table counts are unchanged | R:guards | pass (Red before the fix: field-error list, §7) |
| normal scenario errors unchanged | in normal mode, `save_selected` re-raises the identical `model_identity_mismatch`/`model_unavailable` field-error exception | `test_demo_d3_02_guards.py::test_normal_scenario_save_keeps_model_field_errors[*]` | pass |
| teacher refusal | after `/demo/role` → teacher, a forbidden POST and `GET /admin/ai/state` → 403 `DEMO_ACTION_FORBIDDEN` | R:guards | pass |
| §23 single CSV | seed session download: filename `demo_d0-02-v1_admin_session-1.csv` (ASCII), UTF-8 BOM, parsed with `csv.DictReader`. 27 + 5 = 32 columns in order. 6 rows: teacher 2, student 2, tutor 1, summary 1. Suffix values `prepared_example`/`false`/`d0-02-v1`/`demo-fixture-v1`/`seed_prepared`. `X-Demo-Export-Request-ID` is echoed, `X-Demo-Export-Row-Count` is 6 | R:exports | pass |
| §23 selected/all/user CSV | selected `[seed, visitor]` → `..._selected.csv` with both sessions, origins `seed_prepared`/`visitor_generated`, echo and row count. `..._all.csv` and `..._user-2.csv` → both sessions, no `x-demo-*` headers | R:exports | pass |
| §23/§25 other visitor | visitor B's all export holds only the seed session and none of A's visitor text. B's download of A's session ID → 404 `DEMO_RESOURCE_NOT_FOUND` | R:exports | pass |
| §22 session soft delete | A deletes its visitor session → 200; a second delete → 400 (existing rule). Download and analysis API → 404 `DEMO_RESOURCE_NOT_FOUND`. The detail modal returns 200 as a tombstone without the body. All, user, and selected exports exclude the session; selected with only the deleted ID returns no CSV. The row is kept with `deleted_at` set | R:exports | pass |
| §22 analyzed session soft delete | visitor B deletes seed session 1. The session has a stored `session_feedback_report` and a completed `operation='analysis'` run, and before the delete B's all export carries report text. After the delete: analysis modal, analysis JSON, analysis-run status and download each → 404 `DEMO_RESOURCE_NOT_FOUND`. The detail tombstone, the all export, the user-2 export and the selected export contain no report text and no message text, and selected returns no CSV. The row is tombstoned in B's DB only; A's copy stays live. Before the delete: analysis JSON and analysis-run status → 200 with §19 provenance `seed_prepared`, the run is `ok`/adopted, and the analysis modal → 200 with `data-record-origin="seed_prepared"` | R:exports | pass |
| §14/§16/§19 admin regenerate (D4) | `POST /admin/sessions/1/analyze_regenerate` on the analyzed seed session → 202. The run finishes `ok`/adopted on the local adapter; `outcome.provenance` has `mode=demo` and `external_ai_called=false`, and analysis JSON shows the same provenance. Exactly one attempt-1 `api_usage_log` row is added, with provider `demo`, status `completed` and every usage field null. A replay returns the same `run_id` and adds no rows. A `request_id` already used by an admin-owned non-analysis run → 409 `DEMO_REQUEST_CONFLICT` with no rows | R:admin_analysis | pass (Red before the fix: run `failed`/`configuration_unavailable`) |
| §14/§19 admin end (D4) | a teacher starts a visitor session and runs one local student turn. The admin then calls `POST /admin/sessions/{id}/end` with a request ID → 202. The run is `ok`/adopted, with provenance `visitor_generated` and one null-usage ledger row | R:admin_analysis | pass (Red before the fix: `configuration_unavailable`) |
| §22 teacher delete | delete of seed teacher 2 → `{"status":"deleted"}`. Sessions are kept with `teacher_id` NULL, and the seed session keeps its 5 messages. User export of ID 2 → 404 `DEMO_RESOURCE_NOT_FOUND`. All export still has 6 rows with an empty `teacher_id` | R:exports | pass |
| §24.1 delete facts | `/demo/state?include_tour=true` for A: `teacher_deleted` true, `session_deleted` true, `journey_session_id` = the deleted visitor session. For B: both false, and B's seed session still has teacher 2 | R:exports | pass |
| §21/§8 quotas | groups/contributors/users/bulk/scenario quotas and the last-slot race, on the real app and middleware. `scenario_race` uses the real product seed; the other cases use the synthetic `make_seed` behind the real `demo_seed()` | `test_demo_d3_02_guards.py::test_demo_allowed_crud_quotas[*]` | pass (13/13 in file) |
| §26 reauth | `reauthenticate` fails closed before the limiter and the password check (in-process unit) | `test_demo_d3_02_guards.py::test_demo_reauthenticate_fails_closed_before_limiter_and_password` | pass |
| §23 normal unchanged, exporter units | exporter suffix/normal parity and formula/multiline round trip (in-process, demo config patched on the route module) | `test_demo_d3_02_exports.py` | pass (39/39) |

## 4. CSV samples (`d3-02-evidence/`)

Written by the `[exports]` case from the real app responses. All content is
fictional seed text. The files hold no keys, secrets or host paths (checked
with grep).

| file | from | rows | sessions | origins |
| --- | --- | ---: | --- | --- |
| `single_session-1.csv` | A, `/admin/sessions/1/download` | 6 | 1 | seed_prepared |
| `selected_seed_and_visitor.csv` | A, `/admin/sessions/export-selected` | 11 | 1, 2 | seed_prepared, visitor_generated |
| `other_visitor_all.csv` | B, `/admin/sessions/export` | 6 | 1 | seed_prepared |
| `after_deletes_all.csv` | A after both deletes, `/admin/sessions/export` | 6 | 1 (`teacher_id` empty) | seed_prepared |

Visitor session 2 was made by copying the seed session row and its 5
messages, each prefixed `VISITOR-ONLY `, into A's visitor DB with SQLite.
The summary row was not copied, so its export has 5 rows. A real student
dialogue would go through #198's admission path, which is out of #200 scope.

## 5. Not run, with reasons

- Full pytest, full browser suite, and `tests/check_student_live.py`: full
  regression is CI on the PR head. PR4 changes no templates or JS. The product changes are the demo branches in `admin_scenarios.py` (§7) and `admin_session_actions.py` (§8).
- Teacher CSV export (`session_analysis.py`, 20 + 5 columns): the route is owned by #198.
- `test_s4_csv.py`, `test_export_deleted_teacher.py` and the other admin
  suites: not re-run locally. The product changes are in the scenario and
  admin analysis routes, and their suites are listed in §2. They are covered by CI.
- Visitor quota cases other than `scenario_race` still use the synthetic
  seed. This was not repeated on the product seed, because the quota code
  does not read seed content beyond row counts.
- Two-process or real-socket server (uvicorn): the cases use ASGI in-process
  transport inside a clean subprocess, with the same app object and middleware stack.

## 6. Product defect D1 (#198), resolved by #198 PR4 (#251)

The text below records the state before #251. After #251 merged, the
pre-delete reads return 200, and `R:exports` asserts them (§3).


On the real demo stack, every admin analysis read of the analyzed seed
session returns 503 `DEMO_CONFIGURATION_INVALID`. The demo error boundary
logs the cause as `KeyError`. Affected requests:

- `GET /admin/sessions/1/detail`
- `GET /admin/sessions/1/analysis_modal`
- `GET /admin/sessions/1/analysis`
- `GET /admin/sessions/1/analysis/runs/<run>`

Reproduced in-process on a copy of the seed DB. The call path:

1. `admin_session_actions.py` (via `_load_analysis_response`) or `analysis_runs.py:82` `run_response`
2. `src/services/analysis_results.py:395` `load_plan`
3. `src/services/analysis_plan.py:375` `build_plan`
4. `:182` `estimate_request`
5. `:67` → `src/services/context_budget.py:13` `PROVIDER_ADAPTERS[request.provider]`

`src/services/call_execution.py:33` `PROVIDER_ADAPTERS` has no `"demo"`
entry, but the seed's analysis model uses provider `demo`. These files are
not in #200's §26 list; `analysis_results.py` and `call_execution.py` are
#198's. For that reason, this PR does not assert that the analysis is
readable before the delete.

The post-delete 404 checks run before that code, so they still catch a
missing deleted check. Mutation: with `admin_session_actions.py:285` reduced
to `if not session:`, the `[exports]` case fails (modal 503 instead of 404).
The file was restored.

## 7. Product fix D3 (`admin_scenarios.py`, #200-owned)

- **Defect** (found by #203): in demo, scenario create and update with an
  external model returned 422 field errors
  `[{"path":"config.<role>.resolved_model_config","code":"model_identity_mismatch"}]`
  from `src/services/scenario_drafts.py:137`. Contract §20 (d0-03 l.520)
  requires 422 `DEMO_UNSUPPORTED_CONFIGURATION` at the selection step.
- **Fix:** both routes now call `save_selected`. In demo it maps a
  `model_identity_mismatch` or `model_unavailable` refusal from
  `save_scenario` to `DemoError("UNSUPPORTED_CONFIGURATION")`. In normal mode
  it re-raises the same exception, so normal responses do not change.
  `scenario_drafts.py` is not changed.
- **No rows:** the demo create still runs inside the quota write guard. The
  refusal happens before any insert and rolls back.
- **Red:** before the fix, the new `[guards]` block failed on the first
  request, because `detail` was the field-error list (log `d3-red.log`).

## 8. Product fix D4 (`admin_session_actions.py`, #200-owned)

- **Defect** (found by #203 and by this PR): in demo, admin
  `POST /admin/sessions/{id}/end` and `/analyze_regenerate` reserved
  analysis without the session's local adapter, so the run ended
  `failed`/`configuration_unavailable`. Cause: `analysis_context(request)`
  returned only ctx/registry.
- **Fix:**
  - `analysis_context(request, session=None)` returns #198's
    `demo_analysis(request, session)` (ctx, registry and `demo_adapter`)
    when the request has a demo context and a session is given.
  - Both reservations call `reserve_analysis`, a late-import wrapper of
    #198's `reserve_analysis_run`, so §16 conflicts get the
    `DEMO_REQUEST_CONFLICT` envelope.
  - In demo, regenerate loads the session first; missing or deleted →
    `DEMO_RESOURCE_NOT_FOUND`.
  - Normal mode passes the same ctx/registry as before, and
    `reserve_analysis_run` re-raises unchanged when there is no demo context.
  - The late imports avoid an import cycle: `session_analysis` →
    `admin_session_export` → this module.
- **Red:** `d3-red`-style logs `a2/red.log` (regenerate) and
  `a2/d4-red.log` (admin end). Both runs ended `configuration_unavailable`.
- **Test-only change** `e4f349f`: the guards `[analysis]` case runs on the
  stand-in seed module, which has no fixture `INPUTS`. Its existing stubs of
  `reserve_analysis`/`run_response` now include a stub of
  `session_analysis.demo_binding`. The real adapter path is proven by
  `R:admin_analysis` on the product seed.

Product files changed in this PR: `src/api/routes/admin_scenarios.py` (D3)
and `src/api/routes/admin_session_actions.py` (D4).
