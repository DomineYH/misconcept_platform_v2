# D3-02 admin guard and export evidence (#200 PR4)

Contract: `d0-03-v1-r3` semantics (wire `contract_version` `d0-03-v1-r3`),
§8, §20–§23, §24.1, §25, §26. Policy table: [d3-02-endpoint-policy.md](d3-02-endpoint-policy.md).

This page records the PR4 re-run on the real demo stack, after #194 S1–S4
and #197 were merged. It is #200 evidence only; the final G4 acceptance
belongs to #202.

## 1. Tested revision and stack

- Tested SHA: `aa6f172b306906bc64a63704497a9d3896b9be14` (main `1a40dc2` plus
  `tests/test_demo_d3_02_real.py`). The tree was clean during the run. The
  docs commit that adds this page changes no code or tests.
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

### Seed receipt (build at the tested SHA)

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

## 2. Commands and counts (fresh, on `aa6f172`)

Command per file: `<venv>/bin/python -m pytest -q -p no:cacheprovider tests/<file>.py`.
`test_demo_d3_02_real.py` ran under `flock /tmp/mp2-heavy.lock`, with
`D3_02_EVIDENCE_DIR` set so that it also saved the CSV samples.

| file | collected | passed | failed/skipped | pytest time |
| --- | ---: | ---: | ---: | ---: |
| `tests/test_demo_d3_02_real.py` | 2 | 2 | 0 | 152 s |
| `tests/test_demo_d3_02_guards.py` | 13 | 13 | 0 | 289 s |
| `tests/test_demo_d3_02_exports.py` | 39 | 39 | 0 | 151 s |
| `tests/test_demo_d1_04_app.py` | 23 | 23 | 0 | 349 s |

Also: `ruff check .` passed, and `black --check .` reported 390 files unchanged.

## 3. Acceptance → test → result

Key: `R:guards` = `test_demo_d3_02_real.py::test_demo_d3_02_on_real_seed_and_middleware[guards]`,
`R:exports` = the `[exports]` case. "Real seed" means that every row in
this table was proven on the CLI-built product seed with real middleware.

| acceptance (§) | real-stack check | test id | result |
| --- | --- | --- | --- |
| §20 refused POSTs | all 24 `FORBIDDEN` paths (provider key/enabled/delete/catalog ×3, models, model update, probes, AI settings, comparisons, comparison retry, conversations create/turn/adopt/mentor/end/child retry), each with `{}` and a plausible body → 403 `DEMO_ACTION_FORBIDDEN`. The API key is never echoed. Row counts of `provider_connection`, `provider_audit_log`, `model_config`, `model_probe`, `comparison_run`, `generation_run`, `api_usage_log`, `app_setting` are unchanged. 0 non-UNIX socket connects, 0 reauth limiter/password calls | R:guards | pass |
| CSRF before guard/CRUD | `POST /admin/groups` and a forbidden POST are sent with: no token, a forged token, a foreign `Origin`, and no `Origin`. All four get 403 `DEMO_CSRF_INVALID`, and the group count is unchanged. A control request with valid headers creates a group (201) | R:guards | pass |
| §20 reads | `/admin/ai/state` returns 200 with §19 provenance (`seed_prepared`). Comparison and conversation lists are empty. Missing probe, comparison and conversation IDs → 404 `DEMO_RESOURCE_NOT_FOUND` | R:guards | pass |
| §20 stop/cancel/delete | probe cancel, comparison stop/panel stop, conversation stop, and both DELETEs on absent IDs → 404 `DEMO_RESOURCE_NOT_FOUND` (no foreign-scope cancel) | R:guards | pass |
| teacher refusal | after `/demo/role` → teacher, a forbidden POST and `GET /admin/ai/state` → 403 `DEMO_ACTION_FORBIDDEN` | R:guards | pass |
| §23 single CSV | seed session download: filename `demo_d0-02-v1_admin_session-1.csv` (ASCII), UTF-8 BOM, parsed with `csv.DictReader`. 27 + 5 = 32 columns in order. 6 rows: teacher 2, student 2, tutor 1, summary 1. Suffix values `prepared_example`/`false`/`d0-02-v1`/`demo-fixture-v1`/`seed_prepared`. `X-Demo-Export-Request-ID` is echoed, `X-Demo-Export-Row-Count` is 6 | R:exports | pass |
| §23 selected/all/user CSV | selected `[seed, visitor]` → `..._selected.csv` with both sessions, origins `seed_prepared`/`visitor_generated`, echo and row count. `..._all.csv` and `..._user-2.csv` → both sessions, no `x-demo-*` headers | R:exports | pass |
| §23/§25 other visitor | visitor B's all export holds only the seed session and none of A's visitor text. B's download of A's session ID → 404 `DEMO_RESOURCE_NOT_FOUND` | R:exports | pass |
| §22 session soft delete | A deletes its visitor session → 200; a second delete → 400 (existing rule). Download and analysis API → 404 `DEMO_RESOURCE_NOT_FOUND`. The detail modal returns 200 as a tombstone without the body. All, user, and selected exports exclude the session; selected with only the deleted ID returns no CSV. The row is kept with `deleted_at` set | R:exports | pass |
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

Product defects found: none. No product file was changed.
