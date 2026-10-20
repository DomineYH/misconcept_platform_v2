# D5-02 1단계 로컬 dry run 재실행 — G5 후보 8f97776 (#207)

실행 SHA: main `37ecfb76f2167f47f852beb3493ddda91734083b` (worktree clean, 제품 코드 변경 없음).
G5 후보: `8f97776dcb3456b5df0fb198daf520168752117c` (`docs/demo/d4-03-release-acceptance.md` §7).
`git diff --name-only 8f97776 37ecfb7`는 23개 경로이고 모두 `docs/` 아래다(그 밖 0개). 따라서 실행한 제품 tree
(`src`, `static`, `migrations`, `tests`, 설정 파일)는 G5 후보와 같다.
일시: 2026-10-20 (UTC 10:20–10:30). 환경: WSL2 Linux, Python 3.12.3, uv 0.11.28, `uv sync --frozen --extra dev`로 만든 전용 venv.
drvfs가 느려 `git archive HEAD`를 ext4 디렉터리(`<tree>`)에 풀어 그곳에서 실행했다. 검사·서버 실행의 네트워크는 localhost만 썼고
배포·외부 host 접속은 하지 않았다. secret 세 개는 이 재실행용으로 `secrets.token_urlsafe(36)`으로 새로 만들어 모든 부팅에 썼고 기록하지 않는다.
`<root>`는 빈 0700 일회용 root, `<seed>`는 아래 1.1절로 만든 seed 디렉터리, `<venv>`·`<tmp>`는 전용 venv·TMPDIR,
`<s1>`..`<s3>`는 secret 자리다. 요청·응답 본문, 쿠키 값, token은 기록하지 않는다. 기록은 code·속성 이름·건수·시간뿐이다.

이 기록은 2dc7840 기록(`local-dryrun-2dc7840.md`) §2–§3의 `not run`(seed 미연결)을 채우는 재실행이다.
여기서 쓴 한도 값(`DEMO_MAX_VISITORS=2` 등)은 **로컬 검사용 값이며 승인값이 아니다**.

## 1. 기존 harness (C8)

```sh
UV_PROJECT_ENVIRONMENT=<venv> TMPDIR=<tmp> \
  uv run --frozen python -m pytest -q -p no:cacheprovider tests/test_demo_config.py tests/test_demo_d1_04_app.py
UV_PROJECT_ENVIRONMENT=<venv> TMPDIR=<tmp> \
  uv run --frozen python -m pytest -q -p no:cacheprovider tests/test_demo_d3_02_exports.py
```

| 명령 | 수집 | 결과 | 시간 | 판정 |
| --- | --- | --- | --- | --- |
| config + d1_04 | 46 | 45 passed, 1 failed | 163.05초 | 아래 재실행 |
| d1_04 실패 1건 단독 재실행(`GIT_DIR=<gitdir>`) | 1 | 1 passed | 20.41초 | passed |
| d3_02 exports | 39 | 39 passed | 43.36초 | passed |

- worker 1(직렬). 시작 UTC 10:20:00, 로그 `pytest.log`, `pytest-rerun.log`(coordinator 로그 디렉터리, 저장소 밖).
- 실패: `test_demo_d1_04_app.py::test_demo_boots_on_a_cli_built_seed`. seed CLI가 receipt의 `source_commit`을 얻으려고
  `git rev-parse HEAD`를 부르는데(`src/demo/seed.py:661-667`), `git archive`로 푼 `<tree>`에는 `.git`이 없어 exit 128.
  제품 판정 실패가 아니라 실행 위치(환경) 원인이다. 같은 tree에서 `GIT_DIR`만 worktree의 git 디렉터리로 주고 재실행해 passed.
- 판정: **passed**(환경 원인 1건, 재실행 통과). `health` 사례(`DEMO_MAX_RETIRED_GENERATIONS=1`)가 이 실행에 포함된다.

### 1.1 seed 빌드

```sh
env -i PATH=<PATH> HOME=<HOME> GIT_DIR=<gitdir> UV_PROJECT_ENVIRONMENT=<venv> TMPDIR=<tmp> APP_MODE=demo TESTING=false \
  ENV=production DEMO_DATA_ROOT=<unused> DEMO_SEED_DIR=<seed> DEMO_ORIGIN=https://demo.test NORMAL_ORIGIN=https://normal.test \
  DEMO_SESSION_SECRET=<s1> DEMO_CSRF_SECRET=<s2> SESSION_SECRET=<s3> \
  uv run --frozen python -m src.demo.seed build --out <seed>
```

