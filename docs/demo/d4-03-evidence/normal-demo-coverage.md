# normal/demo CI 적용 범위 평가 (#205)

평가 기준: 수정 전 기준선 main `ba64b82ef74226094a5b51888b1bbcb1734bb062`, 같은 SHA의 `.github/workflows/tests.yml`. 1단계(main `2dc7840`) 평가를 대체한다. 릴리스 후보 `8f97776`(D6 수정)에서 재확인: `ba64b82` 대비 diff는 `src/services/analysis_runs.py`와 새 `tests/test_lock_order.py`뿐이고 `tests.yml`·browser 모듈은 같다. pytest 수집은 3167(normal 프로세스 test 1개 추가), browser 148 그대로이며 아래 job/step 대응은 바뀌지 않는다. 읽기 평가이며 이 문서 작성 중 전체 회귀는 실행하지 않았다(수집 수치는 `pytest --collect-only` 결과). 기준선 전체 회귀는 CI run 39392009613이며 로그는 [인수 기록](../d4-03-release-acceptance.md) §3.2에 있다.

결론: **normal과 demo가 모두 기존 job에서 실행되므로 tests.yml은 변경하지 않는다.** 계약 §26의 #205 CI 쓰기 권한은 아래 "변경이 필요해지는 조건"이 생길 때만 쓴다. ba64b82에서는 그 조건이 하나도 생기지 않았다.

## mode가 정해지는 방식

- 서버 mode는 프로세스 시작 시 `APP_MODE` 환경변수로 한 번 정해지고 기본값은 `normal`이다(`src/config.py:23`, 설정 필드 기본값 `src/config.py:112-113`). 실행 중 전환은 없다.
- CI job은 `APP_MODE`를 설정하지 않는다(`tests.yml`에 `APP_MODE` 없음, job 수준 `env:` 없음, `env:`는 `:63`의 집계 step 하나뿐). pytest의 `tests/conftest.py:8`은 import 전에 `TESTING=true`와 메모리 SQLite를 고정하므로 pytest 프로세스 자체는 normal이다.
- 따라서 demo 프로세스 검사는 각 테스트가 `APP_MODE=demo` 하위 프로세스를 직접 띄워 같은 pytest job 안에서 실행한다. demo 구성 요소 단위 검사는 `src/demo/*`와 명시적 `app_mode` 인자를 프로세스 mode와 무관하게 직접 호출한다.

## job/step별 증명 범위

| CI job / step (`tests.yml`) | normal | demo |
| --- | --- | --- |
| `test` / `ruff check .` (:19), `black --check .` (:20) | 전체 코드 | 전체 코드 (`src/demo/`, `tests/test_demo*.py` 포함) |
| `test` / `pytest -q --durations=30` (:21) | 전체 suite 3166개 수집(`testpaths = ["tests"]`, `pyproject.toml:63`), normal 프로세스 | `tests/test_demo*.py` 28개 파일 1153개 수집(아래 표) |
| `test` / `tests/check_student_live.py` (:22) | 실제 uvicorn 학생 스트리밍, `TESTING=true` normal(`tests/check_student_live.py:29`) | 해당 없음 — §28은 demo live 검사를 요구하지 않음. demo 스트리밍은 #203/#204가 증명 |
| `browser-shard` / `npm run test:browser -- --shard N/3` (:39) | `tests/browser_*.mjs` 148개 중 demo 6개를 뺀 142개, `tests/browser_server.py`(`TESTING=true` `:14`, `demo_config` 없음 = normal) 및 각 모듈 자체 서버 | `tests/browser_demo*.mjs` 6개(아래 표). 실제 demo backend를 쓰는 것은 4개 |
| `browser` / `aggregate_browser_checks.mjs` (:61) | 3개 shard 완전성·결과 집계 | 같음 |

## pytest 안의 demo 검사

`tests/test_demo*.py` 28개 파일, 합계 1153개 수집. 수집 수는 파일명 뒤 괄호. 일부 파일은 두 방식을 함께 쓰므로 주된 방식으로 분류했다.

