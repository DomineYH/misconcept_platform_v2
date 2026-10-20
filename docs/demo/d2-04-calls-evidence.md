# D2-04 demo calls G3 evidence (#198 PR4)

Gate: G3. Issue: #198 (D2-04), PR4. Tested code SHA:
`f0a0c2175bd111612109e1f8fa48d078c8136a5a` on `feat/198-g3` (base main
`1a40dc2`). A1 (`f969a3c9e849a4ea367eb6121d01a7b95299b4c4`) adds the
close-session fix; its rerun is in the A1 section. Commits that change only
this file change no code.

Contract binding: `d0-03-v1-r3`. The d0-03-v2 §31 note only removes the
`openai_helpers` / `session_feedback` guard requirement. It changes no wire
or manifest value.

| Manifest field | Value |
| --- | --- |
| contract_version | `d0-03-v1-r3` |
| content_version | `d0-02-v1` |
| fixture_version | `demo-fixture-v1` |
| supported_config_sha256 | `21dd6c4e9344a4ac150e9d2217c5d7eeb07a85fabc51123b60e5ba99894186c2` |

## Seed receipt

The seed was built by `python -m src.demo.seed build --out <tmp>/seed` in an
isolated temp directory at the tested SHA:

| Field | Value |
| --- | --- |
| source_commit | `f0a0c2175bd111612109e1f8fa48d078c8136a5a` |
| manifest_sha256 | `60cc355c4a5cee0cf1316bb97d8c52b09a03e39bb242514f66062e34159c0516` |
| db_schema_version | `037_demo_provider.sql` |
| seed_sha256 | `932d1305ae91cd4c731c7e47dcefc2367ccf3d8585dfd0036117bcd7fee8ae14` |
| logical_data_sha256 | `8d3fa9dde40a9d65273868a20271cdf767665e27c54384a54c74e575b3342980` |

`seed_sha256` changes on every build: the bcrypt salt is volatile by design
(§17). A build at `911640b` gave `ebd33186…3ba4` with the same
`logical_data_sha256`, which matches `SEED_LOGICAL_SHA256`. Every G3 run
builds its own seed through the same CLI and checks that the printed receipt
equals `receipt.json`.

## Environment

- Python 3.12.3, venv `<venv>`, `uv run --frozen`, serial pytest with 1
  worker.
- The runs used an ext4 copy of the worktree. Its tracked files equal HEAD;
  the only untracked file was this document.
- Why the copy: on the WSL drvfs checkout, each DemoProvider call re-verifies
  the 84 manifest files. That takes about 9 s there, against 0.08 s on ext4,
  so the demo student turn hit its 5 s `student_first_output` limit. CI runs
  on ext4.

## Commands (all fresh at the tested SHA)

| Command | Scope | Collected | Passed | Elapsed |
| --- | --- | --- | --- | --- |
| `pytest -q tests/test_demo_d2_04_g3.py` | G3 flow, reset race, egress | 2 | 2 | 100 s |
| `pytest -q tests/test_demo_d2_04_calls.py` | student/mentor calls, s2r, plan estimate | 148 | 148 | 178 s |
| `pytest -q tests/test_demo_d2_04_analysis.py` | analysis ctx/guard/retry/provenance | 21 | 21 | 67 s |
| `pytest -q tests/test_demo_d2_03_seed.py` | seed build/verify | 25 | 25 | 81 s |
| `pytest -q tests/test_demo_d1_04_app.py` | demo app wiring | 23 | 23 | 358 s |
| `pytest -q tests/test_student_lifecycle.py tests/test_mentor_lifecycle.py` | stream lifecycle | 19 | 19 | 67 s |
| `pytest -q tests/test_context_budget.py tests/test_mentor_context_budget.py tests/test_student_context_budget.py tests/test_analysis_plan.py tests/test_analysis_plan_api.py tests/test_analysis_runs.py` | normal budget/plan/runs | 106 | 106 | 294 s |
| `pytest -q tests/test_demo_d3_02_guards.py` | §20 guards incl. demo analysis | 13 | 13 | 132 s |
| `ruff check .` / `black --check .` | lint, format | n/a | pass | n/a |
| `npm run test:browser -- student_stream mentor_stream` (heavy lock) | normal SSE in the browser | 2 | 2 | 25 s |

Each pytest command runs as `uv run --frozen python -m pytest -p
no:cacheprovider`. The browser run regenerated 4 tracked PNGs under
`tests/uiux/evidence/122/`; they were restored and are not committed.

Red evidence (`911640b`, before the analysis fix): `test_g3_flow_on_real_seed`
failed at the analysis step with `latest_run.status == "failed"` and
`error_code == "configuration_unavailable"`. `test_g3_reset_during_stream`
passed.

A1 rerun at `f969a3c` (fresh; same setup):

