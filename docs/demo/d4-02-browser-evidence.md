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

## 작성 중 대체(최종 evidence 전에 제거)

1. seed: `src.main.demo_seed`가 아직 CONFIGURATION_INVALID이므로(`src/main.py:200`) helper가 `tests/test_demo_d1_04_app.py`의 `make_seed`/`install_seed`(합성 최소 seed)를 바인딩한다. #197 `load_seed`와 #194 S4 바인딩이 병합되면 제거하고 #197 CLI receipt(`seed_sha256`, `manifest_sha256`, `migration_set_sha256`)를 여기에 기록한다. 현재 receipt: 없음(#197 미병합).
2. 만료 재진입용 helper 시계(`VisitorRegistry.clock`): 아직 없음. recovery 모듈 작성 때 추가한다.

## Pending(needs G4 / #202)

- A: 여섯 단계 DOM `data-demo-step`·TourFacts, reset 후 이전 URL/탭 409, CSV 파일 파싱(27+5열, 6행, 출처값, 삭제 후 비노출).
- B: 시나리오 이름 변경·교사 삭제·대화·분석·CSV 뒤 B 불변, A의 run/세션 ID를 B에서 요청 시 404 `DEMO_RESOURCE_NOT_FOUND`.
- F: 삭제 교사 role 전환 409 `DEMO_ROLE_DELETED`, 관리자 자기 삭제 거절.
- I: 새로고침·뒤로/앞으로·BFCache, 중복 클릭, 투어 닫기/건너뛰기/재개, 두 탭 stale, 한/영 전환, 390×844·키보드·200% 확대, 만료 재진입.

## 발견한 제품 결함

없음.
