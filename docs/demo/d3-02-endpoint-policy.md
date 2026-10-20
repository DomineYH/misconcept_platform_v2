# D3-02 관리자 endpoint demo 정책 대응표 (#200)

상태: PR1(CSV·삭제)·PR2(위험 endpoint guard)는 구현, PR3(허용 CRUD quota)는 구현. 추가 표시 key는 없다: #199는 오류 envelope의 recovery key만 표시한다(coordinator 결정, 2026-10-20). 이 문서는 정책의 대응표이며 구현 증거(`d3-02-guards-evidence.md`)는 PR4에서 따로 쓴다.

## 1. 목적과 규칙

- 목적: 관리자 endpoint 각각이 demo에서 어떤 정책·오류를 받는지, 어느 코드가 구현하는지, 어느 PR 단계인지 한 표에 고정한다.
- 의미는 계약 `d0-03-v1-r3`([d0-03-contract-v1.md](d0-03-contract-v1.md)) §20–§23, §24.1, §26에 바인딩한다. 이 문서는 계약값(오류 code, 열 이름, 파일명)을 바꾸지 않는다.
- mode 판정은 `config.APP_MODE == "demo"` 하나뿐이다. 요청 상태·`TESTING`·monkeypatch를 mode로 읽지 않는다. normal 동작과 normal CSV는 byte 단위로 불변이다.
- 오류 code는 계약 §11 표의 code만 쓴다: `DEMO_ACTION_FORBIDDEN` 403, `DEMO_RESOURCE_NOT_FOUND` 404, `DEMO_LIMIT_REACHED` 429, `DEMO_UPLOAD_TOO_LARGE` 413, `DEMO_INVALID_REQUEST` 422.
- demo guard helper는 새 모듈을 만들지 않고 `admin_providers.py`에 둔다. 다른 위험 route는 이 파일에서 import한다.
  - `demo_read_only`: `get_admin_user` 위의 dependency다. demo에서 403 `DEMO_ACTION_FORBIDDEN`을 낸다. FastAPI는 dependency를 body 검증보다 먼저 풀기 때문에 body의 키·비밀번호를 검증하거나 읽기 전에 거절한다(잘못된 JSON 문법만 먼저 422다). 비교·비교 대화 route는 `admin_comparisons.py:DemoComparisonRoute`가 `demo_read_only` route를 비교 body reader(크기·UTF-8 검사)보다 먼저 거절한다. context·관리자·CSRF 검증 뒤이며 body를 읽지 않으므로 2 MiB 초과·잘못된 UTF-8도 403이다. 허용 stop route와 normal은 기존 크기 제한(413)을 유지한다.
  - `demo_missing`: demo에서 기존 404를 `DEMO_RESOURCE_NOT_FOUND`로 바꾼다.
  - `execution_scope`: 취소·삭제 서비스에 현재 방문객 scope를 넘긴다. normal은 `NORMAL_SCOPE`(기본값)다.
  - `execution_registry`: demo app이 묶은 lifecycle(`app.state.demo_request_db.lifecycle`)을 넘긴다. 모듈 singleton은 방문객 authority가 없어 방문객 token을 찾지 못하고 cleanup에서 `RuntimeError`를 낸다. normal은 기존 singleton(기본값)이다.
  - `request_factory`: demo에서 `db.bind`로 factory를 만들지 않고 guard가 걸린 요청 session을 쓴다.
- 교사는 기존 admin 권한 거절을 유지한다(`get_admin_user`의 demo 교사 403 `DEMO_ACTION_FORBIDDEN`, 또는 route 안의 admin 역할 검사). 아래 "허용 역할"은 모두 demo 관리자다.
- 공통 순서: 요청 context/User/CSRF 검증 뒤 demo guard가 body의 키·비밀번호 처리, 외부 SDK, 재인증, 작업 예약보다 먼저 실행된다.

표 약어:

