# CI 실행 낭비 제거와 성능 관측 운영 안내

아래 1단계 절은 당시 설정과 증거를 보존한 기록이다. 현재 2단계 설정과
명령은 뒤의 「2단계: 3개 러너의 균형 분할」 절을 따른다.

## 1단계의 목적과 범위

이 단계는 기능 브랜치 push와 PR 이벤트에서 중복으로 생기던 회귀 실행을
줄이고, 오래 걸리는 검사를 다음 단계에서 근거를 가지고 개선할 수 있도록
시간을 기록한다. 검사 생략, 병렬 분할, 의존성 변경은 포함하지 않는다.
기존 검사 이름인 `test`, `browser`는 그대로 유지한다. 브랜치 보호 설정에서
어떤 검사를 필수로 지정했는지는 이 변경의 확인 범위가 아니다.

변경한 파일은 다음과 같다.

- `.github/workflows/tests.yml`: main push만 실행, 기존 PR 이벤트 유지,
  PR의 이전 실행만 취소, pytest 시간 출력, `test` job의 Node 24 명시
- `tests/run_browser_checks.mjs`: 브라우저 모듈별 START/END 및 경과 시간 출력
- `tests/ci_controls.test.mjs`, `tests/test_ci_controls.py`: 워크플로 계약과
  브라우저 러너의 정상·실패 동작을 실행 검증
- 이 문서: 적용, 확인, 되돌리기 절차

저장소 설정, 브랜치 보호 규칙, requirements, 잠금 파일, 검사 대상 모듈,
검사 순서와 기존 판정 조건은 변경하지 않는다.

## 측정 기준과 해석