- exit 0, 11초. `<seed>`에 `demo-seed-v1.db`, `receipt.json`. receipt `source_commit` = `37ecfb7…`(위 실행 SHA),
  `db_schema_version` = `037_demo_provider.sql`. 로그 `seed-build.log`(경로는 `<seed>`로 바꿔 저장).

## 2. 수동 부팅 (실제 seed)

stage 1 §2와 같은 격리 환경에 `DEMO_SEED_DIR=<seed>`만 더했다.

```sh
env -i PATH=<PATH> HOME=<HOME> UV_PROJECT_ENVIRONMENT=<venv> TMPDIR=<tmp> APP_MODE=demo TESTING=false ENV=production \
  DEMO_DATA_ROOT=<root> DEMO_SEED_DIR=<seed> DEMO_ORIGIN=https://demo.test NORMAL_ORIGIN=https://normal.test \
  DEMO_SESSION_SECRET=<s1> DEMO_CSRF_SECRET=<s2> SESSION_SECRET=<s3> [아래 표의 추가 환경] \
  uv run --frozen uvicorn src.main:app --host 127.0.0.1 --port 18207 --workers 1 --no-access-log
```

서버 실행은 모두 `flock /tmp/mp2-heavy.lock` 아래에서 했다. 요청은 `http://127.0.0.1:18207`로 보내고 `Host: demo.test`,
`X-Forwarded-Proto: https`, 방문객별 `X-Forwarded-For`(10.0.x.x 테스트 값, uvicorn 기본 신뢰 대상은 127.0.0.1)를 붙였다.
Secure 쿠키는 http에서 curl jar에 남지 않으므로 저장소 밖 `<tmp>`의 일회용 python 스크립트가 쿠키를 직접 다루고
code·속성 이름·건수만 출력했다. 기동 시간은 프로세스 시작부터 `/health` 200까지다.

| 부팅 | 추가 환경 | 기동 | 결과 로그 |
| --- | --- | --- | --- |
| boot1 | 없음 | 7.90초, `/health` 200 | `boot1.log`, `server-boot1.log` |
| boot1b (boot1과 같은 `<root>` 재시작) | 없음 | 8.63초, 200 | `boot1.log`, `server-boot1b.log` |
| boot2 | `DEMO_MAX_VISITORS=2` | 7.92초, 200 | `boot234.log`, `server-boot2.log` |
| boot3 | `DEMO_STARTS_PER_IP_PER_MINUTE=1` | 8.93초, 200 | `boot234.log`, `server-boot3.log` |
| boot4 | `DEMO_IDLE_TTL_SECONDS=300 DEMO_ABSOLUTE_TTL_SECONDS=1800 DEMO_CLEANUP_INTERVAL_SECONDS=5` | 8.34초, 200 | `boot234.log`, `server-boot4.log` |

- 판정: **passed**. 실제 seed로 `demo_seed()` → `load_seed` → `validate_seed`(`src/main.py:202-209, 251-262`)를 지나 부팅했다.

## 3. 검사 결과 (checklist C번호·행 번호)

| 항목 | 절차 | 결과 | 판정 |
| --- | --- | --- | --- |
| C1 / 행 19 일부 | 쿠키 없이 `GET /health` | 200, `Cache-Control: no-store`, Set-Cookie 0개 | passed |
| C2a / 행 10 | live pid(uvicorn python, `ss -ltnp`로 확인)의 `/proc/<pid>/environ`, checklist C2a 명령 | 0줄 | passed |
| C2b / 행 10 | checklist C2b awk | `pass` | passed |
| C2 secret 이름 | `DEMO_SESSION_SECRET`, `DEMO_CSRF_SECRET` 이름만 셈 | 2/2 있음(값 보지 않음) | passed |
| C3 / 행 11 표식 | `.demo-root.json` 키 | 키 4개(app_mode, kind, marker_version, root_id), app_mode=demo | passed |
| C3 / 행 11 lock | `ls -A <root>` | `.demo-root.json`, `.demo.lock` 있음 | passed |
| C3 / 행 11 권한 | `stat -c '%U %a'`, `find <root> -perm /022` | 소유자 = 실행 계정, 700, 그룹/기타 쓰기 0개 | passed |
| C3 / 행 11 symlink | `find <root> -maxdepth 1 -type l` | 0줄 | passed |
| C3 / 행 11 두 번째 lock | 실행 중 `flock -n <root>/.demo.lock true` | exit 1 | passed |
| C4 / 행 13 | `GET /demo` 200의 Set-Cookie 속성 이름 | 아래 3.1 | passed |
| C5-0 / 행 4 | 쿠키 없음 | 401 `DEMO_SESSION_REQUIRED`, no-store | passed |
| C5-1 / 행 4 | Origin = `https://normal.test` | 403 `DEMO_CSRF_INVALID` | passed |
| C5-2 / 행 4 | `x-csrf-token` 없음 | 403 `DEMO_CSRF_INVALID` | passed |
| C5-3 / 행 4 | header 값 ≠ cookie | 403 `DEMO_CSRF_INVALID` | passed |
| C5 작업 0 | 같은 jar `GET /demo/state` 전후 | 전 401 `DEMO_SESSION_REQUIRED`, 후 동일(방문 공간 없음) | passed |

