# D3-01 데모 UI 증거 (#199)

계약: `docs/demo/d0-03-contract-v1.md` `d0-03-v1-r3` 의미(§9.1, §10, §11, §24, §24.1, §25, §26)와 #190 변경 기록(`demo:state-check`).
범위:
- PR① 기반: layout data/JSON/배너/공개 링크, CSRF reader, 자료 링크 `ds/dg/da`, 오류 화면 복구 링크.
- PR② 투어: `static/js/demo-tour.js`, 투어 패널, 화면 fixture, 브라우저 검사. PR #240 검토 수정 두 번(r1·r2)을 포함한다.
- PR③: `demo.html` 진입 화면과 `demo_entry` 본문, `error.html`의 `demo_error` 복구 key, partials의 §19 출처 표시.

실제 backend와 실제 `demo_config`로 다시 돌리는 검사는 아직 하지 않았다.

## 무엇을 확인했나

| 계약 | 확인 방법 | 결과 |
| --- | --- | --- |
| §24 normal 무변경 | `tests/test_demo_d3_01_templates.py`: `demo_config`가 없으면 base `3b967f1` 템플릿과 같은 조각, normal shape는 JSON 블록만 추가, 투어 패널·스크립트 없음 | 통과 |
| §24 demo DOM | 같은 파일: root `data-demo-*`, 안전 JSON(허용 필드만, `</script>` escape), 배너, 패널 6단계·조작 순서 | 통과 |
| §10 CSRF reader | 같은 파일: demo에서 `demo_csrftoken`, normal에서 `csrftoken` | 통과 |
| §24.1 여섯 단계 map 표 | `tests/browser_demo_d3_01_ui.mjs` `expectedMapTable`: 표 14행(18 사례)을 `computeSteps`로 재현 | 통과 |
| §24.1 관찰 수용·저장 | `observationsAndStorage`: 잘못된 scope/세대/epoch/대상/kind/UUID는 무시, key `demo:<scope>:<g>:tour-v1`(epoch 없음), 새로고침 후 유지, 두 탭 storage event 합집합 | 통과 |
| §9/§9.1 제어 POST | `roleSwitch`, `lostResponse`, `csrfInvalid`: 소문자 UUID request_id, CSRF header, 응답 유실 시 같은 ID로 조회하고 다시 POST하지 않음, 403 CSRF는 state GET 뒤 같은 ID 조회 | 통과 |
| §24 reset | `resetFlow`: 확인 취소 시 POST 0, 202→폴링→ready 뒤에만 `demo:reset-completed`, 이전 패널에 여섯 완료 1회와 완료 안내, 이전 key 삭제, 새 세대 `X P P P P P` | 통과 |
| §10/§25 단조 적용 | `lowerGenerationIgnored`: 더 낮은 세대 응답은 적용·이벤트 0 | 통과 |
| 변경 3 `demo:state-check` | `stateCheckEvent`: 힌트 뒤 state GET, 이벤트 상태는 GET 결과 | 통과 |
| §25 탭 통지 | `tabNotification`: 다른 scope 무시, 더 큰 세대는 state GET만, 통지 detail 직접 적용 0 | 통과 |
| §24.1 투어 CSV | `tourCsv`: 정상 응답만 Blob 다운로드(BOM 유지)와 `csv_received`; echo/행수/31열/다른 session_id/500/seed 출처는 관찰 0 | 통과 |
| §24 안내 조작 | `guideActions`: 닫기·건너뛰기·재개·다시 시작, focus 복귀, 새로고침 뒤 유지 | 통과 |
| §11 만료 | `expiredEnvelope`: 410 → `demo:expired`, 안내 표시, 조작 숨김 | 통과 |
| §9.1 상태 동기화 (r1) | `firstStateGet`, `stateCheckEvent`: 먼저 namespace 헤더 없는 현재 상태 GET으로 namespace와 cookie를 맞춘 뒤, 확인된 헤더로 사실을 다시 받는다. 오래된 탭(epoch 1, 서버 2)도 복구된다. stub은 서버 경계를 그대로 따른다(ready + include_tour일 때만 헤더 검증) | 통과 |
| reset 실패·재시도 (r1, r2) | `resetFailure`: resetting(세대 2 예약, 사실 없음)과 reset_failed에서는 완료를 표시하지 않고 이전 여정을 유지한다. 끝난 실패는 상태 GET과 같은 ID의 결과 조회 뒤 ID를 버리고, 새 시도는 새 ID와 세대 2/epoch 2를 보낸다. 같은 세대의 재시도가 성공하면 `demo:reset-completed`가 정확히 한 번 나간다 | 통과 |
| §24.1 CSV UTF-8 (r1) | `tourCsv` `utf8` 사례: 잘못된 바이트 0xff가 있으면 다운로드와 관찰이 0이다 | 통과 |
| PR③ 진입 화면 | `test_demo_d3_01_templates.py` 진입 화면 사례: 투어 패널 없이 시작 버튼 두 개, 보관 기간 값 표시. `entryPage`: 401 안내 → 503 `Retry-After` 안내와 ID 폐기 → 새 ID로 시작 → 상태 GET → `next_url` 이동. `/demo` route 검사(`tests/test_demo_d1_03_session.py`)가 그대로 통과한다 | 통과 |
| §11 HTML 오류 (PR③) | 같은 파일의 오류 화면 사례와 `demoErrorPage`: 403/404가 아닌 demo 상태는 복구 key 문구를 제목으로 쓰고 `/demo` 링크를 둔다. `demo_error`가 없거나 None이면 normal markup과 같다 | 통과 |
| §19 출처 (PR③) | partials 사례: provenance 객체가 있을 때만 `record_origin`을 표시한다. 삭제된 세션 표시는 그대로이고, modal 링크에는 demo에서만 `ds/dg/da`를 붙인다 | 통과 |
| 접근성·배치 | `layout`: 390×844, 1440×900, 720×450(1440의 200% 확대)에서 버튼이 보이고 잘리지 않으며 가려지지 않음, 가로 스크롤 없음, 패널이 `<main>`과 겹치지 않음, Tab으로 닫기 도달, `aria-live="polite"` | 통과 |

