# normal/demo CI 적용 범위 평가 (#205 1단계)

평가 기준: main `2dc7840963e6ca700915c2c63cff7729ca54afa9`, `.github/workflows/tests.yml` 현재본. 읽기 평가이며 이 문서 작성 중 전체 회귀는 실행하지 않았다(수집 수치는 `pytest --collect-only` 결과). 2단계에서 고정 후보 SHA 기준으로 이 표를 다시 확인한다.

결론: **normal과 demo가 모두 기존 job에서 실행되므로 tests.yml은 변경하지 않는다.** 계약 §26의 #205 CI 쓰기 권한은 아래 "변경이 필요해지는 조건"이 생길 때만 쓴다.

## mode가 정해지는 방식

- 서버 mode는 프로세스 시작 시 `APP_MODE` 환경변수로 한 번 정해지고 기본값은 `normal`이다(`src/config.py:23`). 실행 중 전환은 없다.
- CI job은 `APP_MODE`를 설정하지 않는다. pytest의 `tests/conftest.py:8`은 import 전에 `TESTING=true`와 메모리 SQLite를 고정하므로 pytest 프로세스 자체는 normal이다.
- 따라서 demo 프로세스 검사는 각 테스트가 `APP_MODE=demo` 하위 프로세스를 직접 띄워 같은 pytest job 안에서 실행한다. demo 구성 요소 단위 검사는 `src/demo/*`와 명시적 `app_mode` 인자를 프로세스 mode와 무관하게 직접 호출한다.

## job/step별 증명 범위

| CI job / step (`tests.yml`) | normal | demo |
| --- | --- | --- |
| `test` / `ruff check .` (:19), `black --check .` (:20) | 전체 코드 | 전체 코드 (`src/demo/`, `tests/test_demo_*.py` 포함) |
| `test` / `pytest -q --durations=30` (:21) | 전체 suite 2752개 수집, normal 프로세스 | `tests/test_demo_*.py` 13개 파일 739개 수집(아래 표) |
| `test` / `tests/check_student_live.py` (:22) | 실제 uvicorn 학생 스트리밍, `TESTING=true` normal(`tests/check_student_live.py:29`) | 해당 없음 — §28은 demo live 검사를 요구하지 않음. demo 스트리밍은 #203/#204가 증명 |
| `browser-shard` / `npm run test:browser -- --shard N/3` (:39) | `tests/browser_*.mjs` 142개, `tests/browser_server.py`(`TESTING=true`, `demo_config` 없음 = normal) 및 각 모듈 자체 서버 | main에는 `browser_*demo*.mjs`가 아직 없음. #199/#201/#202/#204 신규 모듈은 자동 포함(아래) |
| `browser` / `aggregate_browser_checks.mjs` (:61) | 3개 shard 완전성·결과 집계 | 같음 |

## pytest 안의 demo 검사

| 방식 | 파일 (근거 행) |
| --- | --- |
| `APP_MODE=demo` 하위 프로세스 | `tests/test_demo_config.py` (env :12, 실행 :41), `tests/test_demo_d1_02_lifecycle.py` (:761), `tests/test_demo_d1_04_app.py` (env :34, 실행 :46), `tests/test_demo_d1_04_request_db.py` (env :59, 실행 :70), `tests/test_demo_d1_04_cli.py` (`test_demo_config.run_python` 경유, mode 인자 :56, normal 대조 :92) |
| demo 구성 요소 직접 호출 (프로세스는 normal) | `tests/test_demo_d1_01_database.py`, `tests/test_demo_d1_01_lifecycle.py`, `tests/test_demo_d1_01_visitors.py`, `tests/test_demo_d1_03_session.py`, `tests/test_demo_d2_01_mode.py`, `tests/test_demo_d2_01_schema.py`, `tests/test_demo_d3_02_exports.py`, `tests/test_demo_provider.py` |

normal 부정 검사(같은 job): `tests/test_demo_d1_04_cli.py`의 normal 대조, `tests/test_demo_d2_01_mode.py:342`의 normal 프로세스 확인, 그리고 demo 파일 밖의 기존 suite 전체가 normal 동작을 고정한다. §28 "normal 부정" 행의 실제 backend 증명은 #203/#204 증거로 연결한다.

## 신규 브라우저 demo 모듈

- 러너는 `tests/` 안의 `^browser_.*\.mjs$`를 자동 탐색한다(`tests/ci_browser_sharding.mjs:11`). 신규 모듈은 runner·workflow 수정 없이 3개 shard 중 하나에 들어간다.
- 추정 시간이 없는 모듈은 `defaultEstimateMs`로 분할된다(`tests/ci_browser_sharding.mjs:56`, `tests/browser_duration_estimates.json:576`). 이는 shard 균형에만 영향을 주며 실행 여부에는 영향이 없다.
- `browser-shard` job은 `npm ci`(:35), `npx playwright install --with-deps chromium`(:36), `uv sync --frozen --extra dev --python 3.12`(:38)를 이미 실행하므로, #204의 `tests/demo_d4_02_server.py`처럼 Python helper를 하위 프로세스로 띄우는 모듈도 추가 설정 없이 실행된다. helper가 자기 하위 프로세스 env에 `APP_MODE=demo`를 넣으면 job env 변경은 필요 없다.

## 변경이 필요해지는 조건 (현재 없음)

다음 중 하나가 생기면 #205가 tests.yml을 최소 변경한다. job 이름 `test`/`browser-shard`/`browser`와 `tests/ci_controls.test.mjs`가 고정한 step 문구는 유지하고 `uv run --frozen python -m pytest -q tests/test_ci_controls.py`를 통과시킨다.

- #204 helper가 CI job에 없는 시스템 의존성을 요구한다.
- demo 검사가 하위 프로세스 env가 아니라 job 수준 env를 요구한다.
- 필수 검사 명령이 바뀌어 normal 또는 demo 한쪽이 어떤 job에서도 실행되지 않는다.

`tests/ci_controls.test.mjs` 자체를 고쳐야 하면 §26 소유자가 없으므로 #190 변경관리로 먼저 소유를 정한다.
