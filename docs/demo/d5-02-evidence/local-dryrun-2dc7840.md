# D5-02 1단계 로컬 dry run (#207)

실행 SHA: `2dc7840963e6ca700915c2c63cff7729ca54afa9` (worktree clean, 제품 코드 변경 없음).
일시: 2026-10-20. 환경: WSL2 Linux, Python 3.12, `uv sync --frozen --extra dev`로 만든 전용 venv,
`TMPDIR`는 일회용 작업 디렉터리. 네트워크는 localhost만 썼다. 배포·외부 host 접속은 하지 않았다.
일회용 root 경로, secret 값(실행마다 `secrets.token_urlsafe(36)`으로 새로 만듦)은 기록하지 않는다.
아래 `<root>`, `<s1>`..`<s3>`는 그 자리다. `<venv>`는 전용 venv, `<tmp>`는 일회용 TMPDIR 디렉터리다.
uv cache는 override하지 않았다(`UV_CACHE_DIR` 미설정, uv 기본 위치).

## 1. 기존 harness (설정 거절·root/lock·health 200/503·limits)

```sh
UV_PROJECT_ENVIRONMENT=<venv> TMPDIR=<tmp> \
  uv run --frozen python -m pytest -q -p no:cacheprovider tests/test_demo_config.py tests/test_demo_d1_04_app.py
```

- 결과: 수집 36, 35 passed, 1 failed, 848.32초, worker 1(직렬).
- 실패: `test_demo_config.py::test_demo_rejects_anthropic_identity_and_profile_inputs[ANTHROPIC_IDENTITY_TOKEN]`.
  하위 프로세스 `python -c 'import src.config'`가 60초 timeout(`tests/test_demo_config.py:41`)에 걸렸다.
  같은 시각 다른 작업 세션들 때문에 여유 메모리가 약 380 MiB였다. 판정 실패가 아니라 시간 초과다.
- 재실행(같은 SHA, 단독): 위 테스트 함수의 4개 parametrize 전부 passed, 12.87초. 명령:

  ```sh
  UV_PROJECT_ENVIRONMENT=<venv> TMPDIR=<tmp> \
    uv run --frozen python -m pytest -q -p no:cacheprovider \
    "tests/test_demo_config.py::test_demo_rejects_anthropic_identity_and_profile_inputs"
  ```
- 판정: **passed**(환경 원인 timeout 1건, 재실행 통과). 사례별 의미는 `tests/test_demo_d1_04_app.py:62-136`.

## 2. 수동 부팅 (seed 미연결 확인)

`tests/test_demo_d1_04_app.py:15-45`와 같은 구성의 격리 환경(`env -i`; PATH/HOME과 runner 변수 `UV_PROJECT_ENVIRONMENT`, `TMPDIR`만 유지)을 썼다.

```sh
env -i PATH=<PATH> HOME=<HOME> UV_PROJECT_ENVIRONMENT=<venv> TMPDIR=<tmp> APP_MODE=demo TESTING=false ENV=production \
  DEMO_DATA_ROOT=<root> DEMO_ORIGIN=https://demo.test NORMAL_ORIGIN=https://normal.test \
  DEMO_SESSION_SECRET=<s1> DEMO_CSRF_SECRET=<s2> SESSION_SECRET=<s3> \
  timeout 90 uv run --frozen uvicorn src.main:app --host 127.0.0.1 --port 18207 --workers 1 --no-access-log
```

`demo.test`/`normal.test`는 harness의 테스트용 이름이며 승인 대상 host가 아니다. `<root>`는 빈 0700 디렉터리다.
거절 사례는 같은 명령의 `env -i` 인자에 `OPENAI_API_KEY=<비어 있지 않은 값>` 또는 `DEMO_WORKERS=2`를 더하고
`timeout 60 uv run --frozen uvicorn src.main:app --host 127.0.0.1 --port 18207`(`--workers`·`--no-access-log` 없음)로 실행했다.

| 사례 | 추가 환경 | 결과 | 판정 |
| --- | --- | --- | --- |
| 정상 설정 부팅 | 없음 | exit 3, 31초. `Application startup failed`, `src.demo.database.DemoError: DEMO_CONFIGURATION_INVALID` | 예상된 결과 |
| 금지 키 | `OPENAI_API_KEY=` 비어 있지 않은 값 | exit 1, 13초. import 시 `DEMO_CONFIGURATION_INVALID`(`src/config.py:162-167`) | passed |
| worker 2 | `DEMO_WORKERS=2` | exit 1, 8초. import 시 `DEMO_CONFIGURATION_INVALID` | passed |

- 정상 설정 부팅은 root open과 lock까지 진행했다. 실행 뒤 `<root>`에는 `.demo-root.json`, `.demo.lock`만 있다
  (`src/demo/database.py:159-195`). 그 다음 `demo_seed()`가 거절했고(`src/main.py:200-206`, 호출 248행),
  lifespan이 경로 없는 code로 다시 던졌다(`src/main.py:263-273`).
- **예상된 결과, #194 4단계(#197 seed 연결) 뒤 재실행.** 그때 같은 절차로 `/health` 200과 두 방문객 start를
  기록한다. 지금 main에서는 실제 부팅·방문이 불가능하므로 그 항목은 `not run`이다.
- 세 로그 모두에서 secret 세 개와 `<root>` 경로 문자열을 검색했고 일치 0건이다.

## 3. 실행하지 않은 것

- 성공 부팅, `/health` 200, 두 방문객 start: seed 미연결(위 2절). #194 4단계 뒤.
- 브라우저, `tests/check_student_live.py`, 전체 pytest: 문서 변경만이라 범위 밖. 전체 회귀는 PR head의 CI.
- 배포 환경의 모든 검사: 2단계(`checklist.md`), 승인 기록 완성 뒤.