| 방식 | 파일 (수집 수, 근거 행) |
| --- | --- |
| `APP_MODE=demo` 하위 프로세스 (14개) | `test_demo_config.py` (23, env :12, 실행 :42), `test_demo_d1_02_lifecycle.py` (140, :761), `test_demo_d1_04_app.py` (23, env :34, 실행 :51), `test_demo_d1_04_request_db.py` (19, env :59, 실행 :71), `test_demo_d1_04_cli.py` (57, `test_demo_config.run_python` 경유, mode 인자 :56, normal 대조 :92), `test_demo_d2_03_seed.py` (25, env :451, 실행 :471), `test_demo_d2_04_calls.py` (148, `run_python` 경유, env :467, 실행 :470, normal 대조 :519), `test_demo_d2_04_g3.py` (4, `demo_env` import :18, 실행 :70), `test_demo_d3_02_guards.py` (15, env :61, 실행 :74), `test_demo_d3_02_real.py` (3, `demo_env` import :18, 실행 :44), `test_demo_d5_tour_facts.py` (2, `demo_env` :24·:33, 실행 :31), `test_demo_isolation.py` (7, env :232, 실행 :246), `test_demo_reset.py` (25, env :455, 실행 :468), `test_demo_security.py` (17, env :64, 실행 :84·:98) |
| demo 구성 요소 직접 호출 (프로세스는 normal, 14개) | `test_demo_d1_01_database.py` (32), `test_demo_d1_01_lifecycle.py` (72), `test_demo_d1_01_visitors.py` (20), `test_demo_d1_03_session.py` (135), `test_demo_d2_01_mode.py` (60), `test_demo_d2_01_projection.py` (30), `test_demo_d2_01_schema.py` (25), `test_demo_d2_03_fixtures.py` (14), `test_demo_d2_04_analysis.py` (21, `SimpleNamespace(APP_MODE="demo")` :155·:711), `test_demo_d2_d2_provenance.py` (4), `test_demo_d3_01_templates.py` (42), `test_demo_d3_02_exports.py` (39, :76·:571), `test_demo_d3_03_i18n.py` (47), `test_demo_provider.py` (104) |

normal 부정 검사(같은 job): `tests/test_demo_d1_04_cli.py:92`와 `tests/test_demo_d2_04_calls.py:519`의 normal 대조, `tests/test_demo_d2_01_mode.py:342`의 normal 프로세스 확인, `tests/test_demo_d3_02_guards.py:187`, `tests/test_demo_security.py:198-206`(normal은 `.env`를 읽음)과 `:219`(normal은 클라이언트 mode 전환을 무시), 그리고 demo 파일 밖의 기존 suite 전체가 normal 동작을 고정한다.

## 브라우저 demo 모듈

`tests/browser_*.mjs`는 148개이고 그중 demo 모듈은 6개다.

| 모듈 | demo backend |
| --- | --- |
| `browser_demo_d3_01_ui.mjs` | 없음. `/demo/*`를 stub으로 대체(`:6`) |
| `browser_demo_d3_03_recovery.mjs` | 없음. 주입한 `#demo-config`, 실제 demo backend 없음(`:3`) |
| `browser_demo_isolation.mjs` | `tests/demo_d4_02_server.py`를 `uv run --frozen python`으로 하위 실행(`:22`). helper가 자기 프로세스 env에 `APP_MODE=demo`(`tests/demo_d4_02_server.py:57`)와 normal 대조 서버 `APP_MODE=normal`(`:51`)을 넣고 `TESTING=false`(`:46`)로 실제 app을 띄움 |
| `browser_demo_journey.mjs` | `browser_demo_isolation.mjs`의 `server`를 import(`:6`)해 같은 helper 사용 |
| `browser_demo_recovery.mjs` | 같음(`:6`) |
| `browser_demo_d3_04_smoke.mjs` | 자체 uvicorn `src.main:app` 서버(`:63`)를 demo(`APP_MODE: 'demo'` `:94`)와 normal(`APP_MODE: 'normal'` `:110`)로 각각 띄움 |