변경 전 main 실행
[39171140769](https://github.com/DomineYH/misconcept_platform_v2/actions/runs/39171140769)를
기준 기록으로 사용한다.

- 브라우저 검사 단계: 33분 01초
- `browser` job 전체: 33분 32초
- pytest 단계: 12분 26초

이는 해당 실행의 관측값이다. 변경 후 시간이나 예상 절감률을 실측으로
표현하지 않는다. 1단계는 중복 실행과 오래된 PR 실행을 줄이는 변경이며,
남아 있는 한 번의 검사 자체를 더 빠르게 만든다는 보장은 없다.

설치 캐시를 먼저 손대지 않는 이유는 기준 실행에서 긴 검사 단계가
확인되었고, 어떤 테스트가 시간을 쓰는지 모듈·테스트별 자료가 아직
없기 때문이다. 기존 Node 설정의 `cache: npm`, `npm ci`, Playwright 설치,
`uv sync --frozen`을 유지한다. 캐시 적중률이나 설치 병목을 측정하지 않은
상태에서 캐시 변경에 따른 절감치를 주장하지 않는다.

## 워크플로 변경 전후

변경 전 `.github/workflows/tests.yml` 전체:

```yaml
name: Regression tests
on: [push, pull_request]
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --extra dev --python 3.12
      - run: uv run --frozen ruff check .
      - run: uv run --frozen black --check .
      - run: uv run --frozen python -m pytest -q
      - run: uv run --frozen python tests/check_student_live.py
  browser:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 24
          cache: npm
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --extra dev --python 3.12
      - run: npm run test:browser
```

변경 후 전체:

```yaml
name: Regression tests
on:
  push:
    branches: [main]
  pull_request:
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.event_name == 'pull_request' && github.event.pull_request.number || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 24
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --extra dev --python 3.12
      - run: uv run --frozen ruff check .
      - run: uv run --frozen black --check .
      - run: uv run --frozen python -m pytest -q --durations=30
      - run: uv run --frozen python tests/check_student_live.py
  browser:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 24
          cache: npm
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --extra dev --python 3.12
      - run: npm run test:browser
```

`pull_request:`에 branch/path/type 필터를 추가하지 않았으므로 기존의 기본
PR 이벤트(opened, synchronize, reopened)를 유지한다. 모든 PR 이벤트 유형을
새로 구독하는 변경은 아니다. PR이 없는 기능 브랜치 push와 태그 push에서는
이 워크플로를 실행하지 않으며, main push에서는 계속 실행한다.

그룹에는 워크플로 이름과 이벤트 이름을 넣어 PR과 push를 구분한다.
같은 PR 번호의 새 실행은 같은 그룹을 사용하고 이전 실행을 취소한다.
서로 다른 PR은 그룹을 공유하지 않는다. main push는 `github.run_id`를
사용하므로 실행마다 별도 그룹이며 `cancel-in-progress`도 false다.
따라서 이 설정으로 이전 main 실행을 취소하지 않는다. 운영자가 직접
취소하는 동작이나 GitHub의 실행 한도까지 막는다는 뜻은 아니다.

## 브라우저 시간 로그 읽기

각 모듈은 컨텍스트 생성 전에 START를 출력한다. END는 모듈 검사와
컨텍스트 정리 후 출력하며 Node의 단조 시계 `performance.now()`로 측정한
밀리초를 소수점 한 자리로 표시한다. 시스템 시각 변경의 영향을 받지 않는다.

```text
START browser_student_stream.mjs
PASS browser_student_stream.mjs
END browser_student_stream.mjs status=PASS elapsed_ms=1234.5
```

위 숫자는 형식을 설명하는 예시이며 실측값이 아니다. 경과 시간은
컨텍스트/페이지 생성, `/chat` 이동, 모듈 import, 검사 및 컨텍스트 정리를
포함한다. 서버 기동과 Chromium 전체 기동·종료는 포함하지 않으므로 END의
합계는 브라우저 단계 전체 시간과 다를 수 있다.

검사·import·페이지 오류에는 기존 `FAIL` 로그와 비정상 종료 코드를 유지하며
END도 `status=FAIL`로 남기고 다음 모듈을 실행한다. 컨텍스트 정리 자체가
실패하면 END를 FAIL로 남긴 뒤 기존처럼 바깥 오류 처리로 전달하여 나머지
모듈 실행을 중단한다. 기존 PASS 로그는 정리 전에 출력되므로, 이 경우
같은 모듈에 PASS 다음 END FAIL이 나타날 수 있다. 최종 END와 프로세스
종료 상태를 함께 확인한다.

서버나 브라우저가 시작되기 전에 실패하면 시작된 모듈이 없으므로 START/END는
없고 기존 전체 실행 오류가 출력된다. 프로세스 강제 종료나 PR 실행 취소는
`finally` 실행을 보장하지 않으므로 START만 남을 수 있다.

## 로컬 확인

README의 Python/Node 버전과 잠금 파일을 사용한다. 다음은 준비와 범위가
작은 재현 확인 명령이다.

```sh
uv sync --frozen --extra dev --python 3.12
npm ci
npx playwright install --with-deps chromium
uv run --frozen ruff check .
uv run --frozen black --check .
node --check tests/run_browser_checks.mjs
node --check tests/ci_controls.test.mjs
node --test tests/ci_controls.test.mjs
uv run --frozen python -m pytest -q tests/test_ci_controls.py --durations=30
npm run test:browser -- student_stream
git diff --check
```

이 계약 검사는 브라우저 모듈의 이름 정렬과 OR 필터, 실패 후 계속 실행,
페이지 오류 판정, 컨텍스트 생성·페이지 생성·이동·정리 실패와 종료 코드를
확인한다. 시간 측정은 단조 시계의 경계를 모의하여 결정적으로 검증하며,
실제 Chromium 동작을 대체하지 않는다. Python 래퍼가 있으므로 기존 CI의
전체 pytest 수집에도 포함된다. 이 검사는 Node 24의 `registerHooks`를 사용하므로
`test` job에도 `actions/setup-node@v4`로 Node 24를 명시한다. 이 job에서는
실제 Playwright 패키지를 쓰지 않으므로 npm 설치 단계는 추가하지 않는다.
워크플로 검사는 현재 YAML의 좁은 구조와
여기 사용한 표현식을 확인하며 GitHub의 실제 이벤트 처리를 대체하지 않는다.

전체 회귀는 실제 CI에서 아래 명령을 모두 실행한 결과로 판정한다.
로컬의 일부 검사 성공을 전체 완료라고 표현하지 않는다.

```sh
uv run --frozen ruff check .
uv run --frozen black --check .
uv run --frozen python -m pytest -q --durations=30
uv run --frozen python tests/check_student_live.py
npm run test:browser
```

## GitHub에서 수동 확인할 순서

1. 변경 PR의 최신 커밋에서 `test`, `browser`가 모두 생기는지 확인한다.
   PR CI는 보통 `refs/pull/<번호>/merge`의 합성 병합 SHA에서 실행되므로
   실행 SHA가 PR head SHA와 같다고 가정하지 않는다. 실행에 결합된 head와
   base SHA 및 합성 merge SHA를 함께 확인한다.
2. 같은 PR에 새 커밋을 연속으로 올렸을 때 앞선 실행만 취소되고 최신
   실행이 남는지 확인한다. 단, 과거 실행의 수동 재실행도 같은 PR 그룹을
   사용하므로 더 새로운 실행을 취소할 수 있다. 재실행 전에 현재 실행을
   확인한다. 취소된 실행을 성공한 검증 증거로 사용하지 않는다.
3. 서로 다른 PR 실행은 서로 취소하지 않는지 확인한다. 기능 브랜치 push에
   별도 push 실행이 생기지 않고 PR 실행만 생기는지도 확인한다.
4. 승인된 병합 후 main push 실행이 생기는지 확인한다. main에 후속 push가
   생겨도 앞선 main 실행이 이 concurrency 정책으로 취소되지 않는지 확인한다.
   확인만을 목적으로 불필요한 커밋이나 운영 변경을 만들지는 않는다.
5. 최신 PR과 병합 커밋의 `test`, `browser` 전체 성공을 확인한다. pytest의
   느린 항목 최대 30개와 브라우저 START/END 로그가 보이는지도 확인한다.
6. 기준 실행과 변경 후 실행의 URL, SHA, 실행 환경, job/단계 시간, 느린
   pytest 항목과 브라우저 모듈을 기록한다. 다른 커밋이나 실행 취소,
   대기 시간의 차이를 단순 속도 향상으로 해석하지 않는다.

이 단계의 결과 기록에는 실제 실행 URL과 관측값을 추가한다. 아직 실행하지
않은 검사, 검토 중인 결과 및 후속 단계의 예상치는 미확인으로 구분한다.

## 안전하게 되돌리기

1. 되돌릴 변경의 커밋과 후속 변경을 확인하고 별도 PR을 준비한다.
   공유 브랜치를 강제 push하거나 기록을 재작성하지 않는다.
2. 1단계만 되돌리려면 위의 변경 전 워크플로를 복구하고 러너의
   `performance` import, START/END 및 상태 변수만 제거한다. 컨텍스트
   정리는 원래의 `await context?.close();`로 복구한다. 시간 제한이나
   실패 처리를 새로 바꾸지 않는다.
3. 이 계약을 되돌릴 때 함께 도입한 계약 검사도 같은 범위에서 되돌린다.
   후속 단계가 파일을 수정했다면 파일 전체를 과거 버전으로 덮어쓰지 말고
   해당 변경만 역적용한다. 문서도 실제 남아 있는 설정과 맞춘다.
4. lint/format, 범위 검사와 PR의 전체 `test`, `browser` 성공을 확인한 뒤
   승인된 병합 절차를 따른다.
5. 트리거를 되돌리면 기능 브랜치 push 중복 실행이 다시 생기고, 이전 PR
   실행의 자동 취소가 없어지는 점을 운영자가 확인한다.

설치 캐시나 저장소 설정을 변경하지 않았으므로 해당 설정의 별도 복구는
필요 없다. 후속 2단계의 변경은 별도 근거와 검증 결과로 이 문서를 확장한다.


## 2단계: 3개 러너의 균형 분할

### 범위와 변경 파일

1단계 기준 커밋은 `f2b638b22d1bbe7b072244c7747135f55f8411c3`이다.
위의 1단계 「변경 후 전체」가 2단계의 변경 전 워크플로 전체와 정확히 같다.
2단계는 브라우저 모듈만 3개의 별도 GitHub-hosted runner에 분배한다.
기존 `test` 검사와 최종 `browser` 검사 이름은 유지한다. `browser-shard`
행렬의 3개 job이 추가되며 `browser`는 전체 결과를 판정하는 집계 job이 된다.
저장소 설정, 브랜치 보호, 잠금 파일, 패키지 버전은 변경하지 않는다.

- `.github/workflows/tests.yml`: `browser-shard` 3개 행렬, 독립 artifact 업로드,
  항상 실행하는 `browser` 집계, artifact 다운로드 결과와 행렬 결과 전달
- `tests/run_browser_checks.mjs`: 엄격한 인자, 실행 전 분할, 브라우저 없는
  `--list`, 계획/실행/시간 JSON 증거. 기존 START/PASS/FAIL/END와 정리 동작 유지
- `tests/ci_browser_sharding.mjs`: 의존성 없는 발견·균형 분배·완전 합집합 판정
- `tests/aggregate_browser_checks.mjs`: 실제 checkout의 디렉터리에서 재발견하고
  artifact 디렉터리와 JSON을 검증하는 집계 진입점
- `tests/browser_duration_estimates.json`: 출처와 보수적인 기본값을 포함한 실측 기반 가중치
- `tests/ci_controls.test.mjs`, `tests/ci_browser_sharding.test.mjs`,
  `tests/test_ci_controls.py`: 원래 러너 계약을 유지하고 분할/실패 집계 계약 추가
- 이 문서: 단계별 설정과 운영·복구 절차

`tests/browser_*.mjs` 142개의 기존 검사 모듈과 모든 단언은 그대로다.
modal의 매개변수나 검사 범위는 바꾸지 않는다. 보조 모듈과 계약 검사는
`ci_` 이름으로 시작하여 기존 발견 규칙에 검사 모듈로 섞이지 않게 한다.
문서 전용 변경의 빠른 경로(docs-fastlane)는 이번 범위에서 보류했다.
경로 필터나 테스트 생략은 추가하지 않았다.

### 2단계 변경 후 워크플로 전체

```yaml
name: Regression tests
on:
  push:
    branches: [main]
  pull_request:
concurrency:
  group: ${{ github.workflow }}-${{ github.event_name }}-${{ github.event_name == 'pull_request' && github.event.pull_request.number || github.run_id }}
  cancel-in-progress: ${{ github.event_name == 'pull_request' }}
jobs:
  test:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 24
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --extra dev --python 3.12
      - run: uv run --frozen ruff check .
      - run: uv run --frozen black --check .
      - run: uv run --frozen python -m pytest -q --durations=30
      - run: uv run --frozen python tests/check_student_live.py
  browser-shard:
    runs-on: ubuntu-latest
    strategy:
      fail-fast: false
      matrix:
        shard: [1, 2, 3]
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 24
          cache: npm
      - run: npm ci
      - run: npx playwright install --with-deps chromium
      - uses: astral-sh/setup-uv@v6
      - run: uv sync --frozen --extra dev --python 3.12
      - run: npm run test:browser -- --shard ${{ matrix.shard }}/3 --report "$RUNNER_TEMP/browser-results/shard-${{ matrix.shard }}-of-3.json"
      - uses: actions/upload-artifact@v4
        if: ${{ always() }}
        with:
          name: browser-shard-${{ matrix.shard }}-attempt-${{ github.run_attempt }}
          path: ${{ runner.temp }}/browser-results/shard-${{ matrix.shard }}-of-3.json
          if-no-files-found: error
  browser:
    runs-on: ubuntu-latest
    needs: browser-shard
    if: ${{ always() }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-node@v4
        with:
          node-version: 24
      - uses: actions/download-artifact@v4
        id: download
        with:
          pattern: browser-shard-*-attempt-${{ github.run_attempt }}
          path: browser-results
          merge-multiple: false
      - run: node tests/aggregate_browser_checks.mjs --artifacts browser-results --shard-count 3
        if: ${{ always() }}
        env:
          BROWSER_SHARD_RESULT: ${{ needs.browser-shard.result }}
          BROWSER_ARTIFACT_DOWNLOAD_RESULT: ${{ steps.download.outcome }}
```

`fail-fast: false`로 한 shard의 실패가 다른 shard를 조기에 취소하지 않는다.
각 행렬 job은 별도 VM이며 각자 서버, 임시 경로, 브라우저 컨텍스트와 artifact를
갖는다. 서버는 기존처럼 사용 가능한 localhost 임의 포트를 사용한다.
브라우저가 Playwright 라이브러리를 직접 import하는 러너이므로 Playwright Test의
`--shard` 옵션을 전달한 것이 아니다. 여기서 설명한 모듈 단위 분할을 직접 수행한다.

업로드는 검사 실패 때도 시도하며 파일이 없으면 실패한다. artifact 이름에는
shard와 `github.run_attempt`를 넣어 재실행의 오래된 증거를 섞지 않는다.
다운로드의 `merge-multiple: false`는 artifact별 폴더를 보존하여 중복/예상 밖
artifact가 같은 파일명으로 덮여 사라지는 것을 막는다.

집계는 `always()`와 `needs: browser-shard`를 사용한다. 행렬 job 결과가
`success`가 아니거나 다운로드가 성공하지 않으면 JSON이 정상처럼 보여도 실패한다.
어느 shard가 failed/cancelled/skipped이거나 업로드가 실패하면 전체 성공이 될 수 없다.
전체 실행 자체가 취소되면 집계도 취소될 수 있으며, 그것도 성공 증거가 아니다.

집계는 현재 commit의 실제 `tests/` 목록을 다시 읽는다. self-reported
`plannedModules`만 믿지 않는다. 누락/중복/예상 밖 artifact 폴더·파일·shard·모듈,
빈 partition, 잘못된 shard 수, 다른 commit/run/attempt, 다른 inventory/plan ID,
FAIL 상태, 누락되거나 유한하지 않은 시간 값을 모두 거절한다.
JSON에는 `plannedModules`, `executedModules`의 모듈명·최종 상태·`elapsedMs`,
`shard`, `shardCount`, `inventoryId`, `planId`, commit/run/attempt가 들어간다.
`inventoryId`는 정렬된 실제 모듈명 목록의 SHA-256이며 commit 식별자와 함께 판정한다.
`planId`는 inventory와 분배 결과/추정 합계를 묶어 다른 계획이 섞이는 것을 막는다.
컨텍스트 정리 실패로 뒤 모듈이 중단되면 실행 목록이 짧고 전체 상태도 FAIL이다.
강제 종료로 JSON이 없거나 불완전하면 집계는 성공하지 않는다.

### 실측 기반 가중치와 성능 해석

현재 가중치는 성공한 1단계 실제 실행
[39186456508](https://github.com/DomineYH/misconcept_platform_v2/actions/runs/39186456508)의
`browser` job에서 142개 모듈의 `END status=PASS elapsed_ms`를 읽어 얻었다.
PASS 로그 사이 간격의 추정치를 실제 계측값으로 교체했으며 매니페스트의
`sourceRun`, `sourceCommit`, `sourceHead`, `sourceBase`, `sourcePaths`, `kind`에
출처를 남겼다. 실행의 PR binding과 두 job의 checkout 로그로 확인한 결합 상태와
실제 결과는 다음과 같다.

- PR head: `ad597b604a5f8374a2c91ec086d2e4d19b6da438`
- PR base: `0115b8c09ce55b835c6f70f32f38077e045ad12f`
- 실제 checkout 합성 merge commit: `add76e54777f1b82cd408460a2a1006b7e5e5f9c`
- 브라우저 검사 단계: 33분 32초, `browser` job 전체: 34분 08초
- pytest: 2,125 passed, pytest 요약 610.98초, 해당 CI 단계: 10분 21초
- 전체 실행: SUCCESS, 브라우저 142개 모두 END PASS

과거 기준 실행 39171140769와 source commit
`427890033d206a4f043ffc946009bd8ce6a437c2`는 역사적 비교 기록으로 보존한다.
그 실행의 브라우저 단계 33분 01초, job 33분 32초, pytest 단계 12분 26초는
바꾸지 않는다. 과거 PASS timestamp 간격의 추정치 출처는 매니페스트의
`historicalBaseline`에도 남겼다. 두 실행 사이에는 D1 변경도 포함되어 있으므로
위 시간 차이를 1단계 최적화의 효과로 귀속하지 않는다. 실행 환경 변동도
구분해야 하며, 2단계 병렬 분할의 효과는 실제 2단계 성공 CI 전까지 미측정이다.

모듈을 가중치 내림차순으로 정렬하고 동률은 파일명 사전순으로 정한다.
누적 가중치 합계가 가장 작은 shard에 순서대로 배정하고 shard 동률은 작은
번호를 선택한다. 각 shard 안에서는 원래와 같은 파일명 정렬 순서로 실행한다.
기존 142개에 현재 실측 가중치를 적용한 다음 실행의 계획은 아래와 같다.
가중치의 출처는 실측이지만 미래 shard의 부하와 소요 시간은 추정이며,
아래 합계는 읽기 쉽게 소수점 한 자리로 반올림했다.

- shard 1: 1개, 810,717.3 ms 추정
- shard 2: 71개, 596,404.5 ms 추정
- shard 3: 70개, 596,296.3 ms 추정
- 합계: 2,003,418.1 ms 추정

가장 큰 `browser_173_modal_close.mjs`의 기준 실측은 810,717.3 ms(약 13분 31초)다.
다음 큰 기준 실측은 hover 231,939.0 ms, button 156,623.1 ms, form 115,977.7 ms다.
모듈을 쪼개지 않으므로 한 실행에서 가장 오래 걸리는 모듈은 분할만으로 없앨 수
없는 시간 하한이다. 이 실측을 기준으로 예상할 때 modal이 하한을 지배하며,
3개를 더 많은 shard로 늘려도 해당 모듈 자체는 나뉘지 않는다. 위 계획으로
실측 단축률이나 최종 wall time을 주장하지 않는다. CI의 설치·기동·대기·집계
시간도 포함해야 한다.

새 `browser_*.mjs` 모듈은 실제 디렉터리 발견으로 자동 포함한다. 매니페스트에
없으면 가장 큰 성공 기준 실측인 810,717.3 ms를 보수적인 기본 추정값으로 사용한다.
삭제된 모듈의 stale 가중치는 배분 대상을 만들지 않으며, 가중치에 이름이 없다는
이유로 검사를 누락하지 않는다. 기본값과 사용하지 않는 stale 항목까지 모든
가중치는 양수이고 유한한 숫자여야 한다. 중복 이름도 거절한다.

별도 runner 3개가 Python/npm/Chromium 설치를 반복하므로 wall time을 줄이더라도
runner-minutes/청구 비용은 증가할 수 있다. 집계 runner 비용도 있다.
취소·대기 시간과 실제 실행 시간, 전체 사용 분을 각각 기록한 뒤 판단한다.

### 로컬 준비, 목록과 합집합 확인

README의 버전을 사용한다. `--list`에는 npm/Playwright/서버 설치가 필요 없다.
직접 node를 호출하면 npm 배너 없이 JSON만 얻는다.

```sh
node tests/run_browser_checks.mjs --list
node tests/run_browser_checks.mjs --list --shard 1/3 > /tmp/browser-plan-1.json
node tests/run_browser_checks.mjs --list --shard 2/3 > /tmp/browser-plan-2.json
node tests/run_browser_checks.mjs --list --shard 3/3 > /tmp/browser-plan-3.json
node --input-type=module <<'JS'
import assert from 'node:assert/strict';
import {readFile, readdir} from 'node:fs/promises';
const plans = await Promise.all([1, 2, 3].map(async shard =>
  JSON.parse(await readFile(`/tmp/browser-plan-${shard}.json`, 'utf8'))));
const actual = (await readdir('tests')).filter(name => /^browser_.*\.mjs$/.test(name)).sort();
const union = plans.flatMap(plan => plan.plannedModules);
assert.equal(new Set(union).size, union.length, 'duplicate modules');
assert.deepEqual(union.sort(), actual);
assert.equal(new Set(plans.map(plan => plan.inventoryId)).size, 1);
assert.equal(new Set(plans.map(plan => plan.planId)).size, 1);
console.log(`Complete planned union: ${actual.length} modules`);
JS
```

`--list`의 `status: NOT_RUN`, 빈 `executedModules`는 검사를 실행하지 않았다는
뜻이다. 목록 확인 성공을 실제 브라우저 성공이나 CI 성공으로 사용하지 않는다.
`--shard N/COUNT` 또는 `--shard=N/COUNT`는 1부터 시작하는 정수만 받는다.
0, 소수, 범위 초과, 중복 옵션, 모듈 수보다 많은 shard는 오류다.
필터와 shard를 함께 지정하는 것은 로컬/CI 모두 거절한다. shard 없이 쓰는
기존 substring OR 필터는 그대로다.

```sh
npm run test:browser -- student_stream mentor_stream
node tests/run_browser_checks.mjs --list student_stream
```

작은 범위의 계약·구문·Python 확인:

```sh
uv sync --frozen --extra dev --python 3.12
node --check tests/run_browser_checks.mjs
node --check tests/ci_browser_sharding.mjs
node --check tests/aggregate_browser_checks.mjs
node --check tests/ci_controls.test.mjs
node --check tests/ci_browser_sharding.test.mjs
node --test tests/ci_controls.test.mjs tests/ci_browser_sharding.test.mjs
uv run --frozen python -m pytest -q tests/test_ci_controls.py --durations=30
uv run --frozen ruff check .
uv run --frozen black --check .
git diff --check
```

Python 래퍼는 두 Node 계약 파일을 모두 실행한다. 실제 Chromium은 별도다.
로컬에서 전체 shard와 실제 실행 합집합까지 확인하려면 깨끗한 artifact 폴더를
사용하고 아래 명령을 실행한다. GitHub 밖에서는 ID가 `local`이다.
기존 artifact는 다른 경로로 보관하거나 새 빈 디렉터리를 사용한다.

```sh
npm ci
npx playwright install --with-deps chromium
# browser-results가 없는 상태에서 실행한다. 실패하면 즉시 멈춘다.
set -eu
for shard in 1 2 3; do
  folder="browser-results/browser-shard-${shard}-attempt-local"
  mkdir -p "$folder"
  node tests/run_browser_checks.mjs --shard "$shard/3" \
    --report "$folder/shard-${shard}-of-3.json"
done
BROWSER_SHARD_RESULT=success BROWSER_ARTIFACT_DOWNLOAD_RESULT=success \
  node tests/aggregate_browser_checks.mjs --artifacts browser-results --shard-count 3
```

이 예시는 로컬에서 순차 실행하므로 CI의 병렬 wall time을 재현하지 않는다.
로그인 정보·유료 provider 호출 없이 기존 fixture 기반 검사를 실행한다.
로컬 계약만으로 전체 회귀가 끝났다고 판단하지 않는다.

### 실제 CI 확인, shard 수와 시간 갱신

1. stage1이 병합된 정확한 main을 base로 사용한다. stage1이 아직 draft/CI 중이면
   stage2를 최신 base 검증 없이 최종 게시하거나 병합하지 않는다.
2. PR의 head SHA와 base SHA, 실행의 `refs/pull/<번호>/merge` 합성 SHA를 확인한다.
   `gh pr view <번호> --json headRefOid,baseRefOid,statusCheckRollup`과
   `gh run view <실행번호> --json event,headSha,headBranch,jobs,url`로 확인하고,
   `gh run view <실행번호> --job <job번호> --log`의 checkout 로그에서 실제
   checkout SHA를 확인한다. API 요약의 headSha를 무조건 합성 SHA로 간주하지 않는다.
   필요하면 `gh api repos/DomineYH/misconcept_platform_v2/git/commits/<합성SHA>`의
   parents를 읽어 결합된 head/base를 대조한다. PR head와 실행 SHA가 다르다는
   이유만으로 오래된 실행이라고 판단하지 않는다.
3. `test`, 3개의 `browser-shard`, 최종 `browser`가 모두 실행되는지 확인한다.
   pytest 전체, Ruff, Black, live streaming과 모든 브라우저 모듈의 성공이 필요하다.
   각 artifact 폴더에는 해당 shard의 JSON 하나만 있어야 한다.
4. `browser` 로그의 모듈 수, inventory ID, shard 수와 모든 JSON의 계획·실행·PASS를
   대조한다. 누락/실패한 artifact 업로드나 취소된 shard를 성공으로 취급하지 않는다.
   실패/누락/중복/예상 밖 모듈·artifact는 로컬 계약의 음성 사례로 검증한다.
5. stage1과 stage2의 실제 성공 실행 URL, SHA/head/base, runner/Node/Python,
   각 job 및 단계 시간, 설치/대기 시간, 전체 runner 사용 분을 기록한다.
   stage2 실행 전에는 개선 효과를 미측정으로 남긴다.
6. 과거 같은 PR 실행을 수동 재실행하면 새 실행과 같은 concurrency 그룹을
   사용하여 더 새로운 실행을 취소할 수 있다. 재실행 전에 최신 진행 중 실행을
   확인하고, 비교 대상 성공 증거를 보존한다.

shard 수를 변경할 때는 워크플로의 `matrix.shard`, 실행 명령의 분모,
보고 파일명의 `of-COUNT`, 업로드 경로의 `of-COUNT`, 집계 명령의
`--shard-count`와 workflow 계약의 기대값을 한 PR에서 함께 수정한다.
매니페스트는 shard 수와 무관하다. COUNT는 실제 모듈 수보다 클 수 없다.
변경 후 모든 `--list` 합집합, Node/Python 계약, 전체 실제 CI를 다시 확인한다.
필수 `browser` 검사 이름과 항상 실행하는 실패 집계는 유지한다.

시간 자료를 갱신할 때는 성공한 stage1 END 로그 또는 성공한 stage2 JSON의
동일 commit 전체 모듈 집합을 사용한다. 모듈별 `elapsedMs`는 컨텍스트 정리까지
포함하며 server/Chromium 전체 기동·종료는 제외한다. 출처 run/commit/path와
추정인지 계측인지 `kind`를 정확히 갱신한다. 취소·실패·누락 자료를 성공 시간으로
덮어쓰지 않는다. 가중치 변화는 배분과 `planId`를 바꾸므로 목록·합집합과
음성 계약을 재검증한다. 확인된 실측 최대치 등으로 기본값을 갱신한다면
그 선택 근거와 보수성을 함께 기록한다.

### 문제 해결과 2단계 되돌리기

- `No browser checks matched`: substring을 확인한다. shard와 필터는 함께 쓰지 않는다
- invalid shard/empty partition: 1-based 정수와 COUNT, 현재 모듈 수를 확인한다
- invalid estimate/duplicate: 0·음수·문자열·중복 이름을 고친다. 가중치 오류를
  무시하거나 해당 모듈을 빼서 통과시키지 않는다
- 모듈 수는 많지만 한 shard가 오래 걸림: 파일 수보다 추정 합계와 최대 모듈을
  확인한다. 모듈 분해/단언 변경은 별도 검토 범위이며 이번 변경에 포함되지 않는다
- artifact 없음/집계 실패: 세 job의 검사·업로드 결과와 download outcome,
  artifact 이름의 attempt 및 내부 파일명, commit/inventory/plan ID를 확인한다
- stage2 실패 복구는 Actions의 `Re-run all jobs`를 사용한다. `Re-run failed jobs`만
  실행하면 성공했던 shard는 이전 attempt에 남고, 현재 attempt의 artifact가
  누락되거나 runAttempt가 달라 의도적으로 집계가 실패한다. 모든 shard와 집계를
  같은 attempt에서 다시 실행한다
- 새 모듈 추가 후 목록 변화: 기본 추정치로 자동 포함되는 정상 동작이다.
  기존 inventory/plan의 artifact를 재사용하지 않는다
- 강제 종료/취소: JSON과 finally를 보장하지 못하므로 성공 증거로 쓰지 않는다

2단계만 되돌릴 때는 별도 PR에서 `browser-shard` 행렬과 집계 `browser`를
제거하고 위에 보존한 1단계 「변경 후 전체」의 단일 `browser` job을 복구한다.
러너는 1단계 커밋의 직접 발견/substring 필터/순차 실행과 START/END 계측을
복구한다. 새 순수 분할/집계 모듈·가중치·추가 계약을 함께 제거하고
`ci_controls.test.mjs`, Python 래퍼도 1단계 범위로 복구한다. stage1의 main-only
push, PR concurrency, Node 24, pytest durations와 원래 실패 처리는 유지한다.
후속 변경이 있다면 파일 전체를 옛 버전으로 덮지 말고 해당 차이만 역적용한다.
구문·계약·lint/format 및 최신 합성 merge SHA의 전체 `test`, `browser` CI를
통과한 후 승인된 병합 절차를 따른다. 2단계는 저장소 설정을 바꾸지 않았으므로
별도의 branch protection 복구는 없다.
