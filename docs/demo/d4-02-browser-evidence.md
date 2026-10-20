# D4-02 독립 브라우저 여정 검증 evidence (#204)

계약: `d0-03-v1-r3` 의미(문서 `d0-03-v2`). 이 문서의 실행 결과는 **작성 중 확인**이며 완료 증거가 아니다. 최종 acceptance는 #202(G4)가 고정한 통합 SHA와 `docs/demo/d3-04-integration-manifest.json`에서의 실행분만 인정한다.

## 실행 1 — Phase 1 (2026-10-20, 작성 중 확인)

- 기준: origin/main `2dc7840963e6ca700915c2c63cff7729ca54afa9`(#194 S3·#195 PR2·#200 병합 포함), branch `feat/204-browser`. 이 문서와 같은 commit의 test 파일로 실행.
- 환경: WSL2 Linux, Python 3.12(uv venv), Node 24, Playwright Chromium headless, 공유 머신 load average 약 28.
- 명령: `npm run test:browser -- browser_demo_`(heavy lock 아래, worker 1개).

| 모듈 | 결과 | 범위 |
| --- | --- | --- |
| `browser_demo_isolation.mjs` | passed (57.8 s) | 아래 runnable 항목 |
| `browser_demo_journey.mjs` | not run: 모든 case pending(needs G4 / #202) | A 여섯 단계·CSV |
| `browser_demo_recovery.mjs` | not run: 모든 case pending(needs G4 / #202) | I 복구·언어·접근성 |

`browser_demo_isolation.mjs`가 실제로 확인한 것(앱 origin interception 없음):

- helper 기동: 실제 FastAPI, `APP_MODE=demo`, `TESTING=false`, `ENV=development`, loopback `DEMO_ORIGIN`/`NORMAL_ORIGIN`(서로 다른 host), 임의 32자 이상 secret, 임시 `DEMO_DATA_ROOT`. 외부 socket/SDK 시도 0, server log에 cookie 값·임시 root 없음.
- §10 쿠키: `GET /demo` 200이 `demo_session_id`(HttpOnly, Max-Age=28800, Path=/, SameSite=Lax, Domain 없음)와 `demo_csrftoken`(HttpOnly 아님, Max-Age/Expires 없음, Path=/, SameSite=Lax)만 발급. development loopback이므로 Secure 없음. normal `session_id`/`csrftoken` 없음.
- §9 두 방문객: context A(teacher)/B(admin)가 같은 seed에서 각각 201 start, 서로 다른 `browser_scope_id`. start 공개 후 CSRF token 회전. DemoState에 visitor/user/path 필드 없음.
- §9.1/§10 CSRF·Origin 행렬(`POST /demo/role`): 정상 JSON과 동일 origin Referer form은 200(대조군). token 누락·오류, 교차 Origin, Origin 없는 JSON, Origin·Referer 없는 form, 교차 Referer form, start 회전 전 token은 모두 403 `DEMO_CSRF_INVALID`·Set-Cookie 없음·no-store, 상태 불변.
- §9.1 위조/부재 서명: 401 `DEMO_SESSION_REQUIRED`. §3 body의 `path`/`visitor_id`/`user_id`, query `visitor_id`, header `x-demo-user-id`: 422 `DEMO_INVALID_REQUEST`.
- §10 namespace: `ds/dg/da`와 `X-Demo-Generation`/`X-Demo-Scope` 불일치 409 `DEMO_STALE_GENERATION`, `X-Demo-Auth-Epoch` 불일치 409 `DEMO_STALE_ROLE`, B가 A의 namespace 사용 409.
- 격리: A의 역할 전환·reset 뒤 A의 이전 namespace는 409 STALE_GENERATION, B의 scope/generation/auth_epoch/role과 `/metrics` database 총계 불변.
- `POST /login` 403 `DEMO_ACTION_FORBIDDEN`. normal mode(같은 helper `--normal`)에서 `GET /demo`, `/demo/state`, 유효 normal CSRF를 가진 `POST /demo/start|role|reset`이 모두 404, demo cookie 없음.

## 실행 2 — Phase 1 재개 (2026-10-20, 작성 중 확인)

- 기준: origin/main `068bcf9`(#197 seed·fixture, #200 PR2 guard, #199 PR1, #201 PR1–2 포함)을 `feat/204-browser`에 병합(`56d65b0`). 이 문서와 같은 commit의 test 파일로 실행.
- 환경: WSL2 Linux(8 core), Python 3.12.3(uv venv), Node 24, Playwright Chromium headless, load average 약 10.
- 명령: 모듈마다 따로 `flock /tmp/mp2-heavy.lock -c 'npm run test:browser -- <module>'`(worker 1개).

| 모듈 | 결과 | 범위 |
| --- | --- | --- |
| `browser_demo_isolation.mjs` | 시도 2a failed, 시도 2b passed(67.4 s, `6d77b95`) | 실행 1 항목 + 아래 추가 항목 |
| `browser_demo_journey.mjs` | not run: 모든 case pending(needs G4 / #202). 러너의 PASS는 pending 출력뿐 | A 여섯 단계·CSV |
| `browser_demo_recovery.mjs` | not run: 모든 case pending(needs G4 / #202). 러너의 PASS는 pending 출력뿐 | I 복구·언어·접근성 |

helper는 매 기동 시 seed를 새로 빌드하므로 `seed_sha256`(물리 bytes)은 실행마다 다르고 `logical_data_sha256`·`manifest_sha256`은 같다. 각 실행의 receipt는 그 실행의 log `RECEIPT` 행이다. 공통 값: `db_schema_version` `037_demo_provider.sql`, `manifest_sha256` `60cc355c4a5cee0cf1316bb97d8c52b09a03e39bb242514f66062e34159c0516`, `logical_data_sha256` `8d3fa9dde40a9d65273868a20271cdf767665e27c54384a54c74e575b3342980`.

| 시도 | 결과 | `seed_sha256` | `source_commit` | 빌드 방식 | log |
| --- | --- | --- | --- | --- | --- |
| 2a | failed: `/admin/sessions/1/analysis` 503(아래 결함) | `557c6b0809f153f1c0366dea7870b9e5e13df22b72f9f9982fb01bca3001c632` | `56d65b0` | in-process `build()` | `logs-w195/204/browser-isolation-1.log` |
| 2b | passed 67.4 s | `4f9c47a7f17450cb25625d5878dcf4fe3dbd60b73885ce2beda517613f4064cb` | `56d65b0` | in-process `build()` | `logs-w195/204/browser-isolation-2.log` |

`migration_set_sha256`은 receipt 필드가 아니며 #197 manifest가 검증한다(`build()`가 설치 migration과 다르면 실패). 2a/2b의 receipt `command`는 실제 실행이 아닌 기록용 문자열이었다(리뷰 지적) — 실행 3부터 CLI를 실제로 실행한다.

실행 2에서 추가로 확인한 것(#197 seed, seed_refs admin=1·teacher=2·completed_session=1):

- B(§28·§3): B 관리자의 `/metrics` 총계, `/admin/ai/state`, `/admin/users`, `/admin/groups`(demo-config의 sliding `expires_at`만 정규화)와 seed 완료 세션 CSV가, A의 교사 이름 변경·사용자 생성·교사 삭제·A 측 CSV 다운로드 뒤에도 그대로다. A의 CSV는 A의 편집을 반영해 B와 다르다.
- §23: seed 완료 세션의 관리자 CSV는 27열 + suffix 5열(`demo_response_source`…`demo_record_origin`) 순서, 행 값 `prepared_example,false,d0-02-v1,demo-fixture-v1,seed_prepared`.
- B가 A에서 새로 만든 사용자 ID로 `POST /admin/users/<id>/update` → 404 `DEMO_RESOURCE_NOT_FOUND`, no-store, Set-Cookie 없음, 본문에 A 자료 없음. 그 뒤에도 B 불변.
- F(§9·§22): A에서 삭제된 seed 교사로 role 전환 → 409 `DEMO_ROLE_DELETED`. 관리자 자기 삭제(`/admin/users/1/delete`) → 400(기존 제한), `/metrics` 총계와 role/auth_epoch 불변.

## 실행 3 — review repair r1 (2026-10-20, 작성 중 확인)

- 변경: helper가 #197 seed CLI `python -m src.demo.seed build --out <tmp>/seed`를 격리 환경(scrub된 env, `APP_MODE=demo`) 하위 프로세스로 실제 실행하고 exit status 0을 확인한 뒤 그 stdout receipt를 그대로 출력한다(일회용 경로만 `<tmp>`로 가림). 모듈은 개행으로 끝난 `RECEIPT`/`READY` 행만 해석하고, 깨진 JSON은 startup 실패로 거절하며 helper를 정리한다. #198 분석 상세 검사는 실행 가능한 case로 남기고 명시적으로 skip한다(기대 status 200 유지).
- 기준: `feat/204-browser` `6d77b95` + 이 수정(같은 commit으로 기록). 명령: `flock /tmp/mp2-heavy.lock -c 'npm run test:browser -- browser_demo_isolation'`.
- 결과: passed (21.5 s), log `logs-w195/204/browser-isolation-r1.log`. 출력: `SKIP B: B's admin analysis detail … product defect -> #198 …`.
- receipt(CLI 실제 실행분): `seed_sha256` `94da6ace5b1a211e07026f3b0ac54c07a9a90b4ec6e52023f7f23b4221b79427`, `source_commit` `6d77b9575b44ed1cade8d0628a21ccdc40283cc8`, `command` `python -m src.demo.seed build --out <tmp>/seed`, 나머지는 위 공통 값과 같다.
- skip case 확인: 같은 모듈의 임시 사본에서 skip만 끈 실행은 `503 !== 200`으로 실패했다(`logs-w195/204/browser-isolation-r1-unskipped.log`, 사본은 삭제). 즉 검사는 보존되어 있고 #198 수정 전에는 여전히 빨갛다.
- 시작 protocol 진단(브라우저 없음): 가짜 helper로 분할 수신 receipt는 정상 해석, 깨진 JSON과 receipt 부재는 promise 거절, uncaught 예외 0(`logs-w195/204/receipt-diag.log`).

## 작성 중 대체(최종 evidence 전에 제거)

1. seed 바인딩: `src.main.demo_seed`가 main에서 아직 CONFIGURATION_INVALID다(`src/main.py:200`, #194 S4 = PR #242 검토 중). helper는 #197 seed CLI로 실제 product seed를 새로 만들고 `src.main.demo_seed`를 `load_seed(seed_dir)`로 바인딩한다(brief의 대체 ①). 실행 1의 합성 seed(`make_seed`)는 제거했다. #194 S4가 병합되면 helper의 바인딩도 제거한다.
2. 만료 재진입용 helper 시계(`VisitorRegistry.clock`): 아직 없음. recovery 모듈 작성 때 추가한다.

## Pending

needs G4 / #202:
- A: 여섯 단계 DOM `data-demo-step`·TourFacts, reset 후 이전 URL/탭 409, CSV 파일 파싱(27+5열, 6행, 출처값, 삭제 후 비노출).
- B: A의 대화·분석 뒤 B 불변, A의 run ID를 B에서 요청 시 404 `DEMO_RESOURCE_NOT_FOUND`(demo 학생/멘토/분석 실행, #198).
- I: 새로고침·뒤로/앞으로·BFCache, 중복 클릭, 투어 닫기/건너뛰기/재개, 두 탭 stale, 한/영 전환, 390×844·키보드·200% 확대, 만료 재진입.

product defect -> #198:
- B: B의 seed 세션 관리자 분석 상세가 A 편집 뒤 불변. `ANALYSIS_DETAIL` case(요청·status 200·전후 비교)로 남아 있고 `skip` 문자열을 null로 바꾸면 실행된다.

## 발견한 제품 결함

- #198(PR2의 context_budget demo 분기): demo에서 `GET /admin/sessions/{id}/analysis`가 503 `DEMO_CONFIGURATION_INVALID`다. `load_analysis_response` → `load_plan` → `build_plan` → `estimate_request` → `context_budget.provider_parameters`가 `PROVIDER_ADAPTERS["demo"]`를 찾다 `KeyError: 'demo'`(`src/services/context_budget.py:13`). 재현: `provider_parameters(TextRequest("demo","demo-classroom-v1","analysis","",[],{"max_output_tokens":4096},"r"))` → KeyError. 이 분기는 coordinator 결정으로 #198 PR2(w198) 담당이다.