- 러너는 `tests/` 안의 `^browser_.*\.mjs$`를 자동 탐색한다(`tests/ci_browser_sharding.mjs:11`). 위 모듈은 runner·workflow 수정 없이 3개 shard 중 하나에 들어간다. `package.json`의 `test:browser`는 `node tests/run_browser_checks.mjs`(`:4`)다.
- 추정 시간이 없는 모듈은 `defaultEstimateMs`로 분할된다(`tests/ci_browser_sharding.mjs:56`, `tests/browser_duration_estimates.json:576`). 이는 shard 균형에만 영향을 주며 실행 여부에는 영향이 없다.
- `browser-shard` job은 `npm ci`(:35), `npx playwright install --with-deps chromium`(:36), `uv sync --frozen --extra dev --python 3.12`(:38)를 이미 실행하므로, `tests/demo_d4_02_server.py`나 `uv run ... uvicorn`을 하위 프로세스로 띄우는 모듈도 추가 설정 없이 실행된다. 모든 helper가 자기 하위 프로세스 env에 `APP_MODE`를 넣으므로 job env 변경은 필요 없다.

## §28 normal/demo 증명 위치 (ba64b82)

| mode | CI job / step (`tests.yml`) | 증명하는 테스트 파일 | CI run 결과 |
| --- | --- | --- | --- |
| normal | `test` / pytest (:21), `check_student_live.py` (:22); `browser-shard` / `npm run test:browser` (:39) | `tests/test_demo*.py` 밖의 기존 suite 전체(수집 3166 − 1153 = 2013개), normal 대조 검사(위 목록), `tests/check_student_live.py`, `tests/browser_*.mjs` 중 demo 외 142개, `browser_demo_d3_04_smoke.mjs`의 normal 서버(:110), `browser_demo_isolation.mjs`의 normal 대조 서버(helper :51) | 기준선 run 39392009613 passed(pytest 3166 passed, browser 148 모듈 PASS, live PASS). 릴리스 후보 run 39397378254 passed — [인수 기록](../d4-03-release-acceptance.md) §3.1 |
| demo | `test` / pytest (:21); `browser-shard` / `npm run test:browser` (:39) | `APP_MODE=demo` 하위 프로세스 14개 파일과 직접 호출 14개 파일(`tests/test_demo*.py` 28개), `browser_demo_isolation.mjs`, `browser_demo_journey.mjs`, `browser_demo_recovery.mjs`, `browser_demo_d3_04_smoke.mjs`(실제 backend), `browser_demo_d3_01_ui.mjs`, `browser_demo_d3_03_recovery.mjs`(stub) | 기준선 run 39392009613 passed(같은 pytest·browser step). 릴리스 후보 run 39397378254 passed — [인수 기록](../d4-03-release-acceptance.md) §3.1 |

## 변경이 필요해지는 조건 (ba64b82에서 해당 없음)

다음 중 하나가 생기면 #205가 tests.yml을 최소 변경한다. job 이름 `test`/`browser-shard`/`browser`와 `tests/ci_controls.test.mjs`가 고정한 step 문구는 유지하고 `uv run --frozen python -m pytest -q tests/test_ci_controls.py`를 통과시킨다.

- #204 helper가 CI job에 없는 시스템 의존성을 요구한다. → 해당 없음. helper는 `uv sync`(:38)가 만든 Python 환경만 쓴다.
- demo 검사가 하위 프로세스 env가 아니라 job 수준 env를 요구한다. → 해당 없음. `tests.yml`에 `APP_MODE`가 없고 모든 demo 검사가 자기 하위 프로세스 env에 넣는다.
- 필수 검사 명령이 바뀌어 normal 또는 demo 한쪽이 어떤 job에서도 실행되지 않는다. → 해당 없음. 위 표의 step(:19-:22, :39, :61)이 모두 그대로 있다.

결론: ba64b82에서 세 조건 모두 발생하지 않았으므로 tests.yml은 변경하지 않는다.

`tests/ci_controls.test.mjs` 자체를 고쳐야 하면 §26 소유자가 없으므로 #190 변경관리로 먼저 소유를 정한다.
