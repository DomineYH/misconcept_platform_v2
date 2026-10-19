# D5-02 demo 배포 리허설 승인 기록 (#207)

상태: **1단계 골격**. 모든 값 칸이 비어 있다. 2단계는 이 기록이 완성된 뒤에만 시작한다.
기준: main `2dc7840963e6ca700915c2c63cff7729ca54afa9`, 계약 `d0-03-v1-r3` 의미
(`docs/demo/d0-03-contract-v1.md`). 인용 `파일:행`은 이 기준의 main이다.

**값 없음 = 배포 착수 차단** (계약 §28:769). 범위 검증에 실패하는 값도 차단이다.
승인 자체는 PR이 아니라 @DomineYH의 #207 이슈 댓글이며, 그 링크를 §8에 적는다.
이 파일에 실제 host, URL, 경로, secret, hash, 용량 값을 미리 적지 않는다.
값은 승인자가 채우고, 채운 뒤에는 승인 댓글과 일치해야 한다.

배포 제어·health 통신 승인은 외부 AI 호출 승인이 아니다(§14:353). repo에는 배포 파일을
쓰지 않는다. 같은 host의 sub-path 배포는 MVP 밖이다. normal과 demo는 프로세스와 데이터를
분리하고, demo는 단일 app worker다.

## 1. 대상 환경

| 항목 | 값 | 채우는 사람 | 출처/검증 |
| --- | --- | --- | --- |
| 대상 host | | @DomineYH | normal과 다른 host (§2:29) |
| `DEMO_ORIGIN` | | @DomineYH | 단일 origin, production HTTPS, userinfo/path/query/fragment 없음 (§2:29, `src/config.py:218-223`) |
| `DEMO_PUBLIC_URL` | | @DomineYH | path `/` 또는 `/demo`, normal host와 다름 (§2:32, `src/config.py:199-210`) |
| `NORMAL_ORIGIN` | | @DomineYH | demo에서 필수, `DEMO_ORIGIN`과 host 다름 (§2:29, `src/config.py:195-198`) |
| 서비스 식별자 (normal / demo) | | @DomineYH | 두 서비스는 따로 배포·갱신 (§28:771) |
| `DEMO_DATA_ROOT` | | @DomineYH | 절대 경로, `..` 없음, symlink 없음 (§2:28, `src/config.py:215-217`) |
| root mount / 소유권 / 권한 | | @DomineYH | 전용 소유권, 운영 mount·repo·일반 DB와 분리 (§2:28) |
| 신뢰 프록시 주소 (`--forwarded-allow-ips`) | | @DomineYH | 실제 프록시 주소만, `*` 금지 (§8:207) |

## 2. 후보 SHA·설정 버전·릴리스 tuple

#205 G5 기록(`docs/demo/d4-03-release-acceptance.md`)에서 복사. 없는 값은 `미산출` (계약 §28:767).
#207은 복사만 하고 값을 만들어 내지 않는다. 비밀 자체의 값·hash는 적지 않는다.

| 항목 | 값 | 채우는 사람 | 출처/검증 |
| --- | --- | --- | --- |
| `candidate_commit` / `normal_commit` / `demo_commit` (동일 전체 SHA) | | #205 G5 기록에서 복사 | 세 값이 같은 전체 SHA (§28:767) |
| `contract_version` / `contract_commit` | | #205 G5 기록에서 복사 | §28:767 |
| `content_version` | | #205 G5 기록에서 복사 | §28:767 |
| `fixture_version` / `manifest_sha256` | | #205 G5 기록에서 복사 | §28:767 |
| `db_schema_version` / `migration_set_sha256` | | #205 G5 기록에서 복사 | §28:767 |
| `seed_sha256` / `logical_data_sha256` / 생성 명령 | | #205 G5 기록에서 복사 | §28:767 |
| `settings_sha256` (secret 제외, 주입 참조/버전만) | | #205 G5 기록에서 복사 | 정규화 설정 + 주입 참조/버전만 hash (§28:767) |
| 검사 SHA·명령·결과(passed/failed/not run)·증거 링크 | | #205 G5 기록에서 복사 | §28:773 |

