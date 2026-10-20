# D5-02 리허설 체크리스트 (2단계 절차, #207)

기준: main `2dc7840963e6ca700915c2c63cff7729ca54afa9`, 계약 `d0-03-v1-r3`. 인용 `파일:행`은 이 main이고,
`§N:행`은 `docs/demo/d0-03-contract-v1.md`의 행이다. 파일 규칙은 `README.md`다.
운영 배경은 #206 초안 `docs/demo/d5-01-operations.md` (branch `feat/206-ops-draft`, 미병합)다.

이 문서에는 실제 host·origin·경로·IP·secret·용량 값이 없다. 명령의 `<승인 기록 §1 DEMO_ORIGIN>` 같은
자리는 `docs/demo/d5-02-approval.md`를 @DomineYH가 채운 값을 실행자가 대입하고, 증거에는 대입한 값이 아니라
placeholder 그대로 적는다. 배포 스크립트·서비스 파일은 만들지 않는다. 배포·재시작·중단은 승인 기록의 허용 행위 안에서만 한다.

## 0. 시작 조건 (하나라도 아니면 시작하지 않는다)

1. `docs/demo/d5-02-approval.md`의 모든 항목이 채워져 있고 승인 댓글 링크가 있다. 빈칸 하나 = 배포 착수 차단(§28:769).
2. #205 G5 후보 SHA가 기록되어 있고 배포할 SHA와 전체 40자가 같다. tuple(§28:767)은 #205 기록에서 복사한다. 없는 값은 "미산출".
3. #206 PR2(확정 운영 문서)가 병합되었다. 초안 상태에서는 시작하지 않는다.
4. #194 4단계 seed 연결과 #197 seed가 병합되었다. 현재 main의 `demo_seed()`는 `CONFIGURATION_INVALID`를 던져
   demo가 부팅하지 않는다(`src/main.py:200-206`, 호출 `src/main.py:248`). 연결 전에는 아래 행 대부분이 `not run`이다.
5. 실행 책임자와 G6 승인자는 @DomineYH, 증거 검토는 승인 기록의 reviewer 세션이다.

조건 미충족 때문에 시작하지 않은 것은 `not run`이 아니라 "미착수"다. 증거 파일을 만들지 않는다.

## 1. 읽기 전용 명령 (표의 C번호)

- C1 health 코드와 헤더. 이 route는 GET만 등록되어 HEAD를 쓰지 않는다(`src/api/routes/health.py:101-103`).
  `curl -s -D - -o /dev/null -w '%{http_code}\n' "<승인 기록 §1 DEMO_ORIGIN>/health"` — 코드와 `Cache-Control`만 기록.
- C2 demo 프로세스 환경. pid는 승인 기록의 서비스 식별자로 얻는다. 값은 출력하지 않는다.
  - C2a 금지 자격 증명 이름만: `tr '\0' '\n' < /proc/<pid>/environ | cut -d= -f1 | sort | grep -E '^(OPENAI_|ANTHROPIC_|AZURE_OPENAI_|GOOGLE_|GEMINI_|PROVIDER_SECRET_)|^DATABASE_URL$'`
    — 출력 0줄이어야 한다. 금지 이름 전체는 `src/config.py:38-61`, 거절 조건은 `src/config.py:162-167`이다.
    빈 값은 앱이 통과시키지만 운영 자격 증명은 빈 값으로도 주입하지 않으므로 이름이 있으면 실패다.
  - C2b boolean flag는 따로 판정하고 `pass`/`fail`만 출력한다:
    `tr '\0' '\n' < /proc/<pid>/environ | awk '{n=index($0,"="); k=substr($0,1,n-1); if((k=="TESTING"||k=="BOOTSTRAP_ADMIN_ON_STARTUP")&&tolower(substr($0,n+1))=="true")b=1} END{print b?"fail":"pass"}'`
    — `pass`여야 한다. 앱은 소문자 변환 값이 `true`일 때만 참으로 읽고(`src/config.py:313-320`), demo에서 참이면 부팅을
    거절한다(`src/config.py:213-214`). 명시적 `false`나 이름 없음은 통과다.
  - secret 이름(`DEMO_SESSION_SECRET`, `DEMO_CSRF_SECRET`)은 존재해야 하고 값은 보지 않는다.
