# D4-01 격리·보안·경합 검사 evidence (#203)

계약: `d0-03-v1-r3` 의미(문서 `d0-03-v2`). 최종 acceptance는 #202(G4)가 고정한 통합 SHA에서의 실행분만 인정한다(계약 §27/§28). 아래 첫 절이 그 실행이고, 실행 P2–P4는 작성 중 확인 기록이다.

## G4 candidate de3054a00282e1bbfc97784918059f653ac08811 (2026-10-20, 최종, repair r1)

- 후보 SHA: main `de3054a00282e1bbfc97784918059f653ac08811`(#194–#201 최종 PR, #248, #251, #250, #252, #249 포함). 제품 코드 고정.
- 검사 commit: `b4ac931` = 후보 + #203의 test/doc commit. 후보 대비 diff는 세 test 파일과 `docs/demo/d4-01-*`뿐이며 **제품 diff 0**(`git diff de3054a -- src static` 비어 있음). 검사한 tree = commit한 tree(이 문서·evidence를 고친 docs commit만 뒤따른다).
- 계약: `d0-03-v1-r3`.
- 환경: WSL2 Linux, Python 3.12.3, pytest 8.4.2(uv venv, `uv sync --frozen --extra dev`). checkout은 검사 commit의 ext4 `git clone --shared`(drvfs에서는 fixture 재검증이 first-output 한도를 넘는다, P2 참고). heavy lock(`flock`) 아래 worker 1개, 파일마다 별도 pytest 프로세스로 직렬 실행.
- 명령: `uv run --frozen --no-sync python -m pytest -v -p no:cacheprovider --basetemp=<tmp>/g4r1-<stem> tests/test_demo_<stem>.py -rs`
- repair r1(독립 리뷰 P1): 이전 G4 기록은 모든 사례가 제품 seed라고 했으나 일부 하위 프로세스 사례가 대체 seed(`install_seed(make_seed(...))`, `main.demo_seed` 교체, `keep_real_tables`)를 썼다. 그 사례를 모두 #197 seed CLI(`python -m src.demo.seed build --out <tmp>/seed`) → `DEMO_SEED_DIR` → 실제 `demo_seed()` 경로로 옮겼고 단언은 줄이지 않았다(제품 seed의 실제 ID·이름·행 수로 같은 강도로 바꿈). 세 파일에 seed 대체 코드는 남아 있지 않다. 아래 표는 사례를 두 묶음으로 나눈다.

| 후보 SHA | 검사 commit | 계약 | 파일 | 결과 | 시간 | log |
| --- | --- | --- | --- | --- | --- | --- |
| `de3054a` | `b4ac931` | d0-03-v1-r3 | `tests/test_demo_isolation.py` | 7 passed, 0 failed, 0 skipped | 66.5 s | `d4-01-evidence/g4-isolation.log` |
| `de3054a` | `b4ac931` | d0-03-v1-r3 | `tests/test_demo_reset.py` | 25 passed, 0 failed, 0 skipped | 154.5 s | `d4-01-evidence/g4-reset.log` |
| `de3054a` | `b4ac931` | d0-03-v1-r3 | `tests/test_demo_security.py` | 17 passed, 0 failed, 0 skipped | 75.7 s | `d4-01-evidence/g4-security.log` |

합계 49/49 passed. 각 log는 `-v` 출력이라 test ID별 결과를 담는다.

### G4 acceptance (product seed, real app) — 24 passed

각 사례는 새 하위 프로세스에서 실제 FastAPI app(`APP_MODE=demo`, `TESTING=false`, `ENV=production`, 외부 키 환경 제거, 실제 cookie/CSRF middleware)을 띄우고, #197 CLI로 만든 제품 seed를 startup의 실제 `demo_seed()`가 읽는다.

| 파일 | 사례 | 계약 |
| --- | --- | --- |
| isolation | `test_http_visitors_are_isolated_through_mutation_forgery_and_reset` | §3/§5.1/§6/§10/§21 |
| isolation | `test_http_delete_and_csv_export_stay_private_and_skip_deleted_rows` | §22/§23 |
| isolation | `test_conversation_analysis_and_run_ledger_stay_private_per_visitor` | §28/§5.1/§6 |
| isolation | `test_teacher_csv_has_prefix_20_and_demo_suffix_5` | §23 |
| reset | `test_reset_during_student_mentor_analysis_and_shield_ledger_writes[*]`(5 지점 × 2 순서 = 10) | §5.1/§7/§7.2/§11/§28 |
| reset | `test_reset_close_failure_is_db_locked_and_keeps_lease_and_file` | §7.2 |
| reset | `test_caller_disconnect_after_mark_still_completes_reset` | §7.2 |
| reset | `test_reset_alone_drains_and_separate_live_lease_times_out` | §7.2 |
| reset | `test_two_tabs_send_different_reset_ids_concurrently` | §7/§9.1 |
| security | `test_demo_csrf_origin_cookie_matrix` | §9.1/§10 |
| security | `test_demo_cross_auth_and_role_deleted` | §9/§10/§22 |
| security | `test_demo_lifecycle_makes_no_external_calls` | §14 |
| security | `test_demo_admin_dangerous_endpoints_are_forbidden` | §20 |
| security | `test_demo_runs_make_no_external_calls` | §13/§14/§16/§19 |
| security | `test_demo_rejects_injected_external_model` | §11/§13/§20 |

### unit/synthetic 또는 rejected boot/normal mode — 25 passed, G4 acceptance 아님

| 파일 | 사례 | 종류와 이유 | 같은 계약 행의 실제 stack 검사 |
| --- | --- | --- | --- |
| isolation | `test_same_seed_gives_separate_files_and_writes_stay_private`, `test_reset_and_expiry_of_one_visitor_leave_the_other_untouched`, `test_a_context_is_refused_against_the_other_visitor_without_writes` | unit/synthetic: 부모 pytest 프로세스(conftest `TESTING=true`)에서 registry만 직접 호출, synthetic seed(`synthetic_record`). 위조 context·만료 같은 내부 경계를 직접 겨눈다 | 위 isolation acceptance 두 HTTP 사례, #204 `tests/browser_demo_isolation.mjs` |
| reset | `test_commit_inside_write_guard_precedes_reset`, `test_reset_first_rejects_late_write_and_never_recreates_old_db` | unit/synthetic: 부모 프로세스 harness, synthetic seed. barrier를 registry 수준에서 고정 | reset race 10건(acceptance), #198 `tests/test_demo_d2_04_g3.py::test_g3_reset_during_stream` |
| reset | `test_duplicate_reset_increments_generation_once` | unit/synthetic(같은 harness) | `test_two_tabs_send_different_reset_ids_concurrently`(acceptance) |
| reset | `test_ttl_during_reset_preparation_beats_publish`, `test_restart_removes_old_generation_and_rejects_unknown_files[*]`(2), `test_locked_seed_fails_reset_safely_and_new_id_retries`, `test_locked_start_is_not_published_and_new_id_retries`, `test_locked_retired_file_is_kept_until_unlocked`, `test_storage_limit_rejects_reset_and_start_without_state_change`, `test_257th_distinct_request_id_is_limit_reached_without_eviction` | unit/synthetic: 주입 clock(TTL), 재시작 중 남은 파일, SQLite 잠금, 저장 상한, receipt 상한 같은 고장·경계 상태는 공개 API와 제품 seed만으로 만들 수 없다 | 이 파일 안에는 없음. §7.2 잠금 실패의 실제 stack 사례는 `test_reset_close_failure_is_db_locked_and_keeps_lease_and_file`(acceptance) |
| security | `test_demo_import_refuses_invalid_app_mode[*]`(3), `test_demo_config_refuses_every_forbidden_input`, `test_demo_import_refuses_forbidden_input[*]`(3), `test_demo_import_refuses_bootstrap_admin`, `test_dotenv_is_ignored_in_demo_and_read_in_normal` | rejected boot / normal mode: 새 하위 프로세스지만 의도적으로 부팅이 거절되거나 설정만 읽으므로 seed·demo startup이 없다(§2/§14) | 해당 없음(부팅 거절 자체가 검사 대상) |
| security | `test_normal_mode_ignores_client_mode_switches`, `test_normal_admin_api_rejects_the_demo_provider[baseline]` | unit: 부모 프로세스(`TESTING=true`)의 normal mode app. demo startup 없음(§2/§9/§13/§20) | #204 `tests/browser_demo_isolation.mjs`의 normal mode 검사(실제 서버 `--normal`, demo 경로 404) |

- 반환했던 제품 결함 D1·D1b(#198), D3·D4(#200)는 후보에서 모두 해소되어 해당 사례가 통과한다. 이번 이관에서 새 제품 결함은 없었다.
- log의 host 경로는 `<venv>`/`<host-path>`로 가렸다(상대 경로 형태 포함, 전체 evidence 파일을 다시 검사). secret은 없다(각 하위 프로세스가 출력에 secret이 없음을 단언한다). 경고는 제3자 deprecation(starlette TemplateResponse, pythonjsonlogger)뿐이다.
- not run(이 문서 범위 밖, 이유): 전체 `pytest -q`, `npm run test:browser`, `tests/check_student_live.py` — 공통 규칙에 따라 PR head의 CI가 실행한다. `ruff check .`·`black --check .`은 같은 tree에서 통과.
- 이전 기록(`2a5c53e`, 49 passed)은 위 seed 대체 문제로 대체되었다.

## 실행 P4 — #248(D3/D4) 병합 뒤 재실행 (2026-10-20, 작성 중 확인)

- 기준: origin/main `190d74b`(#248: `admin_scenarios.py` D3 envelope, `admin_session_actions.py` D4 `demo_adapter`)를 `feat/203-final`에 병합한 `1df03bf`(충돌 없음). 세 test 파일은 main 변경에 포함되지 않았다. 검사한 tree = commit한 tree.
- 환경·명령: P2/P3와 같다(ext4 `git clone --shared`, Python 3.12.3, pytest 8.4.2, worker 1개, 파일마다 별도 프로세스, 직렬).

| 파일 | 결과 | 시간 |
| --- | --- | --- |
| `tests/test_demo_security.py` | 17 passed | 76.2 s |
| `tests/test_demo_isolation.py` | 7 passed | 39.4 s |
| `tests/test_demo_reset.py` | not run: P3 결과 재사용(25 passed, `1905c99`) | — |

skip 0, xfail 0.

- `test_demo_runs_make_no_external_calls` 통과: 관리자 `analyze_regenerate`까지 모든 run 종류가 로컬 adapter로 끝나고 외부 시도 0(D4 해소).
- `test_demo_rejects_injected_external_model` 통과: 시나리오 API 외부 모델 주입이 422 `DEMO_UNSUPPORTED_CONFIGURATION`(D3 해소). 검사 기대값은 바꾸지 않았다.
- isolation은 변경된 `admin_session_actions.py`의 관리자 세션 삭제 경로를 쓰므로 함께 재실행했다. reset은 관리자 경로를 쓰지 않고(`/admin/` 호출 0) test 파일도 바뀌지 않아 재실행하지 않았다. 세 파일 전체 실행은 G4 후보 SHA에서 한다.
- 남은 제품 결함: 없음(D1·D1b·D3·D4 모두 해소).

## 실행 P3 — #198 수정 뒤 재실행 (2026-10-20, 작성 중 확인)

- 기준: `feat/203-final`에 origin/main `0cc3fab`(#250 seed provenance)와 origin/feat/198-g3 `ae8c76d`(#198 PR4: D1·D1b 수정)를 병합(`b28ccc1`), 그 위 test 수정 commit `7013229`에서 검사. 검사한 tree = commit한 tree. `ae8c76d`는 이 시점 origin/main에 아직 없다.
- 환경·명령: P2와 같다(ext4 `git clone --shared`, Python 3.12.3, pytest 8.4.2, worker 1개, 파일마다 별도 프로세스, 직렬).
- `b28ccc1` 첫 실행: isolation 7 passed, reset 24 passed·1 failed, security 15 passed·2 failed. 새 실패 두 건은 검사 결함이었다(아래). 수정 뒤 `7013229`에서 다시 실행한 결과:

| 파일 | 결과 | 시간 |
| --- | --- | --- |
| `tests/test_demo_isolation.py` | 7 passed | 69.2 s |
| `tests/test_demo_reset.py` | 25 passed | 183.7 s |
| `tests/test_demo_security.py` | 15 passed, 2 failed | 89.5 s |

skip 0, xfail 0.

- 최종 기준 재실행: `ae8c76d`가 origin/main(`23bbfba` 이후)에 들어온 뒤 origin/main을 다시 병합(`1905c99`, 다른 test 두 파일의 flake 수정만 추가)하고 같은 명령으로 재실행했다. isolation 7 passed(42.8 s), reset 25 passed(159.2 s), security 15 passed·2 failed(58.2 s, D4·D3). 결과 동일.

- D1/D1b 해소: 교사 종료·분석이 202·`ok`로 끝나고 격리 run 원장·교사 CSV 두 사례가 통과한다.
- 분석 commit 경합: `[analysis_commit-reset_first]`·`[analysis_commit-write_first]` 둘 다 통과. `parked_or_done`은 barrier 도착(`gate.arrived`)을 기다린 뒤에만 진행하므로 두 지점(파이프라인 결과 직후·`execute_analysis` 완료 직후)에 실제로 도달했다.
- 검사 결함 수정(`7013229`): ① 경합 표의 분석 쓰기 먼저 상태를 `completed`→`ok`(분석 run 종료 상태는 `ok`/`degraded`, `analysis_pipeline.py:158`). ② egress 사례의 명시 retry를 별도 세션으로 옮김. 같은 세션에 테스트가 직접 넣은 실패 턴(세 번째 교사 메시지, `datetime('now')` 시각)이 §18 시각 순서·준비된 두 턴 바인딩과 맞지 않아 분석이 `invalid_reference`로 끝났다. 단언은 줄이지 않았다(중간 원장 증가 확인만 retry 앞 시점 값 student 2·mentor 2로 옮김, 최종 student 4·mentor 2·analysis 2 유지).
- 남은 실패 2건(제품 결함):
  - `test_demo_runs_make_no_external_calls`: 관리자 `POST /admin/sessions/{id}/analyze_regenerate`의 run이 `failed`·`configuration_unavailable`(결함 D4). 그 전 단계(학생·멘토·replay·legacy·교사 분석)는 통과, 외부 시도 0.
  - `test_demo_rejects_injected_external_model`: 결함 D3, 그대로.
- D3 scratch 확인: `7013229` + origin/feat/200-evidence `1f6c605`(PR #248) scratch 병합(커밋 안 함)에서 이 사례 1 passed(25.1 s).
- D4 scratch 확인: ext4 사본에서만 `regenerate_analysis`에 `demo_adapter`를 넘기는 임시 patch(커밋 안 함)로 egress 사례 1 passed(17.6 s). 나머지 단계에 숨은 실패가 없음을 확인했다.

| ID | 위치 | 기대(계약) | 실제 | 소유 |
| --- | --- | --- | --- | --- |
| D4 | `src/api/routes/admin_session_actions.py:79` `analysis_context`가 `ctx`·`registry`만 넘기고 `demo_adapter`가 없음. `regenerate_analysis`(l.381)와 관리자 `/end`(l.152)가 사용. 교사 route는 `session_analysis.py:56` `demo_analysis`로 넘김 | §14/§16/§18, d3-02 endpoint policy l.148: demo 관리자 재분석은 로컬 adapter로 실행 | run `failed`·`configuration_unavailable` | #200(파일 소유, §26 l.652), adapter 연결 방식은 #198 PR4와 같음 |

## 실행 P2 — phase 2 (2026-10-20, 작성 중 확인)

- 기준: main `1a40dc2`(#198 PR1–PR3, #194 S4 병합 포함) 위 branch `feat/203-final`, 검사 commit `f4e7bd0`. 검사한 tree = commit한 tree(검사 중 수정 없음).
- 변경: phase 1에서 "needs G4 / #198"로 skip한 다섯 사례를 실제 검사로 바꿨다. 새 사례는 모두 실제 FastAPI app(`APP_MODE=demo`, `TESTING=false`, `ENV=production`, 외부 키 환경 제거)을 하위 프로세스로 띄우고, #197 seed CLI `python -m src.demo.seed build --out <tmp>/seed`로 만든 제품 seed를 `DEMO_SEED_DIR`로 startup이 직접 읽는다(`main.demo_seed` 대체 없음).
- 환경: WSL2 Linux, Python 3.12.3(uv venv), pytest 8.4.2, worker 1개, 파일마다 별도 pytest 프로세스로 직렬 실행. checkout은 ext4 `git clone --shared`(같은 commit)다. WSL drvfs(`/mnt`)에서는 `DemoProvider._fixture`의 fixture 재읽기가 seed의 5 s first-output 한도를 넘어(#202 보고와 같음) 검사가 그 timeout으로 실패하므로 디스크만 바꿨다. 검사 쪽 timeout 완화는 없다(경합 검사의 `stretch_deadlines`는 barrier로 멈춘 동안 deadline이 끼어들지 않게 하는 경합 고정용이며 다른 파일에는 없다).
- 명령: `uv run --frozen --no-sync python -m pytest -q -p no:cacheprovider --basetemp=<tmp>/pt-<stem> tests/test_demo_<stem>.py -rs`

| 파일 | 결과 | 시간 |
| --- | --- | --- |
| `tests/test_demo_isolation.py` | 5 passed, 2 failed | 237.5 s |
| `tests/test_demo_reset.py` | 23 passed, 2 failed | 712.7 s |
| `tests/test_demo_security.py` | 15 passed, 2 failed | 113.6 s |

skip 0, xfail 0. 실패 6건은 모두 아래 제품 결함이며 검사 기대값은 계약값 그대로 둔다.

### 새로 실행한 사례

- 격리 §28/§5.1/§6 `test_conversation_analysis_and_run_ledger_stay_private_per_visitor`: 같은 seed의 방문객 A(교사)가 학생 두 턴·멘토 한 번 뒤 종료·분석하는 동안 B의 visitor DB(session/message/generation_run/api_usage_log/분석 표) 행이 전후 동일. B가 A의 세션·run ID로 분석/CSV/run 조회 시 404 `DEMO_RESOURCE_NOT_FOUND`·A 본문 없음, B lease 0. **failed**: 종료 단계(결함 D1).
- 격리 §23 `test_teacher_csv_has_prefix_20_and_demo_suffix_5`: 교사 CSV가 교사 20열 + suffix 5열, 파일명 `demo_d0-02-v1_teacher_session-<id>.csv`, seed 세션 `seed_prepared`·새 세션 `visitor_generated`, 두 턴 세션 6행. **failed**: 종료 단계(결함 D1).
- 경합 §5.1/§7/§7.2/§11/§28 `test_reset_during_student_mentor_analysis_and_shield_ledger_writes[stage-order]`: 학생 chunk, 멘토 저장, shield 원장 시작·종료, 분석 commit 다섯 지점 × reset 먼저/쓰기 먼저(asyncio.Event barrier). reset 200·generation+1, old DB에는 쓰기 먼저일 때만 해당 쓰기, 늦은 쓰기 0, 새 DB 원장 = seed, stream은 본문 없는 `run.cancelled` 한 개(`DEMO_STALE_GENERATION`, retryable false), old namespace 409, 정리 뒤 새 세대 파일만 남고 lease 0. 학생·멘토·원장 8건 **passed**, `analysis_commit` 2건 **failed**(결함 D1).
- 보안 §13/§14/§16/§19 `test_demo_runs_make_no_external_calls`: egress guard(자기 증명 5건 포착) 설치 뒤 `import src.main`. 학생(지원 입력·공통 입력·같은 request_id replay), 멘토(수동·자동), legacy `POST /sessions/{id}/messages`(410·행 없음), 명시 retry(같은 turn·새 run), 교사 분석, 관리자 재분석에서 외부 시도 0, 원장 행은 모두 `demo`/`demo-classroom-v1`. 학생·멘토·retry·legacy 단계 통과 뒤 **failed**: 종료 단계(결함 D1).
- 보안 §11/§13/§20 `test_demo_rejects_injected_external_model`: 저장 스냅샷 변조 → 422 `DEMO_UNSUPPORTED_CONFIGURATION`·행 없음(통과), 실행 요청의 provider/model 주입 필드는 무효(통과), 표 행 수 불변·외부 시도 0(통과). 시나리오 API 생성/수정(save_draft·publish)의 외부 모델 주입 → **failed**(결함 D3).

### 제품 결함(소유 이슈로 반환, 검사는 실패 유지)

| ID | 위치 | 기대(계약) | 실제 | 소유 |
| --- | --- | --- | --- | --- |
| D1 | `src/services/analysis_plan.py:67` `estimate_request` → `src/services/context_budget.py:13` `PROVIDER_ADAPTERS[request.provider]`(`call_execution.py:33`에 demo 없음) | §18/§28: demo `POST /sessions/{id}/end`·`/analyze` 202, 분석 실행 | `KeyError: 'demo'` → 503 `DEMO_CONFIGURATION_INVALID` | #198 |
| D1b | `src/services/analysis_runs.py` `reserve_analysis`가 `execution["demo_adapter"]`를 넣지 않음(학생 `generation_runs.py:429`, 멘토 `mentor_generation.py:344`는 넣음) | §14/§18: demo 분석은 로컬 adapter로 실행 | D1을 우회해도 run `failed`·`configuration_unavailable`(하위 프로세스 scratch 확인, 커밋 안 함) | #198 |
| D3 | `src/services/scenario_drafts.py:137` `model_identity_mismatch` 분기(route `src/api/routes/admin_scenarios.py` l.119/154) | §20 l.520: 시나리오 API 외부 모델 주입은 422 `DEMO_UNSUPPORTED_CONFIGURATION` | 422 필드 오류 `[{"path":"config.student.resolved_model_config","code":"model_identity_mismatch"}]`, 행 변화 없음 | #200(필드 422로 충분한지 coordinator 판단 필요) |

D1/D1b는 #202 PR-A smoke 보고의 D1/D1b와 같다.

### 실행하지 않은 범위

- full pytest, `npm run test:browser`, `tests/check_student_live.py`: 공통 규칙 H1(전체 회귀는 PR head CI). 이번 변경은 세 test 파일과 이 문서뿐이다.
- 분석 commit 경합의 barrier 지점은 D1 때문에 실제 도달을 확인하지 못했다. D1/D1b 수정 뒤 같은 명령으로 재검증한다.
