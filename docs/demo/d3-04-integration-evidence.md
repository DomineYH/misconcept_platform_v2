# D3-04 통합 smoke 증거 (#202)

## 기준

- 계약: `d0-03-v1-r3`. 값 묶음은 [`d3-04-integration-manifest.json`](d3-04-integration-manifest.json)이 가진다.
- 최신 실행 SHA: `40ef3b6b0e59da37e43842ad9bb0f556f2559078`(실행 2). 제품 tree는 main `0cc3fab04b6580b999b0ca95ca222687f7ffea85`(#250 포함)에 #198 PR4 #251 head `ae8c76dea86aa3a7abd01cf86366e9e0e81e85c7`를 merge한 `0f1bbd2`와 같다. 그 위의 commit은 smoke 모듈과 이 문서뿐이다. main에 같은 tree가 없으므로 G4 후보가 아니다.
- 모듈: `tests/browser_demo_d3_04_smoke.mjs`.
- 실행 방식: 모듈이 자체 demo와 normal uvicorn 서버를 `TESTING=false`로 띄운다. seed는 `python -m src.demo.seed build --out <tmp>/seed`로 만든다. 러너의 fixture 화면은 backend 증거로 쓰지 않는다(계약 §28). 여정은 화면 조작(시작·대화 시작·입력·종료·투어 역할/reset 버튼·CSV 링크)으로 진행하고, 판정은 `/demo/state?include_tour=true`, 관리자 JSON(먼저 HTTP 200 확인), 내려받은 CSV를 직접 파싱해 확인한다.
- demo 서버 설정은 계약 기본값을 따르되 `DEMO_REQUESTS_PER_MINUTE`만 §8 상한 600으로 둔다. 자동 여정은 D0-02 §7의 5분 대신 수십 초에 끝나 기본 60회/분을 넘기 때문이다(실행 2 직전 시험에서 step 6이 429).
- 단계 판정: 앞 단계가 passed가 아니면 뒤 단계는 `not run (blocked by step N)`이다. 실패 단계는 약화·생략하지 않고 failed로 남긴다. 부가 확인(E*)과 normal(N1)은 별도 방문객/서버라 여정과 독립이다.
- 상태: **G4 미판정**. 실행 2는 #251이 main에 병합되기 전 branch head에서 돌렸다. G4는 고정 SHA에서 다시 실행한다.

## 실행 기록

| 실행 | SHA | 제품 기준 | 명령 | 결과 | 시간 | 로그 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 4dcdf40 | main 1a40dc2 | `npm run test:browser -- demo_d3_04` | FAIL (step 4, E6) | 모듈 47.4초, 러너 포함 약 120초 | [run-1.log](d3-04-evidence/run-1.log), [result-1.json](d3-04-evidence/result-1.json) |
| 2 | 40ef3b6 | main 0cc3fab + #251 ae8c76d | `npm run test:browser -- demo_d3_04` | **PASS** (E4 not run) | 모듈 37.9초, lock 대기·러너 포함 162초 | [run-2.log](d3-04-evidence/run-2.log), [result-2.json](d3-04-evidence/result-2.json) |

환경: Linux ext4 위의 `git clone --shared` 작업 tree(WSL2), Python 3.12(uv venv), Node 24, Playwright 1.63 Chromium. Windows 드라이브를 mount한 WSL drvfs 위에서는 `DemoProvider._fixture`가 호출마다 manifest의 모든 fixture 파일을 다시 읽고 경로를 해석하는 데 12초가 걸려 seed의 `student_first_output=5`초(`src/demo/seed.py:50`)를 넘는다. 그래서 학생 턴이 `first_output_timeout`으로 끝난다. ext4에서는 같은 호출이 0.1초다. CI(ubuntu ext4)는 영향이 없다(범위 밖, 알려진 사항).

## 단계 (실행 2)

| # | 단계 | 명령/조건 | 기대 | 실제 | 결과 | 실행 SHA |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | 시작 (§9, §10, §24, D0-02 §7) | `GET /demo` → '교사로 시작' 클릭 | `GET /demo` 200, `POST /demo/start` 201, DemoState(ready/teacher/CSRF 이름/`next_url`). banner, root `data-demo-mode/scope/generation/auth-epoch`, `#demo-config`에 factory/path/secret/root 없음, map `C X P P P P` | 기대와 같음 | passed | 40ef3b6 |
| 2 | 시나리오 (§24, D0-02 §7) | '대화 시작' → 새로고침 | 새 세션 1개(seed 세션과 다른 ID, facts `journey_session_id`), 학생 응답 0개, 새로고침 뒤 같은 세션, map `C C X P P P` | 기대와 같음 | passed | 40ef3b6 |
| 3 | 대화/멘토 (§15–§19, D0-02 §4) | B 입력 → 공통 입력 (화면 전송) | 완료 학생 턴 2, 저장 코칭 1(첫 턴 자동 코칭, 공통 턴 자동 검사는 메시지 0), stream 4개 모든 provenance `prepared_example`/`external_ai_called=false`/`visitor_generated`, facts `dialogue_complete`, 새로고침 뒤 map `C C C X P P` | 기대와 같음 | passed | 40ef3b6 |
| 4 | 분석 (§18, §22) | '대화 종료 후 분석 보기' → 확인 | `POST /sessions/{id}/end` 202, 채택 분석 `feedback_status=ok`·분포 r1 0/r2 1/r3 1·provenance `visitor_generated`, facts `analysis_ready`, 이력 재열람 시 POST 0·분석 JSON 불변 | 기대와 같음 | passed | 40ef3b6 |
| 5 | 관리자/CSV (§9, §23) | 투어 '관리자로 전환' → 대화목록 → 행 CSV 링크 | `/demo/role` 200, `auth_epoch`+1, 목록 {seed, 새 세션}, 분석 provenance `seed_prepared`/`visitor_generated`, 파일명 `demo_d0-02-v1_admin_session-<id>.csv`, 32열 순서, 6행(교사2·학생2·tutor1·summary1), suffix `prepared_example,false,d0-02-v1,demo-fixture-v1,visitor_generated` | 기대와 같음 ([step5-admin.csv](d3-04-evidence/step5-admin.csv)) | passed | 40ef3b6 |
| 6 | 삭제/reset (§7, §22, §25) | 교사 삭제 → 세션 삭제 → 투어 '내 데모 초기화' | 교사 삭제 200, CSV 교사 식별 열 빈 값, teacher 전환 409 `DEMO_ROLE_DELETED`, facts `teacher_deleted`, 세션 삭제 200 뒤 detail 200(tombstone)/404·download 404·analysis 404 모두 본문 없음, facts `session_deleted`, reset 200/202·generation+1, 이전 세대 URL 409 `DEMO_STALE_GENERATION`, 새 목록은 seed 세션만, teacher 전환 200 | 기대와 같음 | passed | 40ef3b6 |
| E1 | 완료 이력 열기 (§22, D0-02 §7) | 교사로 seed 완료 세션 `/sessions/1` 열기 | 200, 분석 영역 표시, POST 0, 분석 JSON 불변 | 기대와 같음 | passed | 40ef3b6 |
| E2 | ready 상태 역할 전환 (§9) | `POST /demo/role` teacher→admin, 같은 역할 재요청 | 200·`next_url` `/admin/…`, generation 유지, `auth_epoch`+1, 이전 epoch URL 409 `DEMO_STALE_ROLE`, 같은 역할은 no-op | 기대와 같음 | passed | 40ef3b6 |
| E3 | 한영 UI 전환 (§24 `demo.*`, §19) | 헤더 언어 버튼 EN → KOR | `lang=en`, banner·투어 제목이 catalog `demo.banner`/`demo.tour.title` 영어, `#demo-config` `ui_language=en`이고 content/fixture 버전 불변, 되돌리면 한국어 | 기대와 같음 | passed | 40ef3b6 |
| E4 | 만료 후 재진입 (§7, §11) | — | 410 → 새 start | `DEMO_IDLE_TTL_SECONDS` 하한이 300초(`src/demo/visitors.py:49`)라 CI shard에서 5분 대기를 할 수 없다 | not run | 40ef3b6 |
| E5 | 비교 직접 요청 (§20) | 관리자로 `POST /admin/comparison-conversations`, `POST /admin/comparisons` | 둘 다 403 `DEMO_ACTION_FORBIDDEN` | 기대와 같음 | passed | 40ef3b6 |
| E6 | seed 완료 예시 출처 (§19, §23; 5단계의 seed 쪽) | 관리자로 seed 세션 CSV와 `/admin/sessions/1/analysis` | CSV 모든 행 suffix `…,seed_prepared`, 분석 200 + `provenance.record_origin=seed_prepared` | 기대와 같음 | passed | 40ef3b6 |
| N1 | normal (§2, §9, §26) | `APP_MODE=normal`, `TESTING=false`, 임시 SQLite(앱 기동 migration), `BOOTSTRAP_ADMIN_ON_STARTUP=true` | `/health` 200, `/login` 200 → admin 로그인 → `/scenarios`, `demo_config.mode == "normal"`, `/demo`·`/demo/state` 404, `demo_` cookie 없음 | 기대와 같음 | passed | 40ef3b6 |

화면 자료(실행 2): [step1-start.png](d3-04-evidence/step1-start.png), [step3-dialogue.png](d3-04-evidence/step3-dialogue.png), [step4-history.png](d3-04-evidence/step4-history.png), [step6-reset.png](d3-04-evidence/step6-reset.png), [e3-english.png](d3-04-evidence/e3-english.png).

## 결함 원장

| 단계 | 결함 | file:line | 소유 | 상태 |
| --- | --- | --- | --- | --- |
| 4 | D1: demo 세션 종료와 관리자 분석 응답의 계획 추정이 `PROVIDER_ADAPTERS["demo"]` `KeyError`로 503 `DEMO_CONFIGURATION_INVALID`가 됐다. | `src/services/analysis_plan.py:65-67` → `src/services/context_budget.py:13` (실행 1 기준) | #198 | #251(head ae8c76d)에서 수정. 실행 2에서 확인. #251 미병합 |
| 4 | D1b: `analysis_runs.py`가 `execution["demo_adapter"]`를 읽지만 넣는 코드가 없어 demo 분석이 `configuration_unavailable`로 거절됐다. | `src/services/analysis_runs.py:379-380`, `src/services/call_admission.py:85-96` (실행 1 기준) | #198 | #251에서 수정. 실행 2에서 확인. #251 미병합 |
| E6 | D2: seed 완료 예시 분석 JSON에 §19 `provenance`가 없었다. | `src/demo/seed.py:519-530`, `src/api/routes/admin_session_actions.py:246-258` | #202(D2, coordinator 배정) | #250(main 0cc3fab)에서 수정. seed `logical_data_sha256`이 `93808c61…`로 바뀜. 실행 2에서 확인 |

실행 1의 E6 실패는 상태 200을 먼저 확인하지 않아 D1의 503을 provenance 누락으로 보고한 것이기도 했다. 실행 2부터 분석·목록 JSON은 200을 먼저 확인한다. 실행 2에서 새 제품 결함은 없다.

## 범위 밖

- D4 시나리오(두 방문객, 경합, 두 탭, 390px/키보드/확대, 복구 matrix)는 #203/#204 소관이다.
- 제품 파일은 이 branch에서 바꾸지 않았다(D2 제품 수정은 별도 PR #250). 이 branch의 #202 파일은 smoke 모듈, 이 문서, manifest, evidence 폴더뿐이다.
- socket/SDK egress guard는 넣지 않았다(외부 AI 무호출 검증은 §28 #203/#204). smoke는 stream provenance의 `external_ai_called=false`만 확인한다.
- G4 판정과 전체 회귀(pytest, ruff, black, browser full, live)는 포함되지 않는다. 전체 회귀는 PR head 또는 G4 SHA의 CI 결과를 쓴다.

## manifest

값 묶음(후보/계약/fixture/seed/migration/검사)은 [`d3-04-integration-manifest.json`](d3-04-integration-manifest.json)을 본다. `settings_sha256`과 `g4_final_commit`은 null이며 이유는 manifest의 `notes`에 있다.