C5 유효 기준 요청 `V`는 checklist대로 보내지 않았다. C6(normal의 demo 경로 404)은 C8의
`test_normal_mode_serves_no_demo_paths`(1절 passed)로만 확인했고 normal 서버는 띄우지 않았다.

### 3.1 쿠키 속성 (C4, 행 13)

| 쿠키 | 받은 속성 | 행 13 기대값 | 판정 |
| --- | --- | --- | --- |
| `demo_session_id` | HttpOnly, Max-Age=28800, Path=/, SameSite=lax, Secure. Domain 없음, Expires 없음 | Domain 없음, Path=/, HttpOnly, SameSite=Lax, Secure, Max-Age=28800 | passed |
| `demo_csrftoken` | Path=/, SameSite=lax, Secure. Domain·HttpOnly·Max-Age·Expires 없음 | Domain 없음, Path=/, HttpOnly 없음, SameSite=Lax, Secure, Max-Age/Expires 없음 | passed |

### 3.2 행 1-lite: 두 방문객 (teacher)

A(10.0.0.1)와 B(10.0.0.2)가 각각 `GET /demo` → `POST /demo/start`(role=teacher)를 했다.

| 단계 | 결과 |
| --- | --- |
| A start, B start | 둘 다 201, `browser_scope_id` 서로 다름, `<root>`의 방문객 DB 파일 2개 |
| A가 `POST /sessions`(seed 시나리오 1, A의 namespace) | 201, 새 세션 id 생성 |
| 보이는 세션 수(`GET /sessions/{1..5}` 200 개수) | A 후 2, B 전 1, B 후 1. 새 id는 A에서 보임, B에서 안 보임 |
| B가 A의 새 id를 B namespace로 `GET /sessions/{id}` | 404 `DEMO_RESOURCE_NOT_FOUND`, no-store, 본문에 A scope id 없음 |
| B 쿠키 + A namespace로 같은 id | 409 `DEMO_STALE_GENERATION`, no-store, 누출 없음 |
| B 쿠키 + A namespace로 `GET /sessions/{id}/export.csv` | 409 `DEMO_STALE_GENERATION`, no-store, 누출 없음 |

- 판정: **passed**(lite). 누출 검사는 A의 scope id 문자열이 응답 본문에 있는지만 봤다(본문은 저장하지 않음).
  제목 변경·삭제·CSV·reset을 포함한 전체 행 1은 브라우저 행이며 5절의 G5 증거를 참조한다.

### 3.3 행 15/19, 행 14-lite (로컬 검사용 값, 승인값 아님)

| 부팅 | 절차 | 결과 | 판정 |
| --- | --- | --- | --- |
| boot2 `DEMO_MAX_VISITORS=2` | 서로 다른 IP 세 방문객 start | 201, 201, 세 번째 503 `DEMO_CAPACITY`(no-store) | passed (행 15 503) |
| boot2 | 상한 도달 상태에서 C1 | 200, no-store | passed (행 19) |
| boot3 `DEMO_STARTS_PER_IP_PER_MINUTE=1` | 같은 IP 두 방문객 start | 201, 두 번째 429 `DEMO_RATE_LIMITED`(no-store). 직후 C1 200 | passed (행 14-lite) |

행 15의 507 `STORAGE_LIMIT`, 행 14의 나머지 한도(세션·사용자·turn·slot·요청/분·업로드 413)는 로컬에서 실행하지 않았다
(`not run`: 이번 범위 밖. C8 `limits` 사례가 로컬 기준).

### 3.4 행 16/18 (로컬 검사용 값, 승인값 아님)

boot4(`DEMO_IDLE_TTL_SECONDS=300`, `DEMO_ABSOLUTE_TTL_SECONDS=1800`, `DEMO_CLEANUP_INTERVAL_SECONDS=5`)에서 한 방문객이
start(201, 방문객 DB 파일 1개)한 뒤 아무 요청 없이 320초 기다렸다(10:24:09–10:29:29 UTC).