## 실행 기록

모두 2026-10-20, WSL2, Python 3.12(uv venv), Node 24.21.0, Chromium(Playwright), 직렬(worker 1) 실행이다.

| 리비전 | 명령 | 범위 | 결과 |
| --- | --- | --- | --- |
| `5a1f224` (clean; 미추적 증거 문서만) | `uv run --frozen python -m pytest -q tests/test_demo_d3_01_templates.py tests/test_html_error_pages.py tests/test_uiux_entry.py tests/test_display_language.py tests/test_i18n_catalog.py tests/test_demo_d1_03_session.py tests/test_demo_d1_04_app.py tests/test_scenario_screens.py tests/test_uiux_admin_http.py` | 대상 9개 파일 | 257 passed, 207 s |
| `5a1f224` | `uv run --frozen ruff check .`, `uv run --frozen black --check .` | 전체 | 통과 |
| `5a1f224` | `npm run test:browser -- demo_d3_01 uiux` (heavy lock) | 38개 모듈 | 38 PASS, 0 FAIL, 509 s |
| `5a1f224` 이전 작업 트리 | `npm run test:browser -- demo_d3_01` 반복 | 1개 모듈 | 관찰을 첫 상태 적용 전에 버리던 경합 수정 뒤 연속 4회 PASS. 수정 전 실패 2회는 경합(수정함)과 부하 평균 42에서 30초 timeout |

| `012dabd` (r1) | `npm run test:browser -- demo_d3_01` | 1개 모듈 | 연속 3회 PASS |
| `8008bec` (r2) | `npm run test:browser -- demo_d3_01` | 1개 모듈 | PASS |
| `0342ca8` (PR③ + r2 병합, clean) | 위와 같은 대상 pytest 9개 파일 + `tests/test_demo_d3_03_i18n.py` | 대상 10개 파일 | 315 passed, 1 failed: `test_i18n_catalog.py`. 원인은 #199가 쓰는 `demo.*` key 20개가 아직 catalog에 없는 것(#201에 요청함) |
| `0342ca8` | `npm run test:browser -- demo_d3_01 html_errors uiux` (heavy lock) | 39개 모듈 | 39 PASS, 0 FAIL, 1288 s(lock 대기 포함) |

| `8f10472` (catalog key 추가) | `pytest -q` 위 대상 + `tests/test_demo_d3_02_guards.py` | 대상 11개 파일 | 324 passed. `test_i18n_catalog.py` 포함 |
| `8f10472` | `npm run test:browser -- demo_d3` (heavy lock) | 2개 모듈 | 2 PASS |

브라우저 실행이 다시 만든 `tests/uiux/evidence/**` 추적 파일은 커밋하지 않고 되돌렸다.

## 화면

`d3-01-evidence/layout-390x844.png`, `layout-1440x900.png`, `layout-720x450.png` (관리자, 분석까지 완료한 상태).
PR③에서 #199용 `demo.*` key 20개를 catalog에 더한 뒤(`8f10472`, #190 결정) 다시 캡처했다. 이제 실제 한국어 문구가 보인다.

## 아직 아닌 것

- 실제 backend 통과가 아니다. 제어 HTTP·CSV는 Playwright route 가로채기 stub이며 응답 형태는 `src/demo/session.py`의 `DemoState`/`TourFacts` 필드를 따른다(계약 §28).
- 실제 `demo_config`(#194 3단계, main에 병합됨)로 같은 화면을 다시 돌리는 검사, 여정 완주, 두 방문객 격리, BFCache 복구는 PR③과 #202/#203/#204 몫이다.
- 전체 회귀는 PR head의 CI가 담당한다(Addendum H1).