## 3. 허용 행위

허용하지 않은 행은 금지다. 빈 칸 = 허용 안 됨 = 착수 차단.

| 항목 | 값 | 채우는 사람 | 출처/검증 |
| --- | --- | --- | --- |
| 배포 | | @DomineYH | 후보 SHA 한정 (이 기록 2절 tuple) |
| 재시작 | | @DomineYH | 재시작은 이전 공간을 만료시킴 (§4:68) |
| 중단 | | @DomineYH | 복구 목표 (이 기록 6절) |
| mount·권한 설정 | | @DomineYH | 전용 root, 운영 mount 부재 (§28:769) |
| egress 설정 | | @DomineYH | 외부 AI 시도 0 (§14:353) |
| 허용 health 네트워크 목적지 | | @DomineYH | 쿠키 없는 `GET /health` 200/503 (§4:64) |
| 허용 browser 네트워크 목적지 | | @DomineYH | `DEMO_ORIGIN` 외 허용 목적지 (§14:353) |

## 4. secret 주입 참조

값·hash는 적지 않는다. 주입 수단과 참조 식별자만 적는다.

| 항목 | 값 | 채우는 사람 | 출처/검증 |
| --- | --- | --- | --- |
| `DEMO_SESSION_SECRET` 주입 수단 | | @DomineYH | 32자 이상, 금지 패턴 없음 (`src/config.py:228-234`) |
| `DEMO_SESSION_SECRET` 참조 식별자 | | @DomineYH | normal `SESSION_SECRET`과 다름 (`src/config.py:228`) |
| `DEMO_CSRF_SECRET` 주입 수단 | | @DomineYH | session secret과도 다름 (`src/config.py:228`, §2:31) |
| `DEMO_CSRF_SECRET` 참조 식별자 | | @DomineYH | §2:31 |
| 금지 환경 변수 부재 확인 방법 | | @DomineYH | `DATABASE_URL`, `DEMO_FORBIDDEN_ENV` (`src/config.py:38-61`, `162-167`) |

## 5. 최종 quota/TTL·용량 검증

최종값 = 승인 입력, 제안값은 계약·구현 기본값이며 승인 용량이 아니다 (§8:193).
범위 밖·bool/소수/음수·무제한 sentinel은 `DEMO_CONFIGURATION_INVALID`다.

| 설정 | 제안값(기본값) | 허용 범위 | 최종값 | 채우는 사람 |
| --- | --- | --- | --- | --- |
| `DEMO_WORKERS` | 1 | 1 (유일 허용값) | | @DomineYH |
| `DEMO_IDLE_TTL_SECONDS` | 1800 | 300..7200 | | @DomineYH |
| `DEMO_ABSOLUTE_TTL_SECONDS` | 7200 | 1800..28800, idle 이상 | | @DomineYH |
| `DEMO_MAX_VISITORS` | 20 | 1..1000 | | @DomineYH |
| `DEMO_MAX_DB_BYTES` | 33554432 | 1048576..268435456 | | @DomineYH |
| `DEMO_MAX_DISK_BYTES` | 1073741824 | 67108864..10737418240 | | @DomineYH |
| `DEMO_MAX_TURNS` | 10 | 2..100 | | @DomineYH |
| `DEMO_MAX_SESSIONS` | 10 | 2..100 | | @DomineYH |
| `DEMO_MAX_USERS` | 10 | 2..100 | | @DomineYH |
| `DEMO_MAX_GROUPS` | 5 | 1..20 | | @DomineYH |
| `DEMO_MAX_SCENARIOS` | 5 | 1..20 | | @DomineYH |
| `DEMO_MAX_CONTRIBUTORS` | 5 | 1..20 | | @DomineYH |
| `DEMO_MAX_UPLOAD_BYTES` | 65536 | 1024..1048576 | | @DomineYH |
| `DEMO_MAX_UPLOAD_ROWS` | 8 | 1..98 | | @DomineYH |
| `DEMO_MAX_ACTIVE_GLOBAL` | 8 | 2..64 | | @DomineYH |
| `DEMO_MAX_ACTIVE_VISITOR` | 2 | 1..4, global 이하 | | @DomineYH |
| `DEMO_REQUESTS_PER_MINUTE` | 60 | 10..600 | | @DomineYH |
| `DEMO_STARTS_PER_IP_PER_MINUTE` | 10 | 1..60 | | @DomineYH |
| `DEMO_DRAIN_SECONDS` | 5 | 1..30 | | @DomineYH |
| `DEMO_CLEANUP_INTERVAL_SECONDS` | 30 | 5..300 | | @DomineYH |
| `DEMO_MAX_RETIRED_GENERATIONS` | 40 | 1..2000 | | @DomineYH |