| 항목 | 결과 | 판정 |
| --- | --- | --- |
| 행 16 만료 | idle 뒤 이전 쿠키로 `GET /demo/state` → 410 `DEMO_EXPIRED`, no-store | passed |
| 행 18 cleanup tick | `Demo cleanup` 줄 66개. `removed` 합계 0 → 1(10:29:13 UTC 한 줄, start 뒤 약 304초), `pending`은 처음부터 끝까지 0, `codes` 비어 있지 않은 줄 0개, `Demo cleanup failed` 0줄. 방문객 DB 파일 1 → 0 | passed |
| 직후 C1 | 200, no-store | passed |

행 18의 "`pending`이 줄어듦"은 이번에 관찰되지 않았다. 만료 방문객이 대기 세대로 남지 않고 같은 tick에서 바로
지워져 `pending`이 0에서 오르지 않았기 때문이다. reset 뒤 대기 세대(행 17·18 조합)는 실행하지 않았다(`not run`, 브라우저/2단계).
절대 TTL 1800초 경과와 "reset으로 절대 TTL 연장 없음"은 시간이 길어 실행하지 않았다(`not run`).

## 4. 복구 절차 로컬 (checklist 3절)

| 단계 | 결과 | 판정 |
| --- | --- | --- |
| 1 신규 start 차단 | 로컬에는 승인된 차단 수단이 없다 | not run (승인 기록의 차단 수단 필요) |
| 2 demo 중단 | boot1 pid에 SIGTERM, 0.21초 뒤 종료. 이후 C1 연결 불가(curl exit 7, code 000). 정지 뒤 `flock -n` exit 0(lock 해제) | passed |
| 3 drain/dispose | `Demo shutdown` 줄 1개: `removed` 2, `pending` 0, `codes` 비어 있음 | passed |
| 4 첫 배포 복구 목표 | 이전 tuple 없음 → 정지 상태가 목표. 2단계 뒤 정지·연결 불가 확인 | passed (로컬 의미) |
| 재시작(같은 `<root>`) | boot1b 200. 실행 중 `flock -n` exit 1(새 lock 획득). A의 이전 쿠키로 `GET /demo/state` → 410 `DEMO_EXPIRED`(이전 공간 이어 붙지 않음). 새 방문객 start 201. 종료 시 `Demo shutdown` removed 1, pending 0, codes 없음 | passed |

## 5. 로컬에서 실행하지 않은 것 (G5 증거 참조)

같은 제품 tree(위 머리말)의 G5 증거로 대신한다. 이 파일에서 브라우저를 다시 돌리지 않았다.

| 행 | 이유 | 참조 |
| --- | --- | --- |
| 1 (브라우저 전체), 4 (브라우저 role·reset·삭제), 8 (여정·복구) | 브라우저 검사, 로컬 재실행 범위 밖 | `d4-03-release-acceptance.md` §3.1(CI run 39397378254, `8f97776`, browser shard 1–3 passed), §4 행 A·B·F·I |
| 5 reset race | 배포에서 재현하지 않는 행 | §4 행 E (`tests/test_demo_reset.py`), §3.1 pytest passed |
| 6 출력/재전송 | 학생·멘토 실행과 retry는 여정 행 | §4 행 G, §3.1 |
| 20 `/health` 503 backlog | 대기 세대를 쌓는 허용 행위가 로컬 서버에 없음 | 1절 C8 `health` 사례 passed(이번 실행), §3.1 |
| 12 egress | `strace` 미설치(`which strace` 없음), 설치 금지 | not run. 2단계 승인 인프라 수단 필요 |
| 2, 3, 7, 9, 17, 21(1단계), 22 | 2단계 배포 행 또는 normal 서버 필요 | not run, 2단계 |

## 6. 이 기록이 증명하지 않는 것

- 실제 host, TLS 종단, reverse proxy와 그 헤더 처리, 서비스 계정, 실제 egress 통제(방화벽·인프라 로그).
- 승인된 quota·TTL·상한 값. 여기 값은 로컬 검사용이다.
- normal 서비스와 함께 돈 상태, normal 자료·설정 불변(행 22), 배포 수단이 보고하는 SHA(행 9).
- 로그 점검: 모든 실행 로그에서 secret 세 값과 일회용 root 경로 문자열을 검색했고 일치 0건이다.

2단계(실제 환경 리허설)는 `docs/demo/d5-02-approval.md`가 비어 있고 승인 댓글이 없으므로 **미착수**로 남는다.
이 기록은 2단계 판정을 내리지 않는다.
