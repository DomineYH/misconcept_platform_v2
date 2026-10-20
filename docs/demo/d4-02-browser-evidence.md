# D4-02 독립 브라우저 여정 검증 evidence (#204)

계약: `d0-03-v1-r3` 의미(문서 `d0-03-v2`). 최종 acceptance는 아래 [G4 최종 실행](#g4-최종-실행--후보-de3054a-2026-10-20)만 인정한다. 실행 1–6은 작성 중 확인 기록이며 완료 증거가 아니다.

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

## 실행 4 — Phase 2 (2026-10-20, 작성 중 확인)

- 기준: main `1a40dc2`(#198 PR2 context_budget demo 분기, #194 S4 `demo_seed` → `load_seed(DEMO_SEED_DIR)` 병합 포함) 위 `feat/204-final` `2f9c6ba`. 이 문서는 실행 뒤 같은 branch의 다음 commit에서 수정했고 test 파일은 바뀌지 않았다.
- 환경: WSL2 Linux(8 core, load average 약 20), Python 3.12.3(uv venv), Node 24, Playwright Chromium headless. 실행 tree는 `git archive 2f9c6ba`를 ext4 임시 디렉터리에 푼 것이다. 저장소가 있는 `/mnt/d`(drvfs)에서는 demo provider가 호출마다 fixture 84개를 다시 읽느라(`src/demo/provider.py` `_fixture`) 약 10 s가 걸려 seed의 학생 첫 출력 제한 5 s(`src/demo/seed.py` `DEMO_TIMEOUTS`)를 넘고 `first_output_timeout`으로 실패한다. 이는 실행 위치 문제이며 ext4(CI 포함)에서는 재현되지 않았다.
- 명령: `flock /tmp/mp2-heavy.lock -c 'npm run test:browser -- browser_demo_isolation browser_demo_journey browser_demo_recovery'`(worker 1개, 5분 7초). log: `logs-w204/p2/final.log`.
- receipt: 모듈마다 helper가 seed CLI를 실제 실행한다. `source_commit` `2f9c6bae8105dc958f9807967fb42edbfa1b8aab`, `seed_sha256` `0060fac9…d2f6`(isolation), `3c23e4e9…044d`(journey), `50952ea6…aede`(recovery), 나머지는 위 공통 값과 같다.

| 모듈 | 결과 | 범위 |
| --- | --- | --- |
| `browser_demo_isolation.mjs` | failed: #198 결함 1건만. 나머지 검사는 모두 통과 | 실행 1–3 항목 + seed 세션 관리자 분석 상세 |
| `browser_demo_journey.mjs` | failed: 4단계 `POST /sessions/2/end` 503(#198). 1–3단계와 B 격리 통과, 5–7단계 not run | A 여섯 단계·CSV·삭제·reset, B |
| `browser_demo_recovery.mjs` | failed: I2의 종료 세션 이력 `GET /sessions/2` 503(#198). I1·I3·I4·I5·I6·I7 passed | I 복구·언어·접근성 |

이번 실행에서 실제 앱으로 확인한 것(앱 origin interception 없음, 제품 seed 바인딩, 테스트 측 seed 바인딩 제거):

- A 1–3단계(§24·§24.1, 체험 명세 §7): 교사 start → 시나리오 → B 입력·공통 입력으로 완료 턴 2·자동 코칭 1·미개입. 단계마다 root `data-demo-*`가 `/demo/state`와 같고 투어 map(`C X P P P P` → `C C X P P P` → `C C C X P P`)과 TourFacts(`teacher_entered`, `scenario_created`·`journey_session_id`, `dialogue_complete`)가 일치.
- B(§28 1행·§3): A의 대화 뒤 B의 `/metrics` 총계·사용자·그룹·세션 목록 불변. A의 세션 ID와 학생 run ID를 B의 namespace로 요청(`/admin/sessions/<id>/download`, `/admin/sessions/<id>/analysis`, `/runs/<run>`, `/sessions/<id>/runs?request_id=`) → 404 `DEMO_RESOURCE_NOT_FOUND`, no-store, A의 본문 없음.
- I1: 새로고침·뒤로·앞으로 뒤 root namespace가 `/demo/state`와 같고 미전송 초안 보존, 이동 중 POST 0. `pageshow.persisted`는 Chromium headless에서 항상 false(기록만, BFCache 복원은 확인 불가).
- I2: start·send·end 이중 클릭에 `POST /demo/start` 1, `turns/stream` 2(두 번 전송), `/end` 1, `/metrics` 세션 +1. 종료 세션 이력 화면은 #198 결함으로 확인 실패.
- I3: 투어 닫기/건너뛰기/재개는 패널·localStorage `demo:<scope>:<gen>:tour-v1` 플래그만 바꾸고 TourFacts·map 불변, POST 0.
- I4: 두 번째 탭의 role 전환·reset 뒤 첫 탭에 `demo.stale_role`·`demo.stale_generation` 안내, 입력 잠금. 첫 탭의 이전 namespace 쓰기 → 409 `DEMO_STALE_ROLE`/`DEMO_STALE_GENERATION`, 총계 불변.
- I5: 미전송 초안이 있으면 언어 전환이 `common.guard_input`으로 거절되고 초안 유지. 빈 입력에서 en 전환 시 한국어 콘텐츠·투어 map·namespace 유지.
- I6: 390×844와 200% 확대(1280×844의 절반 viewport 640×422)에서 입력·전송·종료 조작이 viewport 안에서 가려지지 않음. 키보드만으로 start·전송 완료.
- I7: helper 테스트 시계(대체 ②, `ADVANCE`)로 idle TTL을 넘기면 `/demo/state` 410 `DEMO_EXPIRED`, 이전 namespace URL 거절, GET/이동으로 새 scope가 생기지 않고 명시 start로만 새 `browser_scope_id`.

스크린샷·내려받은 CSV는 실행 tree의 `test-results/d4-02/`(gitignore)에 남고 commit하지 않았다. 최종 G4 실행분만 `docs/demo/d4-02-evidence/`에 둔다.

## 실행 5 — Phase 3, #198 수정 뒤 (2026-10-20, 작성 중 확인)

- 기준: `feat/204-final` `e5a9887`. origin/main `fbbcad7`(#251 = #198 PR4 `ae8c76d`: context_budget demo 분기, `/end`·`/analyze` demo adapter, close_session demo scope, 분석 REQUEST_CONFLICT envelope; #250 seed provenance)을 병합했다. 이 문서는 실행 뒤 다음 commit에서 수정했고 test 파일은 바뀌지 않았다.
- 환경: WSL2 Linux(load average 약 2–3), Python 3.12.3(uv venv), Node 24, Playwright Chromium headless. 실행 tree는 `git archive e5a9887`를 ext4 임시 디렉터리에 푼 것이다(실행 4와 같은 drvfs 이유).
- 명령: `flock /tmp/mp2-heavy.lock -c 'npm run test:browser -- browser_demo_isolation browser_demo_journey browser_demo_recovery'`(worker 1개). log: `logs-w204/p3/final.log`.
- receipt: `source_commit` `e5a988709f1f…`, `logical_data_sha256` `93808c612ae6496a31f100dde1ce5c6780a3a6fd8cd136b82bc871eb6d746141`(#250으로 바뀜), `manifest_sha256` `60cc355c…0516`, `seed_sha256` `dcce23bb…89ec`(isolation)·`e12f2872…abf5`(journey)·`40f34e23…5e94`(recovery).

| 모듈 | 결과 | 범위 |
| --- | --- | --- |
| `browser_demo_isolation.mjs` | passed (22.8 s) | 실행 1–3 항목 + seed 세션 관리자 분석 상세(`ANALYSIS_DETAIL`) |
| `browser_demo_journey.mjs` | failed (412.9 s): 투어 map·관찰 7건만, 아래 결함 하나에서 나옴. 자료 단계 1–7은 모두 통과 | A 여섯 단계·CSV·삭제·reset, B |
| `browser_demo_recovery.mjs` | passed (21.7 s): I1–I7 | I 복구·언어·접근성 |

이번 실행에서 처음 실제 앱으로 통과한 것:

- A 4단계: 이중 확인 뒤 `POST /sessions/<id>/end` 200/202, 분석 modal 200, TourFacts `analysis_ready`, 이력 화면 `/sessions/<id>` 분석 결과 표시.
- A 5단계: UI로 관리자 전환(`auth_epoch`+1, root namespace 일치), 관리자 세션 목록에서 대상 분석 열람, `GET /admin/sessions/<id>/analysis` 200. 내려받은 파일 `demo_d0-02-v1_admin_session-<id>.csv` 파싱: 관리자 27열 + suffix 5열 순서, 6행(teacher 2·student 2·tutor 1·summary 1), 모든 행 대상 session_id·`prepared_example`·`false`·`visitor_generated`, 교사 식별 열 채움. 투어 CSV 버튼은 숨겨져 있어(아래 결함) 같은 관리자 export인 행의 CSV 링크로 받았다.
- B: A의 대화 뒤와 분석·CSV 뒤 두 번, B의 총계·사용자·그룹·세션 목록 불변, A의 세션·run ID는 B에서 404 `DEMO_RESOURCE_NOT_FOUND`.
- A 6단계(§22): 교사 삭제 → TourFacts `teacher_deleted`, 새 CSV 6행에 교사 식별 열 빈 값. 세션 삭제 → `session_deleted`, 단일 다운로드·관리자 분석 API·modal·`/sessions/<id>`·`/sessions/<id>/analysis` 404, 전체 export에 해당 행 없음, 선택 export CSV 없음, 목록 API·화면에 없음.
- A 7단계: UI reset 응답 뒤 새 generation, 이전 탭의 `POST /demo/role`과 이전 namespace URL은 409 `DEMO_STALE_GENERATION`, 새로고침 뒤 새 세대 root·TourFacts 초기값·map, B 불변.
- I2: start·전송·종료 이중 클릭 뒤 POST 수 1/2/1, `/metrics` 세션 +1, 종료 세션 이력 200, 분석 JSON의 teacher/student 메시지 2/2. 분석 실행 중 role 전환은 409 `DEMO_BUSY`(retryable, `demo.wait_or_stop`)이므로 검사는 그 동안 재시도한다.

실패(모두 같은 결함, 단계를 멈추지 않도록 모아서 끝에 실패시킴): map `step 4`·`step 5 after the role switch`(`C C C X P P` ≠ `C C C C X P`), 관찰 `admin_record_viewed` 없음, `#demo-tour-csv` 숨김, map `step 5`·`step 6 before reset`(`C C C X P P` ≠ `C C C C C X`), reset 직후 일회 완료 표시(`X P P P P P` ≠ `C C C C C C`; 완주하지 않은 여정은 완료로 표시하지 않는 §24.1 규칙의 결과).

## 실행 6 — Phase 4, D5 수정 확인 (2026-10-20, 작성 중 확인)

- 기준: `feat/204-final` `eda6f0c`. 확인용으로 **scratch clone에서만** `origin/fix/d5-tour-facts` `419c66d`(D5: `DemoRequestDB.display()`가 `demo_config.tour_facts`를 채움, `src/api/dependencies.py`)를 병합했다(scratch commit `54be319`, branch에는 commit하지 않음; main에는 D5 자체 PR로 들어온다). 이 문서는 실행 뒤 다음 commit에서 수정했다.
- harness 변경(`eda6f0c`): helper `DEMO_REQUESTS_PER_MINUTE=600`(설정 최대; 100 ms `/demo/state` poll이 기본 60/분을 넘어 `/admin/sessions-page`가 429였다), journey `facts()`가 navigation(교사 삭제 alert 뒤 users 화면 reload)에도 재시도, 투어 map·`admin_record_viewed` 관찰·투어 CSV 버튼을 다시 hard assertion으로 되돌리고 행 링크 대체 경로를 제거했다.
- 환경: ext4 scratch clone(`git clone --shared`), WSL2 Linux(load average 약 2.5), Python 3.12.3(uv venv), Node 24, Playwright Chromium headless.
- 명령: `flock /tmp/mp2-heavy.lock -c 'npm run test:browser -- browser_demo_isolation browser_demo_journey browser_demo_recovery'`(worker 1개). log: `logs-w204/p4/final.log`, scratch 병합 범위 `logs-w204/p4/scratch-merge.txt`.
- receipt: `source_commit` `54be319`(scratch 병합 commit), `logical_data_sha256` `93808c61…6141`, `manifest_sha256` `60cc355c…0516`, `seed_sha256` `eeddc3af…8f67`(isolation)·`f4358c58…0914`(journey)·`e3f68d21…5aff`(recovery).

| 모듈 | 결과 |
| --- | --- |
| `browser_demo_isolation.mjs` | passed (20.0 s) |
| `browser_demo_journey.mjs` | passed (25.2 s): A 여섯 단계 전부 — 단계별 map `C X P P P P` → … → `C C C C C X`, reset 성공 일회 `C C C C C C`, `history_viewed`·`admin_record_viewed` 관찰, 투어 CSV 버튼으로 받은 파일 파싱(27+5열, 6행, 출처값), 삭제 §22, reset·이전 탭 409, B 격리 |
| `browser_demo_recovery.mjs` | passed (42.4 s): I1–I7 |

D5 없이 이 branch만으로는 실행 5와 같이 투어 map·관찰·투어 CSV 검사가 실패한다(이제 hard assertion이므로 해당 단계에서 멈춘다). 세 모듈 모두 통과하려면 D5가 main에 있어야 한다.

## G4 최종 실행 — 후보 de3054a (2026-10-20)

- 후보(`candidate_commit`): main `de3054a00282e1bbfc97784918059f653ac08811`. D3 제품 PR(#194–#201 최종 PR, #248(#200), #251(#198), #250(D2), #252(D5), #249(flake))을 모두 포함한다. 후보에는 `docs/demo/d3-04-integration-manifest.json`이 없어 SHA는 coordinator 지시(G4 order)로 고정했다.
- 실행 commit(`tested_commit`): `feat/204-final` `ca1e5f2b00adc767617eec0aae2725a08cf2e848` = 후보 + 이 branch의 test·doc commit(병합 `ca1e5f2`, 충돌 없음). 후보 대비 제품 diff 0(`tests/`·`docs/`만 다름). 이 문서와 `d4-02-evidence/`는 실행 뒤 다음 commit에서 추가했다.
- 계약: `d0-03-v1-r3`.
- 환경: 로컬 WSL2 Linux(시작 load average 4.6), Python 3.12.3(uv venv, `--frozen`), Node v24.21.0, Playwright Chromium 153.0.8010.12 headless, worker 1개. 실행 tree는 committed tree의 ext4 `git clone --shared`(clean, untracked `node_modules` symlink만). 앱 origin interception 없음, 외부 AI 호출 없음.
- 명령: `flock /tmp/mp2-heavy.lock -c 'npm run test:browser -- browser_demo_isolation browser_demo_journey browser_demo_recovery'`, exit 0.
- receipt(모듈마다 helper가 seed CLI `python -m src.demo.seed build --out <tmp>/seed`를 실제 실행): `source_commit` `ca1e5f2b00adc767617eec0aae2725a08cf2e848`, `db_schema_version` `037_demo_provider.sql`, `logical_data_sha256` `93808c612ae6496a31f100dde1ce5c6780a3a6fd8cd136b82bc871eb6d746141`, `manifest_sha256` `60cc355c4a5cee0cf1316bb97d8c52b09a03e39bb242514f66062e34159c0516`, `seed_sha256`(물리 bytes, 기동마다 다름) `7f8072b1…b79a`(isolation)·`fa59041f…7e5b`(journey)·`b2b4612d…da59`(recovery). 전체 값은 log의 `RECEIPT` 행.

| 모듈 | 후보 | 실행 commit | 결과 | 범위 | log |
| --- | --- | --- | --- | --- | --- |
| `browser_demo_isolation.mjs` | `de3054a` | `ca1e5f2` | passed (23.5 s) | 실행 1–3 항목(§9·§9.1·§10 쿠키·CSRF·namespace, B/F 격리, normal mode 404) + seed 세션 관리자 분석 상세 | [`browser-de3054a.log`](d4-02-evidence/browser-de3054a.log) |
| `browser_demo_journey.mjs` | `de3054a` | `ca1e5f2` | passed (28.5 s) | A 여섯 단계: 단계별 root `data-demo-*`·TourFacts·map `C X P P P P` → … → `C C C C C X`, reset 성공 일회 `C C C C C C`; `admin_record_viewed` 관찰은 직접, `history_viewed`·`csv_received`는 그것이 있어야 넘어가는 map 단계(4·5단계 완료)로 확인; 투어 CSV 버튼 파일 파싱(§23 27+5열, 6행, 출처값); 삭제 §22; reset 뒤 이전 탭·URL 409; B 격리 세 번(§28) | 같음 |
| `browser_demo_recovery.mjs` | `de3054a` | `ca1e5f2` | passed (35.5 s): I1–I7 | I 복구·이중 클릭·투어 닫기·두 탭·ko/en·390 px/200%/키보드·만료 재진입(테스트 시계) | 같음 |
| 전체 pytest·전체 browser suite·live(H1) | `de3054a` | — | not run (로컬): PR head의 CI가 실행한다 | 회귀 전체 | CI |

`d4-02-evidence/`의 파일(모두 이 실행에서 생성, 내용은 demo fixture·seed 자료뿐이며 비밀·cookie·host 경로 없음):

| 파일 | 내용 |
| --- | --- |
| [`browser-de3054a.log`](d4-02-evidence/browser-de3054a.log) | 머리말(후보·실행 commit·계약·환경·명령·exit) + 러너 출력 전체 |
| [`journey-admin.csv`](d4-02-evidence/journey-admin.csv) | 5단계 투어 CSV 버튼으로 받은 `demo_d0-02-v1_admin_session-2.csv`(관리자 27열 + suffix 5열, 6행) |
| [`journey-1-teacher.png`](d4-02-evidence/journey-1-teacher.png), [`journey-4-history.png`](d4-02-evidence/journey-4-history.png), [`journey-5-admin.png`](d4-02-evidence/journey-5-admin.png), [`journey-7-reset.png`](d4-02-evidence/journey-7-reset.png) | A 1·4·5·7단계 화면(5단계: map `C C C C C X`, 투어 CSV 버튼 표시; 7단계: reset·새로고침 뒤 새 세대 초기 map) |
| [`recovery-stale-role.png`](d4-02-evidence/recovery-stale-role.png), [`recovery-stale-generation.png`](d4-02-evidence/recovery-stale-generation.png) | I4 첫 탭의 `demo.stale_role`·`demo.stale_generation` 안내 |
| [`recovery-layout-390.png`](d4-02-evidence/recovery-layout-390.png), [`recovery-layout-640.png`](d4-02-evidence/recovery-layout-640.png) | I6 390×844, 200% 확대(640×422) 배치 |

확인 한계: `pageshow.persisted`는 Chromium headless에서 항상 false라 BFCache 복원 자체는 확인하지 못했다(I1은 기록만). I7은 아래 테스트 시계를 쓴다.

## 테스트 대체

- 만료 재진입용 helper 시계: `tests/demo_d4_02_server.py`의 `TestClock`이 `src.demo.visitors.SystemClock`을 대신하고, stdin `ADVANCE <seconds>`일 때만 앞당긴다. I7만 사용한다.
- 요청 한도: helper가 `DEMO_REQUESTS_PER_MINUTE=600`(설정 최대)을 둔다. 검사가 `/demo/state`를 100 ms마다 poll하기 때문이다.
- seed 바인딩 대체는 실행 4에서 제거했다. 제품 `src.main.demo_seed`가 helper가 빌드한 `DEMO_SEED_DIR`를 읽는다.

## 발견한 제품 결함

- 해결됨(실행 5): #198 demo 분석 계획 503(`src/services/analysis_plan.py:67` → `context_budget.provider_parameters` KeyError 'demo'). #251로 해결.
- 해결됨(G4): `demo_config.tour_facts`가 모든 demo 페이지에서 null이었다(`DemoRequestDB.display()`, `src/api/dependencies.py`, §26 #194). 그래서 관찰이 보내지지 않고 map이 `analysis`에서 멈췄다. D5(#252)로 해결되어 G4 실행의 journey가 통과했다.
- 열린 제품 결함 없음(이 세 모듈 범위).