- 경로 접두 `src/api/routes/`는 생략한다. `src/services/...`는 전체 경로로 쓴다.
- `FORBID` = 403 `DEMO_ACTION_FORBIDDEN`. 거절된 요청은 catalog/probe/비교 run/원장·사용량 row를 만들지 않고 outbound 호출이 없다.
- `NF` = 없거나 다른 scope의 ID이면 404 `DEMO_RESOURCE_NOT_FOUND`.
- `READ` = 현재 방문객 DB(factory)에서만 읽는 조회. 전용 오류는 `NF`.
- `CANCEL` = 현재 방문객 DB에 있는 기록만 대상이다. 실행 취소는 app lifecycle(`execution_registry`)의 `ctx.scope` token에만 한다(비교 stop은 기존 서비스가 `registry.cancel(scope, "run", generation_id)`, probe는 `registry.lookup(ctx.scope, "probe", probe_id)`). 삭제의 busy 판정도 같은 registry·scope로 한다. 부재 404, 다른 scope 취소 0.
- `CRUD` = `write_guard(ctx)` 아래 허용. quota 초과 429 `DEMO_LIMIT_REACHED`, count 확인과 insert/commit은 한 transaction.

## 2. 대응표

### 2.1 AI 연결·모델·probe·설정 (§20, §26)

| method | path | 행위 | 허용 역할(demo) | demo 정책 | 결과/오류 | 구현 위치 | 단계 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/admin/ai/state` | AI 상태·저장된 모델/역할 | 관리자 | READ(정적 예시, unknown usage). demo 응답에 §19 `provenance`(`record_origin=seed_prepared`; demo에서 AI 설정은 변경할 수 없는 seed 예시) | 200 | `admin_providers.py:provider_state` | PR2 |
| POST | `/admin/ai/providers/{provider}/key` | provider 키 저장·교체 | 관리자 | FORBID. `reauthenticate`·키 처리 전에 거절 | 403 | `admin_providers.py:save_key` | PR2 |
| POST | `/admin/ai/providers/{provider}/enabled` | provider 활성 전환 | 관리자 | FORBID | 403 | `admin_providers.py:set_enabled` | PR2 |
| POST | `/admin/ai/providers/{provider}/delete` | provider 키 삭제 | 관리자 | FORBID | 403 | `admin_providers.py:delete_key` | PR2 |
| POST | `/admin/ai/providers/{provider}/catalog` | catalog 새로고침 | 관리자 | FORBID. 외부 호출·원장 예약 없음 | 403 | `admin_catalog.py:refresh_catalog` | PR2 |
| POST | `/admin/ai/models` | 모델 등록 | 관리자 | FORBID(읽기 전용) | 403 | `admin_models.py:register_model` | PR2 |
| POST | `/admin/ai/models/{model_id}/update` | 모델 활성/옵션 변경 | 관리자 | FORBID(읽기 전용) | 403 | `admin_models.py:update_model` | PR2 |
| POST | `/admin/ai/models/{model_id}/probes` | 역할 시험 예약 | 관리자 | FORBID. 로컬/외부 모두 거절 | 403 | `admin_probes.py:reserve_probe` | PR2 |
| GET | `/admin/ai/probes/{request_id}` | probe 상태 | 관리자 | READ(같은 방문객 DB의 기존 상태만) | 200 / `NF` | `admin_probes.py:probe_status` | PR2 |
| POST | `/admin/ai/probes/{request_id}/cancel` | probe 취소 | 관리자 | CANCEL(kind=`probe`). demo는 `db.bind` factory·normal `active_probes`를 쓰지 않고 `cancel_reserved_probe(ctx.factory, id, ctx=ctx, registry=execution_registry(request))` | 기존 성공 응답 / `NF` | `admin_probes.py:cancel_probe` | PR2 |
| POST | `/admin/ai/settings/update` | AI 설정 변경 | 관리자 | FORBID(읽기 전용) | 403 | `admin_ai_settings.py:update_settings` | PR2 |

### 2.2 구형 단일 역할 비교 (§20)

| method | path | 행위 | 허용 역할(demo) | demo 정책 | 결과/오류 | 구현 위치 | 단계 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/admin/comparisons` | 비교 목록 | 관리자 | READ(기본 비어 있음) | 200 | `admin_comparisons.py:comparison_history` | PR2 |
| GET | `/admin/comparisons/{comparison_id}` | 비교 상세 | 관리자 | READ | 200 / `NF` | `admin_comparisons.py:get_comparison` | PR2 |
| GET | `/admin/comparisons/{comparison_id}/panels/{panel_id}/model` | panel model | 관리자 | READ | 200 / `NF` | `admin_comparisons.py:comparison_model` | PR2 |
| POST | `/admin/comparisons` | 비교 실행 | 관리자 | FORBID | 403 | `admin_comparisons.py:start_comparison` | PR2 |
| POST | `/admin/comparisons/{comparison_id}/panels/{panel_id}/retry` | panel 재시도 | 관리자 | FORBID | 403 | `admin_comparisons.py:retry_panel` | PR2 |
| POST | `/admin/comparisons/{comparison_id}/panels/{panel_id}/stop` | panel 중지 | 관리자 | CANCEL(기존 stop 서비스, `scope=ctx.scope`, `registry=execution_registry`) | 기존 결과 / `NF` | `admin_comparisons.py:stop_panel` | PR2 |
| POST | `/admin/comparisons/{comparison_id}/stop` | 비교 전체 중지 | 관리자 | CANCEL(기존 stop 서비스, `scope=ctx.scope`, `registry=execution_registry`) | 기존 결과 / `NF` | `admin_comparisons.py:stop_all` | PR2 |
| DELETE | `/admin/comparisons/{comparison_id}` | 비교 기록 삭제 | 관리자 | 기존 권한·비용 원장 보존의 방문객 기록 삭제만(`scope=ctx.scope`, `registry=execution_registry`로 busy 판정) | 기존 204 / `NF` | `admin_comparisons.py:remove_comparison` | PR2 |

