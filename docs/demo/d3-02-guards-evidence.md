# D3-02 admin guard and export evidence (#200 PR4)

Contract: `d0-03-v1-r3` semantics (wire `contract_version` `d0-03-v1-r3`),
§8, §20–§23, §24.1, §25, §26. Policy table: [d3-02-endpoint-policy.md](d3-02-endpoint-policy.md).

This page records the PR4 re-run on the real demo stack, after #194 S1–S4
and #197 were merged. It is #200 evidence only; the final G4 acceptance
belongs to #202.

## 1. Tested revision and stack

- Tested SHA: `56b14d42cfe63267ac150f69e89c5640ed22630b`. This is main
  `1a40dc2` plus `tests/test_demo_d3_02_real.py`, the repair r1 case, and the
  D3 fix in `src/api/routes/admin_scenarios.py` (§7). The tree was clean
  during the run, and the docs commits change no code or tests.
- Earlier runs:
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

### Seed receipt (build at `aa6f172`; later commits change no seed input)

| field | value |
| --- | --- |
| `source_commit` | `aa6f172b306906bc64a63704497a9d3896b9be14` |
| `manifest_sha256` | `60cc355c4a5cee0cf1316bb97d8c52b09a03e39bb242514f66062e34159c0516` |
| `db_schema_version` | `037_demo_provider.sql` |
| `logical_data_sha256` | `8d3fa9dde40a9d65273868a20271cdf767665e27c54384a54c74e575b3342980` |
| `seed_sha256` | `01f484a81e3f1b82eddbfdf1e618a0de8cb6e8c1fedbae58d81acc86f745abe1` (changes on every build because password hashes are volatile; a build at `1a40dc2` gave a different file hash and the same logical hash) |
| `seed_refs` | admin 1, teacher 2, group 1, scenario 1, completed_session 1, model 1, connection 4 |
| versions | content `d0-02-v1`, fixture `demo-fixture-v1`, seed `demo-seed-v1` |

The receipt's `command` field holds a host path and is left out here.

## 2. Commands and counts

Command per file: `<venv>/bin/python -m pytest -q -p no:cacheprovider tests/<file>.py`.
`test_demo_d3_02_real.py` ran under `flock /tmp/mp2-heavy.lock`, with
`D3_02_EVIDENCE_DIR` set so that it also saved the CSV samples.

| file | revision | fresh/reused | collected | passed | failed/skipped | pytest time |
| --- | --- | --- | ---: | ---: | ---: | ---: |
| `tests/test_demo_d3_02_real.py` | `56b14d4` | fresh | 2 | 2 | 0 | 66 s |
| `tests/test_demo_d3_02_guards.py` | `56b14d4` | fresh | 15 | 15 | 0 | 104 s |
| `tests/test_scenario_api.py` | `56b14d4` | fresh | 30 | 30 | 0 | 16 s |
| `tests/test_scenario_drafts.py` | `56b14d4` | fresh | 72 | 72 | 0 | 55 s |
| `tests/test_scenario_publication.py` | `56b14d4` | fresh | 48 | 48 | 0 | 84 s |
| `tests/test_scenario_screens.py` | `56b14d4` | fresh | 25 | 25 | 0 | 3 s |
| `tests/test_scenario_retirement.py` | `56b14d4` | fresh | 23 | 23 | 0 | 32 s |
| `tests/test_demo_d3_02_exports.py` | `aa6f172` | reused: file unchanged; the D3 fix touches only scenario routes | 39 | 39 | 0 | 151 s |
| `tests/test_demo_d1_04_app.py` | `aa6f172` | reused: file unchanged; the D3 fix touches only scenario routes | 23 | 23 | 0 | 349 s |

On `56b14d4`, `ruff check .` passed and `black --check .` reported 390 files
unchanged. The CSV samples written by the `56b14d4` run are byte-identical
to the ones committed here.

## 3. Acceptance → test → result

Key: `R:guards` = `test_demo_d3_02_real.py::test_demo_d3_02_on_real_seed_and_middleware[guards]`,
`R:exports` = the `[exports]` case. Only rows whose test id is `R:guards`
or `R:exports` were proven on the CLI-built product seed with real
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
| §22 analyzed session soft delete | visitor B deletes seed session 1. The session has a stored `session_feedback_report` and a completed `operation='analysis'` run, and before the delete B's all export carries report text. After the delete: analysis modal, analysis JSON, analysis-run status and download each → 404 `DEMO_RESOURCE_NOT_FOUND`. The detail tombstone, the all export, the user-2 export and the selected export contain no report text and no message text, and selected returns no CSV. The row is tombstoned in B's DB only; A's copy stays live. The readable-before-delete API check is not run: see §6 | R:exports | pass (pre-delete API read blocked, §6) |
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
  regression is CI on the PR head. PR4 changes no product code, templates or JS.
- Teacher CSV export (`session_analysis.py`, 20 + 5 columns): the route is owned by #198.
- `test_s4_csv.py`, `test_export_deleted_teacher.py` and the other admin
  suites: not re-run locally because no product file changed. They are covered by CI.
- Visitor quota cases other than `scenario_race` still use the synthetic
  seed. This was not repeated on the product seed, because the quota code
  does not read seed content beyond row counts.
- Two-process or real-socket server (uvicorn): the cases use ASGI in-process
  transport inside a clean subprocess, with the same app object and middleware stack.

## 6. Product defect found (not #200-owned; NEEDS_DECISION)

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

The only product file changed in this PR is `src/api/routes/admin_scenarios.py` (D3).
