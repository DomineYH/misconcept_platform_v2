# D4-01 격리·보안·경합 검사 evidence (#203)

계약: `d0-03-v1-r3` 의미(문서 `d0-03-v2`). 이 문서의 실행 결과는 **작성 중 확인**이며 완료 증거가 아니다. 최종 acceptance는 #202(G4)가 고정한 통합 SHA(`docs/demo/d3-04-integration-manifest.json`의 `candidate_commit`)에서의 실행분만 인정한다(계약 §27/§28).

## G4 최종 실행 — pending G4 candidate

아직 실행하지 않았다. #202가 후보 SHA를 고정하면 별도 order로 세 파일과 README 전체 명령을 그 SHA에서 실행해 여기에 기록한다.

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