### 2.3 새 비교 대화 (§20)

경로 접두 `BASE` = `/admin/comparison-conversations`. 구현 파일은 모두 `admin_comparison_conversations.py`.

| method | path | 행위 | 허용 역할(demo) | demo 정책 | 결과/오류 | 구현 위치 | 단계 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/admin/model-comparison` | 비교 화면(지원 예정 안내) | 관리자 | READ. 실행 유발 없음 | 200 | `:page` | PR2 |
| GET | `BASE` | 대화 목록 | 관리자 | READ(기본 비어 있음) | 200 | `:history` | PR2 |
| GET | `BASE/{identifier}` | 대화 상세 | 관리자 | READ | 200 / `NF` | `:get` | PR2 |
| GET | `BASE/{identifier}/panels/{panel_id}/model` | panel model | 관리자 | READ | 200 / `NF` | `:model` | PR2 |
| POST | `BASE` | 대화 생성 | 관리자 | FORBID | 403 | `:create` | PR2 |
| POST | `BASE/{identifier}/turn` | 턴 실행 | 관리자 | FORBID | 403 | `:turn` | PR2 |
| POST | `BASE/{identifier}/adopt` | 채택 | 관리자 | FORBID | 403 | `:adopt` | PR2 |
| POST | `BASE/{identifier}/mentor` | 멘토 실행 | 관리자 | FORBID | 403 | `:mentor` | PR2 |
| POST | `BASE/{identifier}/end` | 대화 종료 | 관리자 | FORBID | 403 | `:end` | PR2 |
| POST | `BASE/{identifier}/children/{child_id}/panels/{panel_id}/retry` | panel 재시도 | 관리자 | FORBID | 403 | `:retry` | PR2 |
| POST | `BASE/{identifier}/stop` | 대화 중지 | 관리자 | CANCEL(기존 stop 서비스, `scope=ctx.scope`, `registry=execution_registry`) | 기존 결과 / `NF` | `:stop` | PR2 |
| DELETE | `BASE/{identifier}` | 대화 기록 삭제 | 관리자 | 기존 권한·비용 원장 보존의 방문객 기록 삭제만(`scope=ctx.scope`, `registry=execution_registry`로 busy 판정) | 기존 204 / `NF` | `:delete` | PR2 |

### 2.4 재인증 서비스 (§26)

| method | path | 행위 | 허용 역할(demo) | demo 정책 | 결과/오류 | 구현 위치 | 단계 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| (서비스) | 없음. `key`/`enabled`/`delete` route가 호출 | `reauthenticate(request, user, password)` | 관리자 | demo에서 비밀번호 검증·전역 limiter 전에 fail-closed | 403 | `src/services/provider_reauthentication.py:reauthenticate` | PR2 |

### 2.5 조회·삭제·CSV (§20 마지막 행, §22, §23, §24.1)

조회 GET은 모두 READ다: 현재 방문객 DB 관리자 조회만, 삭제된 세션의 본문·분석은 노출하지 않는다. 상태·분석 API와 단일 다운로드는 삭제된 세션에서 `NF`.

| method | path | 행위 | 허용 역할(demo) | demo 정책 | 결과/오류 | 구현 위치 | 단계 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/admin/sessions-page` | 세션 목록 화면 | 관리자 | READ | 200 | `admin_sessions.py:sessions_page` | PR1 |
| GET | `/admin/sessions` | 세션 목록 JSON | 관리자 | READ | 200 | `admin_sessions.py:list_sessions_api` | PR1 |
| GET | `/admin/stats` | 통계 | 관리자 | READ | 200 | `admin_session_stats.py:get_stats` | PR1 |
| GET | `/admin/user-conversations` | 사용자별 세션 수 | 관리자 | READ | 200 | `admin_session_stats.py:user_conversations_page` | PR1 |
| GET | `/admin/analysis-page` | 분석 화면 | 관리자 | READ | 200 | `admin_analysis.py:analysis_page` | PR1 |
| GET | `/admin/analysis/{analysis_id}/detail` | 분석 상세 modal | 관리자 | READ | 200 / `NF` | `admin_analysis.py:analysis_detail_modal` | PR1 |
| GET | `/admin/api-usage` | API 사용량 | 관리자 | READ(unknown은 미측정 표시) | 200 | `admin_api_usage.py:api_usage_dashboard` | PR1 |
| GET | `/admin/sessions/{session_id}/detail` | 세션 상세 modal | 관리자 | READ. 삭제 세션 본문 비노출 | 200 / `NF` | `admin_session_actions.py:session_detail` | PR1 |
| GET | `/admin/sessions/{session_id}/analysis_modal` | 분석 modal | 관리자 | READ. 삭제 세션 비노출 | 200 / `NF` | `admin_session_actions.py:analysis_modal` | PR1 |
| GET | `/admin/sessions/{session_id}/analysis` | 분석 API | 관리자 | READ. 삭제 세션 `NF` | 200 / `NF` | `admin_session_actions.py:get_admin_analysis` | PR1 |
| GET | `/admin/sessions/{session_id}/analysis/runs/{run_id}` | 분석 run 상태 | 관리자 | READ. 삭제 세션 `NF` | 200 / `NF` | `admin_session_actions.py:get_admin_analysis_run` | PR1 |
| POST | `/admin/sessions/{session_id}/delete` | 세션 soft delete | 관리자 | 기존 제한(이미 삭제 400) 유지. demo: 같은 `write_guard` handle 아래 `capture_tour_facts` → 삭제 commit → lock 해제 전 `retain_tour_facts`로 `session_deleted` 확정. rollback이면 갱신 0 | 200 / 기존 400·404 | `admin_session_actions.py:delete_session` | PR1 |
| POST | `/admin/users/{user_id}/delete` | 교사 삭제 | 관리자 | 기존 제한(자기 삭제·진행 세션·모델 검증 기록) 유지. 종료 세션 `teacher_id=NULL`·본문 보존. demo: 같은 handle 아래 capture → commit → lock 해제 전 retain으로 `teacher_deleted` 확정. 삭제 중 실행 취소는 `ctx.scope` 포함(`active_runs.get` 대신 `lifecycle` 공개 API) | 기존 응답 / 기존 400·404 | `admin_users.py:delete_user` | PR1 |
| GET | `/admin/sessions/export` | 필터 세션 CSV(`all`) | 관리자 | demo에서만 suffix 5열. 삭제 세션 제외 | 200 CSV | `admin_session_export.py:export_sessions` | PR1 |
| POST | `/admin/sessions/export-selected` | 선택 세션 CSV(`selected`) | 관리자 | suffix 5열. `X-Demo-Export-Request-ID` echo, `X-Demo-Export-Row-Count` 제공. 삭제 행 제외 | 200 CSV / 잘못된 request ID 422 `DEMO_INVALID_REQUEST` | `admin_session_export.py:export_selected_sessions` | PR1 |
| GET | `/admin/user-conversations/{user_id}/export` | 사용자별 CSV(`user-<id>`) | 관리자 | suffix 5열. 삭제 교사 ID로 보존 내용을 우회 노출하지 않음 | 200 CSV / 기존 404 | `admin_session_export.py:export_user_conversations` | PR1 |
| GET | `/admin/user-conversations/{user_id}/sessions` | 사용자별 종료 세션 목록 | 관리자 | READ | 200 / 기존 404 | `admin_session_export.py:list_user_sessions` | PR1 |
| GET | `/admin/sessions/{session_id}/download` | 단일 세션 CSV(`session-<id>`) | 관리자 | suffix 5열. `X-Demo-Export-Request-ID` echo, `X-Demo-Export-Row-Count` 제공. 삭제 세션 `NF` | 200 CSV / `NF` / 잘못된 request ID 422 `DEMO_INVALID_REQUEST` | `admin_session_export.py:download_session` | PR1 |
| 공통 | (exporter) | CSV 열·행 생성 | - | keyword-only `demo_seed_session_id`. §3 참고 | - | `src/services/export.py` | PR1 |

