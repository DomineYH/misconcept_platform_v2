# CI 실행 낭비 제거와 성능 관측 운영 안내

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
   Actions 화면의 실행 SHA를 PR 최신 SHA와 대조한다.
2. 같은 PR에 새 커밋을 연속으로 올렸을 때 앞선 실행만 취소되고 최신
   실행이 남는지 확인한다. 취소된 실행을 성공한 검증 증거로 사용하지 않는다.
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