| Command | Collected | Passed | Elapsed |
| --- | --- | --- | --- |
| `pytest -q tests/test_demo_d2_04_g3.py` | 3 | 3 | 24 s |
| `pytest -q tests/test_student_lifecycle.py tests/test_mentor_lifecycle.py` | 19 | 19 | 26 s |
| `pytest -q tests/test_deleted_session_access.py tests/test_lesson_snapshots.py tests/test_mentor_security.py tests/test_scenario_api.py tests/test_student_failures.py tests/test_student_security.py tests/test_teacher_error_keys.py` (close_session) | 155 | 155 | 159 s |
| `pytest -q tests/test_demo_d2_04_analysis.py tests/test_demo_d3_02_guards.py` | 34 | 34 | 88 s |
| `ruff check .` / `black --check .` | n/a | pass | n/a |
| `npm run test:browser -- student_stream mentor_stream` (heavy lock) | 2 | 2 | 16 s |

Red at `d802bb7` + the new test: `test_g3_close_during_stream_cancels_it`
timed out waiting for the held stream.

## Acceptance

Test ids are in `tests/test_demo_d2_04_g3.py` unless noted.

| Acceptance | Contract | Test | Result |
| --- | --- | --- | --- |
| Student → mentor → end → analysis completes on the real seed and a real visitor DB through the real routes (`/sessions`, `/turns/stream`, `/mentor/stream`, `/end`, `/analysis/runs/{id}`, `/analysis`) | §27 G3, §6, §16 | `test_g3_flow_on_real_seed` | PASS |
| Final saves exist only in the visitor's DB: student and mentor runs `completed`, analysis run `ok` and adopted, messages linked, session ended, no rows in the other visitor's DB | §6 | `test_g3_flow_on_real_seed` | PASS |
| Ledger: one row per call (attempt 1), provider `demo`, `completed`, every usage, cost and pricing column null, `usage_complete` false | §19 | `test_g3_flow_on_real_seed` | PASS |
| Provenance `prepared_example`, `external_ai_called=false` and `visitor_generated` on the SSE terminal, `/runs/{id}`, the analysis run and the analysis JSON | §19 | `test_g3_flow_on_real_seed` | PASS |
| Reset during a held student stream: the stream ends with a body-free `run.*` terminal, the run row in the old DB is unchanged, it has no message or finished ledger row, the new generation has no such run, and cleanup removes the old DB without recreating it | §6, §7, C06 | `test_g3_reset_during_stream` | PASS |
| Egress 0: SDK constructors (OpenAI/Anthropic/Gemini), the httpx and requests transports, `socket.connect`, `connect_ex` and `create_connection` all stay at 0 from before startup until after shutdown and cleanup. This covers every §20 forbidden POST (403 `DEMO_ACTION_FORBIDDEN`) plus the probe request for the seed model, and those leave 0 probe, comparison and external ledger rows. A canary proves each spy fires before the window opens | §14, §20, C14, C20 | both tests | PASS |
| Closing a demo session during a held stream cancels that stream in the visitor scope: `/sessions/{id}/close` returns 200, the stream ends with a non-completed `run.*` terminal without being released, the run is `cancelled` with no message, and egress stays 0 | §6, C05 | `test_g3_close_during_stream_cancels_it` (A1) | PASS |
| The real `supported_config_sha256` from `src/services/model_configuration.py` is used; mismatches come from the manifest, not a faked helper | §16.1 | `tests/test_demo_d2_04_calls.py::test_s2r_*` | PASS |
| A demo analysis plan estimate needs no SDK adapter | §14/§16 | `tests/test_demo_d2_04_calls.py::test_s2_demo_analysis_plan_estimate_needs_no_sdk_adapter` | PASS |

## Defects found by G3 and fixed (Red first)

1. `context_budget.provider_parameters` raised `KeyError: 'demo'` from
   `analysis_plan.estimate_request`, so `/end` returned 503. Fixed in
   `1223daf`.
2. Nothing wrote `execution["demo_adapter"]`, so each demo analysis reached
   `admit_call` without ctx and failed `configuration_unavailable`. Fixed in
   `f0a0c21`: the route builds the adapter and `request_analysis` passes it
   on. `analysis_runs.py` was edited under coordinator approval.
3. `close_session` (`sessions.py`) ended the session without the demo ctx,
   so a live demo stream was never signalled. Fixed in `f969a3c` (A1): it
   now uses `demo_scope` and `guarded_db`, as `end_session` does.

## Not run / untested

- Full pytest suite: CI on the PR head, per the testing policy.
- `tests/check_student_live.py`: not run. No student stream file changed.
- `/sessions/{id}/analyze` (manual re-analysis) in demo: not driven by G3.
  It gets the same adapter wiring as `/end`.
- Admin `analyze_regenerate` in demo has no demo adapter. That is #200's
  file and the coordinator has routed it there.

## Note on stubs

스텁 통과는 G3로 표시하지 않는다. Stub passes are not G3 evidence.
`tests/test_demo_d2_04_analysis.py` still uses the provider fixture's hash
stub from `tests/test_demo_provider.py`, because its fictional config is not
a valid `ScenarioConfig`. The real helper and the real seed are exercised in
`tests/test_demo_d2_04_g3.py`.