### 2.6 허용 CRUD (§21, §8)

모든 mutation은 `write_guard(ctx)` 아래에서 허용한다. GET 화면은 READ다. quota 설정은 `DEMO_MAX_USERS/GROUPS/SCENARIOS/CONTRIBUTORS/UPLOAD_BYTES/UPLOAD_ROWS`(config만, §8 기본값). 방문객 AppSetting으로 전역 quota를 바꿀 수 없다.

- 구현 helper는 `src/services/admin_user_bulk.py:demo_quota`다. demo에서 count 전에 `BEGIN IMMEDIATE`로 방문객 writer를 잡아 count와 insert/commit이 같은 guard·transaction에 있다. 따라서 병렬 생성이 마지막 자리를 함께 쓰지 못한다. count는 현재 row 수이고, 삭제 commit 뒤 자리가 돌아온다. 시나리오는 soft delete된 row를 세지 않는다.
- 시나리오 생성은 `save_scenario`가 자기 transaction을 다시 시작하므로, demo에서 `visitors.write_guard(ctx)`와 `ctx.factory()` session으로 count·저장·commit을 한 guard 안에서 한다(PR1 `demo_delete`와 같은 방식).
- normal은 helper가 SQL 없이 바로 돌아오므로 동작이 같다.

| method | path | 행위 | 허용 역할(demo) | demo 정책 | 결과/오류 | 구현 위치 | 단계 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| GET | `/admin/users` | 계정 목록 | 관리자 | READ | 200 | `admin_users.py:list_users` | PR3 |
| POST | `/admin/users` | 계정 생성 | 관리자 | CRUD. `DEMO_MAX_USERS` | 201 / 429 `DEMO_LIMIT_REACHED` | `admin_users.py:create_user` | PR3 |
| POST | `/admin/users/{user_id}/update` | 계정 수정 | 관리자 | CRUD. 기존 중복·권한 검사 유지 | 기존 응답 | `admin_users.py:update_user` | PR3 |
| GET | `/admin/users/bulk/template` | bulk 템플릿 | 관리자 | 기존 parser/필드 재사용 | 200 CSV | `admin_users.py:download_bulk_template` | PR3 |
| POST | `/admin/users/bulk/preview` | bulk 미리보기 | 관리자 | route가 upload를 64 KiB 단위로 읽다가 `DEMO_MAX_UPLOAD_BYTES`를 넘는 순간 413으로 멈춘다(normal은 같은 bytes를 같은 결과로 읽는다). Starlette multipart 해석이 handler 전에 part를 임시 파일에 받는 점은 그대로다. rows > min(`DEMO_MAX_UPLOAD_ROWS`, 남은 user 수)이면 429. `parse_csv`가 `DEMO_MAX_UPLOAD_ROWS` 초과를 normal 100행 400보다 먼저 429로 거절한다 | 200 / 413 `DEMO_UPLOAD_TOO_LARGE` / 429 `DEMO_LIMIT_REACHED` | `admin_users.py:preview_bulk_upload`, `src/services/admin_user_bulk.py` | PR3 |
| POST | `/admin/users/bulk/register` | bulk 등록 | 관리자 | 최종 transaction에서 같은 min 재검사(count+insert 한 transaction). 초과 요청은 행 등록 없이 전체 429 | 200 / 429 `DEMO_LIMIT_REACHED` | `admin_users.py:register_bulk_users_endpoint`, `src/services/admin_user_bulk.py` | PR3 |
| GET | `/admin/groups` | 그룹 목록 | 관리자 | READ | 200 | `admin_groups.py:list_groups` | PR3 |
| POST | `/admin/groups` | 그룹 생성 | 관리자 | CRUD. `DEMO_MAX_GROUPS` | 201 / 429 `DEMO_LIMIT_REACHED` | `admin_groups.py:create_group` | PR3 |
| POST | `/admin/groups/{group_id}/update` | 그룹 수정 | 관리자 | CRUD. 기존 참조 제한·버전 검사 유지 | 기존 응답 | `admin_groups.py:update_group` | PR3 |
| POST | `/admin/groups/{group_id}/delete` | 그룹 삭제 | 관리자 | CRUD. 기존 참조 제한 유지 | 기존 응답 | `admin_groups.py:delete_group` | PR3 |
| GET | `/admin/scenarios` | 시나리오 목록 | 관리자 | READ | 200 | `admin_scenarios.py:list_all_scenarios` | PR3 |
| GET | `/admin/scenarios/new` | 편집기(신규) | 관리자 | READ | 200 | `admin_scenarios.py:new_scenario_editor` | PR3 |
| GET | `/admin/scenarios/{scenario_id}` | 시나리오 draft | 관리자 | READ | 200 / `NF` | `admin_scenarios.py:get_scenario_draft` | PR3 |
| GET | `/admin/scenarios/{scenario_id}/edit` | 편집기(수정) | 관리자 | READ | 200 / `NF` | `admin_scenarios.py:edit_scenario_draft` | PR3 |
| POST | `/admin/scenarios` | 시나리오 생성 | 관리자 | CRUD. `DEMO_MAX_SCENARIOS`, 공통 demo 모델 검증 | 201 / 429 `DEMO_LIMIT_REACHED` / 422 `DEMO_UNSUPPORTED_CONFIGURATION` | `admin_scenarios.py:create_scenario`, `src/services/admin_scenario_ops.py` | PR3 |
| POST | `/admin/scenarios/{scenario_id}/update` | 시나리오 수정 | 관리자 | CRUD. 기존 completeness/review/`expected_version` 검사 유지 | 기존 응답 / 422 `DEMO_UNSUPPORTED_CONFIGURATION` | `admin_scenarios.py:update_scenario`, `src/services/admin_scenario_ops.py` | PR3 |
| POST | `/admin/scenarios/{scenario_id}/delete` | 시나리오 삭제 | 관리자 | CRUD | 기존 응답 | `admin_scenarios.py:delete_scenario` | PR3 |
| GET | `/admin/about` | 기여자 화면 | 관리자 | READ | 200 | `admin_about.py:admin_about_page` | PR3 |
| POST | `/admin/about` | 기여자 생성 | 관리자 | CRUD. `DEMO_MAX_CONTRIBUTORS`. 외부 URL fetch 없음 | 201 / 429 `DEMO_LIMIT_REACHED` | `admin_about.py:create_contributor` | PR3 |
| POST | `/admin/about/{contributor_id}/update` | 기여자 수정 | 관리자 | CRUD | 기존 응답 | `admin_about.py:update_contributor` | PR3 |
| POST | `/admin/about/{contributor_id}/delete` | 기여자 삭제 | 관리자 | CRUD | 기존 응답 | `admin_about.py:delete_contributor` | PR3 |