범위 출처: §8:197-209, `src/config.py:237-258`, `src/demo/visitors.py:47-57`(workers 포함).
기본값 출처: `src/config.py:121-141`.

용량 검증은 승인값으로 §8:213 교차 검사를 통과해야 한다. 아래 결과 칸도 비어 있다.

| 항목 | 값 | 채우는 사람 | 출처/검증 |
| --- | --- | --- | --- |
| `max_disk ≥ seed + 2 × max_db` 결과 | | @DomineYH | `src/demo/visitors.py:66`; 미달이면 부팅 거절 |
| `seed ≤ max_db` 결과 | | @DomineYH | `src/demo/visitors.py:65`, `src/demo/database.py:265` |
| `absolute ≥ idle` 결과 | | @DomineYH | `src/demo/visitors.py:67` |
| 측정 근거(seed 크기·세대당 증가·root 사용량 측정 방법과 결과) | | @DomineYH | 측정 없이 용량을 보장하지 않는다 |

## 6. rollback 대상·범위·유효 기간

복구 순서(계약 §28:771, #206 초안 `docs/demo/d5-01-operations.md` §8, branch
`feat/206-ops-draft`, 미병합):

1. 신규 start 차단
2. demo 중단
3. 진행 작업 취소/drain/dispose
4. 승인된 이전 동일 tuple로 복구

첫 배포라 이전 정상 demo tuple이 없으면 승인된 중단 상태가 복구 목표다.
normal DB/설정은 바꾸지 않는다. 두 서비스를 자동으로 동시에 갱신하지 않는다.
운영 DB 복원이나 무손실 자동 rollback을 가정하지 않는다.

| 항목 | 값 | 채우는 사람 | 출처/검증 |
| --- | --- | --- | --- |
| 이전 정상 demo tuple(SHA/설정/seed) 또는 `없음` | | @DomineYH | `없음`이면 복구 목표는 승인된 중단 상태 (§28:771) |
| rollback 범위 | | @DomineYH | demo만, normal 불변 (§28:771) |
| rollback 유효 기간 | | @DomineYH | 승인 댓글에 명시 |

## 7. 책임자·승인자·reviewer·증거 위치

| 항목 | 값 | 채우는 사람 | 출처/검증 |
| --- | --- | --- | --- |
| 실행 책임자 | | @DomineYH | 2026-10-18 위임 답변 |
| G6 승인자 | | @DomineYH | 2026-10-18 위임 답변 |
| reviewer 세션 | | @DomineYH | 별도 reviewer worker 세션(실행 세션과 다름) |
| 증거 본문 | | @DomineYH | `docs/demo/d5-02-rehearsal-evidence.md` |
| 증거 디렉터리 | | @DomineYH | `docs/demo/d5-02-evidence/` |

## 8. 승인 댓글 링크·시각

| 항목 | 값 | 채우는 사람 | 출처/검증 |
| --- | --- | --- | --- |
| 승인 댓글 링크 (#207, @DomineYH) | | @DomineYH | 승인은 PR이 아니라 이슈 댓글 |
| 승인 시각 | | @DomineYH | 댓글 시각과 일치 |
