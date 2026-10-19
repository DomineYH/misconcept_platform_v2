# D3-01 데모 UI 증거 (#199)

계약: `docs/demo/d0-03-contract-v1.md` `d0-03-v1-r3` 의미(§9.1, §10, §11, §24, §24.1, §25, §26)와 #190 변경 기록(`demo:state-check`).
범위: PR① 기반(layout data/JSON/배너/공개 링크, CSRF reader, 자료 링크 `ds/dg/da`, 오류 화면 복구 링크)과 PR② 투어(`static/js/demo-tour.js`, 투어 패널, 화면 fixture, 브라우저 검사).
PR③(`demo.html` 진입 화면, partials 출처 표시)과 실제 `demo_config`로 다시 돌리는 검사는 아직 하지 않았다.

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
| 접근성·배치 | `layout`: 390×844, 1440×900, 720×450(1440의 200% 확대)에서 버튼이 보이고 잘리지 않으며 가려지지 않음, 가로 스크롤 없음, 패널이 `<main>`과 겹치지 않음, Tab으로 닫기 도달, `aria-live="polite"` | 통과 |

## 실행 기록

모두 2026-10-20, WSL2, Python 3.12(uv venv), Node 24.21.0, Chromium(Playwright), 직렬(worker 1) 실행이다.

| 리비전 | 명령 | 범위 | 결과 |
| --- | --- | --- | --- |
| `5a1f224` (clean; 미추적 증거 문서만) | `uv run --frozen python -m pytest -q tests/test_demo_d3_01_templates.py tests/test_html_error_pages.py tests/test_uiux_entry.py tests/test_display_language.py tests/test_i18n_catalog.py tests/test_demo_d1_03_session.py tests/test_demo_d1_04_app.py tests/test_scenario_screens.py tests/test_uiux_admin_http.py` | 대상 9개 파일 | 257 passed, 207 s |
| `5a1f224` | `uv run --frozen ruff check .`, `uv run --frozen black --check .` | 전체 | 통과 |
| `5a1f224` | `npm run test:browser -- demo_d3_01 uiux` (heavy lock) | 38개 모듈 | 38 PASS, 0 FAIL, 509 s |
| `5a1f224` 이전 작업 트리 | `npm run test:browser -- demo_d3_01` 반복 | 1개 모듈 | 관찰을 첫 상태 적용 전에 버리던 경합 수정 뒤 연속 4회 PASS. 수정 전 실패 2회는 경합(수정함)과 부하 평균 42에서 30초 timeout |

브라우저 실행이 다시 만든 `tests/uiux/evidence/**` 추적 파일은 커밋하지 않고 되돌렸다.

## 화면

`d3-01-evidence/layout-390x844.png`, `layout-1440x900.png`, `layout-720x450.png` (관리자, 분석까지 완료한 상태).
캡처 시점에는 #201 PR1(catalog `demo` 영역)이 아직 병합되지 않아 `demo.*` 문구가 번역 fallback("표시할 수 없는 안내입니다.")으로 보인다. 배치·조작 검사는 문구와 무관하다. #201 PR1 병합 뒤 PR③에서 다시 캡처한다.

## 아직 아닌 것

- 실제 backend 통과가 아니다. 제어 HTTP·CSV는 Playwright route 가로채기 stub이며 응답 형태는 `src/demo/session.py`의 `DemoState`/`TourFacts` 필드를 따른다(계약 §28).
- 실제 `demo_config`(#194 3단계, main에 병합됨)로 같은 화면을 다시 돌리는 검사, 여정 완주, 두 방문객 격리, BFCache 복구는 PR③과 #202/#203/#204 몫이다.
- 전체 회귀는 PR head의 CI가 담당한다(Addendum H1).