- C3 root. 표시는 `<승인 기록 DEMO_DATA_ROOT>` 참조로만 쓰고 출력 경로는 증거에 옮기지 않는다.
  `ls -A <root>`에 `.demo-root.json`과 `.demo.lock`이 있다(`src/demo/database.py:159, 186`). 표식은 키 4개(kind, marker_version,
  root_id, app_mode)이고 app_mode=demo다(`src/demo/database.py:176-183`): `jq -r 'keys[]' <root>/.demo-root.json`(값은 기록하지 않음).
  `stat -c '%U %a' <root>` — 소유자가 승인된 서비스 계정, 그룹/기타 쓰기 비트 없음(`src/demo/database.py:147-149`).
  `find <root> -maxdepth 1 -type l` 출력 0줄. 서비스가 떠 있는 동안 `flock -n <root>/.demo.lock true; echo $?`는 `1`이다
  (`src/demo/database.py:189-191`; 서비스가 서 있지 않을 때 실행하면 의미 없음).
- C4 쿠키 속성(값 가림). `curl -s -D - -o /dev/null "<승인 기록 §1 DEMO_ORIGIN>/demo" | grep -i '^set-cookie' | sed -E 's/^(set-cookie: [^=]+)=[^;]*/\1=<omitted>/I'`
- C5 CSRF·origin 거절. 먼저 유효 bootstrap을 받고, 검사하는 조건 하나만 바꾼다.
  - 준비: 증거 디렉터리 밖 임시 파일 `J=$(mktemp)`에 `curl -s -c "$J" -o /dev/null -w '%{http_code}\n' "<승인 기록 §1 DEMO_ORIGIN>/demo"` → 200.
    `T=$(awk -F'\t' '$6=="demo_csrftoken"{print $7}' "$J")`. jar와 token은 출력·기록하지 않고 끝나면 `rm -f "$J"`.
  - 유효 기준 요청 `V` = `curl -s -o /dev/null -w '%{http_code}\n' -X POST -b "$J" -H 'Origin: <승인 기록 §1 DEMO_ORIGIN>' -H "x-csrf-token: $T" -H 'content-type: application/json' --data '{"request_id":"<새 UUID>","role":"teacher"}' "<승인 기록 §1 DEMO_ORIGIN>/demo/start"`.
    아래 사례는 `V`에서 표시한 부분만 바꾼다. 기준 요청 자체는 C5에서 보내지 않는다(방문 공간을 만들기 때문).
  - C5-0 `-b "$J"` 제거(서명 없음) → **401** `SESSION_REQUIRED`. 서명 검사가 CSRF보다 먼저다(`src/api/dependencies.py:584-588`, code 표 `src/demo/database.py:24`).
  - C5-1 Origin을 `<승인 기록 §1 NORMAL_ORIGIN>`으로 → **403** `CSRF_INVALID`(`src/api/dependencies.py:615-654`, `src/demo/database.py:37`).
  - C5-2 `x-csrf-token` header 제거 → **403**. C5-3 header 값을 cookie와 다른 값으로 → **403**.
  - 작업 0: 같은 jar로 `GET /demo/state`의 HTTP code를 C5 전후에 받아 같아야 한다.
  route는 `src/api/routes/demo.py:241, 253, 276`이다. `/demo/role`, `/demo/reset`은 행 4의 브라우저 검사에서 같은 사례를 확인한다.
- C6 normal에 demo 경로 없음. `curl -s -o /dev/null -w '%{http_code}\n' "<승인 기록 §1 NORMAL_ORIGIN>/demo"` (GET /demo, /demo/state, POST start/role/reset 모두 404; 로컬 기준 `tests/test_demo_d1_04_app.py:152-160`).
- C7 서비스 로그의 cleanup·종료 줄. 승인 기록의 로그 조회 수단으로 `Demo cleanup`, `Demo cleanup failed`, `Demo shutdown` 줄 수와 `removed`/`pending`/`codes`
  필드만 센다(`src/main.py:216-229, 291-298`). 줄 전체를 붙이지 않는다.
- C8 같은 SHA 선행 로컬 검사: `uv run --frozen python -m pytest -q tests/test_demo_config.py tests/test_demo_d1_04_app.py`.

## 2. 검사표

기존 검사 중 `tests/test_demo_isolation.py`, `tests/test_demo_security.py`, `tests/test_demo_reset.py`,
`tests/browser_demo_isolation.mjs`, `tests/browser_demo_journey.mjs`, `tests/browser_demo_recovery.mjs`는 #203/#204 산출물(§26:694-695)이다.
이 main(2dc7840)에는 **없다**(ls로 확인). 병합 뒤 사용하고, 없으면 해당 행 증거는 `not run`이다. `tests/test_demo_d3_02_exports.py`는 있다.
결과 칸은 모두 `passed` / `failed` / `not run`(이유)이며 기록 파일에만 적는다.