### 2.7 계약 표에 없던 분석·종료 route (가장 가까운 규칙으로 분류)

| method | path | 행위 | 허용 역할(demo) | demo 정책 | 결과/오류 | 구현 위치 | 단계 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| POST | `/admin/sessions/{session_id}/end` | 관리자 세션 종료 + 분석 예약(`request_analysis`) | 관리자 | 허용. 방문객 여정의 일부다(§22 "정상 여정은 먼저 대화를 종료", §24.1 dialogue_complete/analysis_ready). 종료 저장은 §6 write_guard, 분석 예약·실행은 #198 공통 admission과 §16 request_id 예약 계약을 따른다. 삭제 세션은 `NF`. demo는 두 서비스에 `ctx`·`registry=execution_registry`와 방문객 session(`analysis_db`)을 넘긴다. | 기존 응답 / `NF` | `admin_session_actions.py:end_session` | PR2 |
| POST | `/admin/sessions/{session_id}/analyze_regenerate` | 분석 재생성 예약(채택 보고서 보존) | 관리자 | 허용. §16의 명시적 retry 규칙을 따른다: 같은 request_id+같은 입력은 기존 run 반환, 다른 body는 `DEMO_REQUEST_CONFLICT`, 자동 retry 0(교사 분석의 1회 재시도 규칙만 예외), 정상 완료 결과를 새 예시로 바꾸지 않음. 외부 호출 없음(§14). demo는 `ctx`·`registry`·방문객 session을 넘긴다. | 기존 응답 | `admin_session_actions.py:regenerate_analysis` | PR2 |
| POST | `/admin/sessions/{session_id}/analysis/runs/{run_id}/cancel` | 분석 run 취소 | 관리자 | 허용. 방문객 여정의 일부이며 §20 stop/cancel 규칙과 같다: 현재 namespace에 있는 run만 취소, 다른 scope 취소 0, 부재·삭제 세션 `NF`. 취소는 `analysis_runs.cancel_analysis(..., ctx=ctx, registry=execution_registry)`의 `ctx.scope` 경로다. | 202 / `NF` | `admin_session_actions.py:cancel_admin_analysis_run` | PR2 |

