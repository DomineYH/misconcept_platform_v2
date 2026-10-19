# D5-01 demo 배포·제한·복구 운영 문서 (#206 초안)

상태: **초안(PR1)**. 확정(PR2)은 #205가 `docs/demo/d4-03-release-acceptance.md`에
G5 승인 후보와 검사 결과를 기록한 뒤에 한다(계약 §27:714, §28:769).
기준: main `2dc7840963e6ca700915c2c63cff7729ca54afa9`(#224/#225/#227 병합 후),
계약 `d0-03-v1-r3` 의미
(`docs/demo/d0-03-contract-v1.md`). 인용 `파일:행`은 이 기준의 main이다.

이 문서에는 실제 host, origin, URL, 경로, mount, secret, 용량을 적지 않는다.
그 값은 모두 D5-02(#207, `docs/demo/d5-02-approval.md`) 승인 입력이다.
승인값이 없거나 범위 검증에 실패하면 **배포 착수 차단**이다(§28:769).
설정 예시는 `docs/demo/d5-01-deployment.env.example`이다.

## 1. 별도 origin과 쿠키 (§2, §10)

- demo는 normal과 다른 host의 단일 origin에서 따로 실행한다. `DEMO_ORIGIN`과
  `NORMAL_ORIGIN`은 둘 다 필수이고 host가 달라야 한다(`src/config.py:218-223`).
  userinfo/path/query/fragment, wildcard `*`, 쉼표 다중 origin은 거절된다
  (`src/config.py:79-96`). production은 HTTPS만, development만 loopback HTTP를 허용한다.
- demo에서는 CORS middleware와 normal Session/CSRF middleware를 붙이지 않는다
  (`src/main.py:500-542`).
- 쿠키 이름은 고정이며 바꾸는 설정이 없다(§10:257). 발급: `src/api/routes/demo.py:161-177`.
  - `demo_session_id`: host-only(Domain 없음), Path=/, HttpOnly, SameSite=Lax,
    production Secure, Max-Age=28800.
  - `demo_csrftoken`: host-only, Path=/, JS가 읽음(HttpOnly 아님), SameSite=Lax,
    production Secure, Max-Age 없음(브라우저 세션 수명).
  - 쿠키의 8시간은 DB TTL이 아니다. 서버 만료가 우선한다.
- `DEMO_PUBLIC_URL`은 normal 서비스가 demo 링크를 보여 줄 때 쓴다. 비어 있으면 링크를
  숨긴다. 값이 있으면 path는 `/` 또는 `/demo`이고 normal host와 달라야 한다
  (`src/config.py:199-210`, §26:633).

## 2. 전용 root와 단일 프로세스 (§2, §4)

- `DEMO_DATA_ROOT`는 절대 경로이고 `..`가 없어야 한다(`src/config.py:215-217`).
  root는 실행 사용자 소유, group/world 쓰기 없음, repo와 겹치지 않아야 한다
  (`src/demo/database.py:147-158`). 운영 mount·일반 DB와의 분리는 D5-02에서 증명한다.
- 첫 실행은 빈 root에 표식 `.demo-root.json`을 만든다. 표식이 없는데 root가 비어 있지
  않으면 거절한다(`src/demo/database.py:159-183`).
- `.demo.lock`에 비차단 배타 `flock`을 잡는다. 두 번째 프로세스는 실패한다
  (`src/demo/database.py:186-195`).
- `DEMO_WORKERS`의 유일한 허용값은 1이다(`src/demo/visitors.py:48`). uvicorn은
  `--workers 1`(기본값)으로 실행하고 `WEB_CONCURRENCY`를 설정하지 않는다.
  uvicorn 0.38.0의 `--workers` 기본값은 `$WEB_CONCURRENCY` 또는 1이다.
  worker가 둘이면 둘째의 lock 획득이 실패한다. `--reload`는 개발 전용이다.
- 프록시: start의 IP별 한도는 ASGI client 주소를 쓴다
  (`src/api/dependencies.py:746-752`). `--forwarded-allow-ips`에는 실제 프록시 주소만 넣고 `*`를 쓰지 않는다. 실제 프록시 주소는 D5-02 입력이다.
- 접근 로그: 자료 URL에는 `ds`/`dg`/`da` query가 들어간다(§10:265). uvicorn 접근 로그는
  query를 포함한 경로를 출력한다(uvicorn 0.38.0 `protocols/utils.py:52-56`).
  `--no-access-log`로 끄거나 마스킹한다. 앞단 프록시의 접근 로그도 같다(§4:74).
  앱의 `LoggingMiddleware`는 path만 기록한다(`src/main.py:92-104`).

## 3. 시작·종료·재시작 (§4:67-68)

- import 시점의 설정 검증(`src/config.py:153-259`)이 실패하면 프로세스가 시작되지 않는다.
- 시작(`src/main.py:233-279`): root open과 lock → seed 검증
  (`root.validate_seed`) → registry/session 구성 → cleanup 작업 시작. 일반 `init_db`,
  admin bootstrap, 운영 orphan 검색은 호출하지 않는다. 실패는 경로 없는 code로만 보고한다.
- 현재 `demo_seed()`는 fail-closed다(`src/main.py:200-206`). #194 4단계가
  #197 seed에 연결하기 전까지 demo는 시작을 거절한다. seed 위치는 D5-02 입력이다.
- 종료(`src/main.py:280-301`): cleanup 취소 → session 종료 → 모든 방문 공간을
  `shutdown`으로 만료하고 범위별로 최대 `DEMO_DRAIN_SECONDS` drain → 정리 → lock 해제
  (`src/demo/visitors.py:1214-1222`).
- 재시작: 이전 공간을 이어 붙이지 않는다. root의 남은 세대는 orphan으로 정리 queue에
  들어가고(`src/demo/visitors.py:335-338`) 이전 서명 쿠키는 registry에 없어 만료된다.

## 4. 상한·TTL (§8:191-213)

아래 숫자는 모두 **제안값 / D5-02 승인 입력**이다. 계약·구현 기본값이며 승인된 운영
용량이나 보존 정책이 아니다. 30분/2시간은 방문 공간의 수명이다. 운영 로그·백업 보존
정책과는 별개이고, 그 정책은 D5-02에서 정한다. 범위 검사: `src/demo/visitors.py:46-61`,
`src/config.py:237-258`.

| 설정 | 제안값 | 허용 범위 | 초과 시 |
| --- | --- | --- | --- |
| `DEMO_IDLE_TTL_SECONDS` | 1800 | 300..7200 | 410 만료 |
| `DEMO_ABSOLUTE_TTL_SECONDS` | 7200 | 1800..28800, idle 이상 | 410 만료, reset으로 연장 없음 |
| `DEMO_MAX_VISITORS` | 20 | 1..1000 | 503 CAPACITY |
| `DEMO_MAX_DB_BYTES` | 33554432 | 1048576..268435456 | 507 STORAGE_LIMIT |
| `DEMO_MAX_DISK_BYTES` | 1073741824 | 67108864..10737418240 | 507 STORAGE_LIMIT |
| `DEMO_DRAIN_SECONDS` | 5 | 1..30 | 503 drain timeout |
| `DEMO_CLEANUP_INTERVAL_SECONDS` | 30 | 5..300 | 다음 tick 재시도 |
| `DEMO_MAX_RETIRED_GENERATIONS` | 40 | 1..2000 | 503 CLEANUP_BACKLOG |
| `DEMO_MAX_TURNS` / `_SESSIONS` | 10 / 10 | 2..100 | 429 |
| `DEMO_MAX_USERS` / `_GROUPS` / `_SCENARIOS` / `_CONTRIBUTORS` | 10/5/5/5 | 2..100 / 1..20 | 429 |
| `DEMO_MAX_UPLOAD_BYTES` / `_ROWS` | 65536 / 8 | 1024..1048576 / 1..98 | 413 |
| `DEMO_MAX_ACTIVE_GLOBAL` / `_VISITOR` | 8 / 2 | 2..64 / 1..4, visitor ≤ global | 429 |
| `DEMO_REQUESTS_PER_MINUTE` / `DEMO_STARTS_PER_IP_PER_MINUTE` | 60 / 10 | 10..600 / 1..60 | 429, 고정 60초 window |

- 교차 검증: `max_disk ≥ seed + 2 × max_db`, `seed ≤ max_db`, absolute ≥ idle이 아니면
  부팅 거절(`src/demo/visitors.py:62-69`, seed 크기 재검사 `src/demo/database.py:262-267`).
- 디스크 admission: 새 공간은 `max_db` 만큼 미리 예약한다. root 사용량 + root 밖 seed 크기 +
  기존·신규 예약이 `max_disk`를 넘으면 507이다(`src/demo/visitors.py:507-525`).
  상태 코드: `src/demo/database.py:28-50`.
- cleanup: `DEMO_CLEANUP_INTERVAL_SECONDS`마다 만료 공간을 정리한다
  (`src/demo/visitors.py:1206-1212`). 잠긴 DB는 `locked`로 남겨 다음 tick에 재시도하고,
  해제 없이 삭제하지 않는다(`src/demo/visitors.py:1110-1135`). 대기 세대가 상한에 닿으면
  새 start/reset은 503 CLEANUP_BACKLOG다(`src/demo/visitors.py:499-505`).

## 5. health와 metrics (§4:64-66)

- `GET /health`는 쿠키·방문객 DB 없이 root 표식/lock, manifest hash, seed의 read-only
  조회, cleanup 작업 상태와 대기 세대 수를 검사한다
  (`src/api/routes/health.py:50-98`). 정상이면 200 `status=healthy`, 아니면 503
  `status=degraded, database=unhealthy`. 경로·방문객 수·secret은 내보내지 않는다.
- 방문객 상한 도달만으로는 readiness를 내리지 않는다. seed 실패, cleanup hard cap, root
  오류는 503이다. 별도 readiness URL은 없다.
- `GET /metrics`는 현재 방문객의 관리자만, 그 방문객 DB 집계만 준다. 전역 수치는 서버
  운영 로그(`Demo cleanup` 로그의 removed/pending/codes)로 본다.

## 6. seed·fixture 교체와 유지보수 (§17, 이슈 본문 §11)

- 교체는 새 버전 seed로 새 방문을 받는 것이다. 배포 시 기존 공간은 `version_replace`로
  모두 만료하고 진행 중 공간에 migration을 적용하지 않는다(§17:448).
- manifest 필드·파일 hash·migration set이 맞지 않으면 시작을 거절하고 health는 503이다.
  기존 DB로 fallback하지 않는다(§17:423, `src/demo/database.py:250-283`).
- 새 AI 역할, 출력 schema, stream event, model option이 생기면 fixture 계약도 함께 검증한다.
  fixture 변경은 fixture/seed/capability 버전 상승과 검증 재실행을 요구한다(§17:444).

## 7. secret 분리와 egress (§2, §14)

- demo secret 두 개는 32자 이상, 서로 다르고, normal `SESSION_SECRET`과도 다르며, 금지
  패턴이 없어야 한다(`src/config.py:224-234`, 패턴 `src/config.py:27-36`). 값과 hash는
  어떤 문서·증거에도 기록하지 않는다. settings hash에는 주입 참조/버전만 넣는다(§28:767).
- demo 환경에 값이 비어 있지 않은 `DATABASE_URL` 또는 `DEMO_FORBIDDEN_ENV`
  (`src/config.py:38-61`)의 키, `TESTING=true`, `BOOTSTRAP_ADMIN_ON_STARTUP=true`가
  있으면 부팅 거절이다(`src/config.py:162-167, 213`). 빈 값은 통과하지만 운영 DB URL·
  mount·normal secret·운영 자격 증명은 빈 값으로도 demo에 주입하지 않는다.
- 외부 AI 시도는 0이어야 한다(§14:353). 인프라 outbound allowlist와 운영 mount 부재의
  증명은 D5-02 범위다. 이 문서는 인프라를 바꾸지 않는다.

## 8. 배포·복구·승인 (§28:767-771)

- normal과 demo는 같은 후보 SHA로 따로 배포한다. 코드가 같다는 이유로 두 서비스를
  자동으로 동시에 갱신하지 않는다. normal DB와 설정은 바꾸지 않는다.
- 리허설 실패 시: 신규 start 차단 → demo 중단 → 진행 작업 취소/drain/dispose →
  승인된 이전 동일 tuple로 복구. 첫 배포면 중단 상태가 복구 목표다.
- 별도 승인: D5-02 환경과 행위(host/origin/URL/root/mount/용량/quota/최종 TTL/서비스와
  주입 권한/rollback 대상), D5-03 공개, secret·지속 접근·보안/네트워크 변경.

### 릴리스 tuple — #205 G5 기록으로 확정

| 항목 | 값 |
| --- | --- |
| `candidate_commit` / `normal_commit` / `demo_commit` | |
| `contract_version` / `contract_commit` | |
| `content_version` | |
| `fixture_version` / `manifest_sha256` | |
| `db_schema_version` / `migration_set_sha256` | |
| `seed_sha256` / `logical_data_sha256` / 생성 명령 | |
| `settings_sha256` (secret 제외) | |
| 검사 SHA·명령·결과(passed/failed/not run)·증거 링크 | |