| # | 검증 | 근거(§) | 명령/절차 | 기대값 | 증거 파일 |
| --- | --- | --- | --- | --- | --- |
| 1 | 두 방문객 격리 | §28:758 | 실제 브라우저 context 둘로 같은 배포 origin 사용. A에서 제목 변경·삭제·CSV·reset 후 B의 목록·총계·상세 확인. `tests/browser_demo_isolation.mjs` (#203/#204 산출물, 병합 뒤 사용) | B 불변. A scope+ID로 B가 요청하면 본문 없는 거절 | `isolation-<sha7>.md` |
| 2 | normal 부정 | §28:759 | C6. normal 로그인·권한은 변경 없이 읽기 확인만 | 404 5종 | `isolation-<sha7>.md` |
| 3 | AI 무호출 | §28:760, §14:353 | 행 12와 같은 egress 창 안에서 학생/멘토/분석/retry/catalog/probe 포함 여정. `tests/test_demo_security.py` (#203 산출물, 병합 뒤 사용) | 외부 시도 0 | `egress-<sha7>.log` |
| 4 | 실제 CSRF/cookie | §28:761, §10:259 | C5, 그리고 브라우저로 missing/invalid/cross-origin start·role·reset·삭제 | 서명 없음 401, 유효 서명에서 CSRF/Origin 오류 403, 작업 0. normal 쿠키는 demo에서 인증 안 됨, 역방향도 동일 | `isolation-<sha7>.md` |
| 5 | reset race | §28:762 | 두 탭 중복 reset과 만료 후 reset을 실행. barrier 양쪽 순서는 `tests/test_demo_reset.py` (#203 산출물)이며 배포에서 재현하지 않고 #205 G5 기록의 같은 SHA 증거 링크로 대체 | generation 단일 증가, 안전 상태. 링크의 SHA가 다르면 `not run` | `isolation-<sha7>.md` |
| 6 | 출력/재전송 | §28:763 | 여정에서 학생/멘토 실행과 명시 retry 1회. 같은 ID 중복 실행 여부는 방문객 화면 기준 | 같은 ID 1회, retry는 같은 turn | `rehearsal-<sha7>.md` |
| 7 | 삭제/export | §28:764 | 세션 삭제 후 목록·상세·CSV에서 비노출 확인. `tests/test_demo_d3_02_exports.py`를 C8과 함께 선행 | 삭제 세션 모든 경로 비노출, 다른 방문객 불변 | `isolation-<sha7>.md` |
| 8 | 실제 여정/복구 | §28:765 | A/B/C 각 완주, 두 탭·refresh·390px. `tests/browser_demo_journey.mjs`, `tests/browser_demo_recovery.mjs` (#204 산출물, 병합 뒤 사용) | 각 완주, 중복 방지 | `rehearsal-<sha7>.md` |
| 9 | 지정 SHA 배포 | §28:767, §27 G5 | 배포 수단이 보고한 SHA를 승인 기록 SHA·#205 후보 SHA와 대조. tuple 표를 복사해 `rehearsal-<sha7>.md`에 둔다 | 세 SHA 전체 일치, `git status` clean | `rehearsal-<sha7>.md` |
| 10 | 키 부재 | §2:35, §14:347 | C2 | C2a 출력 0줄, C2b `pass` | `rehearsal-<sha7>.md` |
| 11 | root 표식·lock·운영 mount 부재 | §2:28, §2:33 | C3. 운영 mount·DB 접근 경로가 프로세스에서 안 보임은 승인 기록 제시 방법으로 확인하고 경로는 옮기지 않음 | 표식·lock 있음, symlink 0, 두 번째 lock 시도 실패, 운영 경로 없음 | `rehearsal-<sha7>.md` |
| 12 | egress 0 | §14:353 | 승인된 인프라 로그/방화벽 수단으로 여정 전체(행 1-8, 13-20) 동안 demo 프로세스의 외부 연결을 센다 | 허용 목적지(승인 기록 값) 외 0건 | `egress-<sha7>.log` |
| 13 | 쿠키 속성 | §10:254-255 | C4. 발급 코드는 `src/api/routes/demo.py:161-177` | `demo_session_id`: Domain 없음, Path=/, HttpOnly, SameSite=Lax, Secure, Max-Age=28800. `demo_csrftoken`: Domain 없음, Path=/, HttpOnly 없음, SameSite=Lax, Secure, Max-Age/Expires 없음 | `isolation-<sha7>.md` |
| 14 | quota 429·413 | §8:202-207 | 승인 기록의 최종값에서 세션·사용자·turn·활성 slot·요청/분·start/IP/분·업로드 한도를 초과시킨다. 로컬 참조: `tests/test_demo_d1_04_app.py:77-83` | 승인값 초과 시 429, 업로드는 413. 한도 이하는 정상 | `rehearsal-<sha7>.md` |
| 15 | 503·507 | §8:199-201 | 방문객 수를 `DEMO_MAX_VISITORS` 승인값까지 채워 다음 start를 시도. 디스크·DB 한도는 승인된 방법(허용 행위에 있을 때만)으로 | 503 CAPACITY, 507 STORAGE_LIMIT. 허용 행위에 없으면 `not run`(이유) | `rehearsal-<sha7>.md` |
| 16 | 만료 410 | §8:197-198 | idle 승인값 경과 뒤 기존 쿠키로 요청 | 410. reset으로 절대 TTL 연장 없음 | `rehearsal-<sha7>.md` |
| 17 | reset | §4:68, §10:259 | 방문객 화면에서 reset 1회 후 generation 증가와 이전 자료 비노출 | 단일 증가, 새 공간 | `isolation-<sha7>.md` |
| 18 | cleanup tick | §8:208, `src/main.py:209-230` | 행 16·17 뒤 `DEMO_CLEANUP_INTERVAL_SECONDS` 승인값 이상 대기 후 C7 | `removed`가 늘고 `pending`이 줄어듦. `codes` 비어 있음 | `rehearsal-<sha7>.md` |
| 19 | `/health` 200 (방문객 상한) | §4:65, `src/api/routes/health.py:79-84` | 행 15에서 상한에 닿은 동안 C1 | 200 (상한만으로 503 아님) | `health-<sha7>.log` |
| 20 | `/health` 503 (backlog) | §4:65, §8:209 | 대기 세대가 `DEMO_MAX_RETIRED_GENERATIONS` 승인값에 닿는 상태에서 C1. 이 상태를 만드는 방법이 허용 행위에 없으면 `not run` | 503, 본문에 경로·방문객 수 없음 | `health-<sha7>.log` |
| 21 | rollback/중단 | §28:771 | 3절 | 3절의 순서와 확인값 | `recovery-<sha7>.md` |
| 22 | normal 자료·설정 불변 | §28:771 | 3절 시작 전과 끝난 뒤에 같은 지표를 같은 명령으로 대조(승인 기록의 rollback 범위에 적힌 normal 지표: 설정 참조/버전, 스키마 버전, 행 수. 없으면 실행자가 시작 전에 정해 기록하고 reviewer가 확인) | 일치. normal 서비스 재시작·갱신 0회 | `recovery-<sha7>.md` |

`/health` 200 기준 본문은 `status=healthy` 등 세 필드뿐이다(`src/api/routes/health.py:90-98`). 응답 본문은 기록하지 않는다.

## 3. 복구 절차 (§28:771 순서 그대로)

1. 신규 start 차단: 차단 직전에 C5 준비 절차로 새 jar를 받는다(GET /demo가 막히기 전). 승인된 수단으로 막은 뒤 그 jar로
   C5의 유효 기준 요청 `V`(같은 origin, 유효 서명·token, 새 UUID)를 한 번 보낸다. 기대값: 성공 code(2xx)가 아니고
   (연결 불가 또는 차단 수단의 거절 code), 그 jar의 `GET /demo/state` code가 차단 전과 같아 방문 공간이 생기지 않았다.
   C5의 무효 요청은 admission이 열려 있어도 거절되므로 차단 증거로 쓰지 않는다.
2. demo 중단: 승인된 서비스 수단으로 정지. C1이 응답 불가임을 확인.
3. 진행 작업 취소/drain/dispose: 종료 로그의 `Demo shutdown` 줄(`src/main.py:291-298`)에서 `pending`·`codes`만 기록. 범위별 drain은 최대 `DEMO_DRAIN_SECONDS`(`src/main.py:289`).
4. 승인된 이전 동일 tuple로 복구: 승인 기록의 이전 tuple을 같은 수단으로 시작하고 C1=200과 tuple 일치를 기록.
   첫 배포면 이전 tuple이 없으므로 **승인된 중단 상태**가 복구 목표다. 서비스 정지·start 차단 유지를 확인하고 끝낸다.

운영 DB 복원은 없다. 무손실 자동 rollback을 가정하지 않는다. 재시작은 이전 방문 공간을 이어 붙이지 않는다(§4:68).
normal DB/설정은 바꾸지 않고, 코드가 같다는 이유로 두 서비스를 자동 동시 갱신하지 않는다(§28:771).

## 4. 판정과 G6

- 22행 중 하나라도 `failed` 또는 `not run`이면 `d5-02-rehearsal-evidence.md`의 판정은 **공개 중단**이다. #208은 이 판정을 읽는다.
- 모두 `passed`일 때만 "공개 가능 후보"로 적고, 공개는 별도 승인(D5-03)이다.
- G6 기록: 승인자 @DomineYH / 시각 / SHA — 2단계 실행 전에는 비어 있다. 실행자나 reviewer가 대신 적지 않는다.