### 2.8 다른 소유자

| method | path | 행위 | 소유 | 비고 |
| --- | --- | --- | --- | --- |
| GET | 교사 CSV export route | 교사 단일·다중 export | #198 (`src/api/routes/session_analysis.py`) | #200 코드가 아니다. #198이 같은 exporter에 keyword `demo_seed_session_id`를 넘긴다 |

## 3. CSV provenance (§23)

- 대상: 관리자 단일·전체·선택·사용자별 export(위 4개 route와 `download_session`) 및 교사 export 공통 exporter(`src/services/export.py`).
- 열 수: 관리자 27+5, 교사 20+5. 기존 열의 이름·순서·값 의미·행 구조와 `meta_json`·snapshot_origin·제목·본문은 그대로 둔다.
- suffix 5열(이 순서, 모든 message/summary 행의 값):
  1. `demo_response_source` = `prepared_example`
  2. `demo_external_ai_called` = 문자열 `false`
  3. `demo_content_version` = `d0-02-v1`
  4. `demo_fixture_version` = `demo-fixture-v1`
  5. `demo_record_origin` = `seed_prepared` 또는 `visitor_generated`. `session_id == seed_refs["completed_session"]`이면 seed, 아니면 visitor. `seed_refs`는 #194 S3가 `app.state.demo_request_db.seed_refs`로 공급한다.
- exporter 인자: keyword-only `demo_seed_session_id: int | None`. `None`(normal 기본)이면 출력은 기존과 동일하다. normal exporter 호출에 demo 값을 기본 주입하지 않는다.
- 파일명(계약): `demo_d0-02-v1_<admin|teacher>_<session-ID|selected|all|user-ID>.csv`. 관리자 route 구현값은 `demo_d0-02-v1_admin_session-<id>.csv`, `..._admin_selected.csv`, `..._admin_all.csv`, `..._admin_user-<id>.csv`. `Content-Disposition`은 이 ASCII 패턴과 검증된 ID로만 구성한다. 사용자 삭제 뒤 세션 export에는 user-ID를 만들어 넣지 않는다. normal 파일명(`sessions_export.csv`, `selected_sessions.csv`, `user_{id}_conversations.csv`, `session_{id}.csv`)은 불변이다.
- 헤더: `download_session`과 `export_selected_sessions`만 `X-Demo-Export-Request-ID`(canonical UUID, 클라이언트 값을 그대로 echo)와 `X-Demo-Export-Row-Count`(header 제외 실제 행 수, 1 이상)를 낸다. 잘못된 request ID와 data 행이 0인 결과(header만 있는 CSV)는 422 `DEMO_INVALID_REQUEST`이며 행을 만들어 넣지 않는다. 헤더가 없으면 echo를 생략한다. 나머지 두 admin export route에는 이 헤더가 없다.
- 유지: UTF-8, 기존 csv writer quoting/줄바꿈, 수식 시작 문자(`=`, `+`, `-`, `@`, tab, CR) 방어. 주석·배너·별도 summary 행을 추가하지 않는다. 기본 두 턴 세션은 6행(teacher 2 / student 2 / tutor 1 / summary 1).

## 4. 다른 소유자 요청

- #194: `app.state.demo_request_db.seed_refs` 노출, 관리자 template `demo_config`.
- #197: `seed_refs.completed_session` 유지.
- #198: `call_admission.py`, `session_analysis.py` 교사 export 전달, `provider_smoke.py`.
- #199: 읽기 전용/지원 예정 표시만 맡고 API 정책을 중복하지 않는다.
- #201: i18n key `demo.action_forbidden`, `demo.not_found`, `demo.limit_reached`, `demo.upload_limit`.
- #192: `generation_lifecycle.py`/`probe_lifecycle.py` 변경이 필요하면 요청한다.

## 5. 범위 밖

- template/화면 표시(#199), i18n key(#201), 공통 admission/execution·학생/멘토/분석 전달부·교사 export route(#198), config/main/dependencies/health(#194), schema/migration/model 검증 helper(#195), seed/fixture(#197), lifecycle/registry 내부(#192).
- 비교 실행·채택 체험 추가. 안전한 stop/delete 허용은 비교 실행의 MVP 포함을 뜻하지 않는다.
- 외부 호출, 운영 자료/secret, 새 dependency, 공용 conftest/CI 수정. 계약값은 이 문서에서 바꾸지 않는다.

## 6. 분류 기록

S1 초안의 미분류 3건(§2.7)은 coordinator 결정(2026-10-20)에 따라 위 표처럼 분류했다. 세션 종료와 run 취소는 방문객 여정으로 허용하고, 분석 재생성은 §16 retry 규칙을 따른다. 세 route는 demo에서 분석 서비스(`mark_session_ended`, `request_analysis`, `cancel_analysis`)에 `ctx=request.state.demo_context`와 `registry=execution_registry(request)`를 넘겨 demo의 write_guard·scope·provider 경로를 탄다(S2 repair, 2026-10-20). 서비스는 `registry.write_guard(ctx)` 안에서 넘겨받은 session을 commit한다. 요청 session은 commit마다 자기 write guard를 잡아 같은 방문객 transition에 재진입하므로, demo는 요청 session을 먼저 commit한 뒤 `ctx.factory()` 방문객 session을 넘긴다(`analysis_db`, PR1 `demo_delete`와 같은 방식). normal은 요청 session과 기본값을 그대로 쓴다.
