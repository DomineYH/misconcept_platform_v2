# D0-03 데모 공통 계약 v1

## 1. 기준·범위와 읽는 법

현재 편집본은 **`d0-03-v2` 승인 개정안**이다. 승인된 기준은 `d0-03-v1-r3`, 입력 `f6551482fb895da7af90a4bd481c3e25fd9d5c19`, 후보 `d6455d10c2d75acde811f85a53143be086d67545`이며 소비자·독립 재검토 ACCEPT와 maintainer G0 승인이 완료되었다. v1-r3 승인은 그대로 유효하다. v2는 §31의 C14/C27 패치 필요성 정정에 한정하며 정확한 변경 후보에 대한 별도 검토 대체·G0 변경 승인은 §31에 기록한다. 구현 완료를 뜻하지 않는다. 승인 기록은 [G0 기록](d0-03-g0-acceptance.md), 설계 이유는 [ADR-0013](../adr/0013-demo-isolation-and-keyless-provider.md)에 둔다. 용어는 [GLOSSARY](../../GLOSSARY.md)를 따른다.

| 고정 입력 | merge SHA | 파일 버전·최종 파일 commit·blob |
| --- | --- | --- |
| [D0-01 경계 조사](d0-01-boundary-survey.md), #188 / PR #209 | `23c84dc8125534ef3e975eb9206cc3ced755c002` | r2 정정본(정식 버전 필드 없음); commit `1b323d4484145d3d3b87c3a6667b743a5987d395`; blob `050b00e556e3c78ecb77aecc6c64dce6c40cb04e` |
| [D0-02 체험 명세](d0-02-experience-spec.md), #189 / PR #210 | `f6551482fb895da7af90a4bd481c3e25fd9d5c19` | `d0-02-v1`; commit `66cf82a105e03501aa53a371729889dbe614b18e`; blob `e0709908e1b2f5b0c930ca6eb1a2b80b600bebea` |

원 조사 base는 `9ed37942f0cff80b409a67d252a76bce02d78e0b`, 이번 작성 base는 `f6551482fb895da7af90a4bd481c3e25fd9d5c19`, branch는 `docs/190-contract-v1`이다. D0-01 독립 재검토 대상은 `c1c9cc58abb64228f56613c8750e42f8c46018d2`(ACCEPT), D0-02 독립 검토 대상은 `2f2474a78e03bc476c1f29f0424598d2c095ee9c`(ACCEPT), 교육 승인 대상은 `84c2fe4cc9e0af0b1c7b77ff22accc20793d93bb`이다. [#189 교육 승인](https://github.com/DomineYH/misconcept_platform_v2/issues/189#issuecomment-6249579919)은 G0 승인이 아니다.

추가 입력은 제공된 #182, #188–206 issue JSON의 본문·Triage Notes·위임 답변·brief, `REPORT-190.md`, `brief-190.md`, triage의 `ANSWERS-SUMMARY.md`, 저장소 지침·ADR 0001–0012, untracked 계획 `Refactoring_docs/demo/client-demo-implementation-plan.ko.html` §2/4.1/4.2/5/7/9.2/9.3/9.5/10이다. scratchpad와 untracked 계획은 Git 산출물 SHA로 가장하지 않는다. 역사적 ‘산출물 없음’ 문장은 위 병합 입력으로 대체한다.

범위는 방문객별 임시 SQLite, 기존 화면·저장 흐름, 한국어 준비 콘텐츠, 학생봇·멘토·사후 분석, CSV·삭제·reset이다. 비교 실행·채택은 MVP에서 제외하고 직접 API도 차단한다. 제품 코드·migration·테스트는 이 문서 작업의 산출물이 아니다. 이후의 `DemoContext` 등 타입·함수·설정·HTTP 필드는 **신규 설계 인터페이스**다. §26에서 ‘기존’으로 표시한 경로만 입력 SHA에 존재하며, `src/demo/`와 demo router·검사·evidence 경로는 모두 **신규 제안**이다.

공통 표기: `UUID`는 소문자 표준 UUID 문자열, `int`는 bool을 제외한 정수, 시각은 UTC ISO 8601(`Z`)이다. 클라이언트가 보내는 식별자는 권한의 근거가 아니다. 서버가 서명 쿠키·레지스트리·현재 User를 검증한 뒤에만 사용한다. 각 절의 오류는 §11의 응답 규약을 따른다.

## 2. 서버 mode와 설정 (C01)

`APP_MODE: Literal["normal","demo"] = "normal"`을 프로세스 시작 시 한 번 확정하고 변경하지 않는다. 알 수 없는 값·빈 값은 `DEMO_CONFIGURATION_INVALID`로 부팅 실패한다. `ENV`는 기존 development/production 보안 설정으로 분리한다. `ENV=production, APP_MODE=demo`를 허용한다. query/header/cookie/요청 본문/`TESTING`은 mode 선택 입력이 아니다.

모델·연결 선택/가용성/검증 공개 함수에는 keyword-only `app_mode: Literal["normal","demo"]`를 명시적으로 전달한다. 예: 기존 `connection_ready`, `effective_roles`, `model_available`, `validate_model_and_options`에 이 인자를 추가하는 설계다. 기존 normal 호출자도 #195/#198이 명시적으로 연결한다. G2 단위 검사는 서버 mode 인자를 직접 공급하고 G1은 #194가 실제 config를 주입한다. ambient 요청 상태나 테스트 monkeypatch를 mode로 해석하지 않는다.

| 신규 설정 | 타입·기본값·범위 / 검증 | 실패 동작·확정 시점 |
| --- | --- | --- |
| `DEMO_DATA_ROOT` | 절대 디렉터리 경로, 기본 없음; root 자체와 상위 경로에 symlink 없음, 전용 소유권, 운영 mount·repo·일반 DB와 분리 | 누락/불일치 부팅 실패. 실제 경로·mount는 D5-02 승인 입력 |
| `DEMO_ORIGIN`, `NORMAL_ORIGIN` | 각각 단일 origin 문자열, 기본 없음; userinfo/path/query/fragment 없음, 서로 다른 host, production은 HTTPS; development 검사만 명시적 loopback HTTP 허용 | demo에서 둘 다 필수. 실제 host/origin은 D5-02 승인 입력; wildcard/CORS 다중 origin 금지 |
| `DEMO_SESSION_SECRET` | 기본 없음; 최소 32자, 기존 secret의 금지 패턴도 거절; normal 서명 키와 다른 주입 참조 | 누락·재사용 부팅 실패. 운영 secret을 demo에 전달하지 않고 주입 메타데이터로 분리 확인 |
| `DEMO_CSRF_SECRET` | 기본 없음; 같은 강도 조건, demo session 서명 키와도 분리 | 누락·재사용 부팅 실패. 값은 생성/기록하지 않음 |
| `DEMO_PUBLIC_URL` | optional URL, 기본 빈 문자열; production HTTPS, userinfo/query/fragment 없음, path는 `/` 또는 `/demo`; normal origin과 host가 다름 | 비어 있으면 링크 숨김. 잘못된 비어 있지 않은 값은 설정 실패. 실제 URL은 D5-02 승인 입력 |
| `DEMO_WORKERS` | int, 기본/유일 허용값 1 | 1 이외 거절. root의 프로세스 독점 lock도 필요하며 두 번째 프로세스는 실패 |

이름이 `DEMO_`인 설정은 normal에서 제품 mode를 바꾸지 않는다. normal은 링크용 URL/origin 검증만 소비하며 나머지 demo 자원을 열지 않는다. demo는 `.env` 자동 로딩을 끄고 명시적 프로세스 환경만 읽는다. normal의 기존 `.env` 동작은 유지한다. demo에서 `TESTING=true`, `BOOTSTRAP_ADMIN_ON_STARTUP=true`, 비어 있지 않은 `DATABASE_URL`, 외부 제공자 키·마스터 암호화 키/버전·SDK base URL override가 공급되면 부팅 거절한다. 기존 `DATABASE_URL`의 기본 `./dialogue_sim.db`를 demo engine 생성에 사용하지 않는다.

## 3. DemoContext와 DB 선택 (C02)

신규 `DemoContext`는 요청이 보유하는 불변 경계다. 서버 레지스트리의 변경 가능한 방문 상태와 구별한다.

| 필드 | 타입·의미 |
| --- | --- |
| `visitor_id` | 서버 발급 UUID4; 내부 방문 식별, 클라이언트 DB 선택 입력으로 받지 않음 |
| `browser_scope_id` | 서버 발급 128-bit 이상 불투명 문자열; 브라우저의 캐시/URL namespace, 인증 기능 없음 |
| `generation` | int ≥ 1; 방문객별 단조 증가, reset/만료 시 재사용 금지 |
| `auth_epoch` | int ≥ 1; 역할 전환/reset 시 증가, 역할이 같은 재전송은 증가하지 않음 |
| `role`, `user_id` | admin/teacher, 해당 방문객 DB의 실제 User PK(int ≥ 1); 요청이 고르지 않음 |
| `created_at`, `last_activity_at`, `idle_expires_at`, `absolute_expires_at`, `expires_at` | UTC 시각; 마지막 값은 두 만료 시각의 min, 요청 수명 중 snapshot이며 현재 판정은 서버 레지스트리 참조 |
| `factory` | `async_sessionmaker[AsyncSession]`과 동등한 호출 인터페이스; 이 세대의 검증된 engine에 고정, 다른 세대로 자동 교체하지 않음 |
| `lease` | 서버 발급 Lease 참조; §6의 수명/현재성 API에서만 사용 |

기존 `get_db_session()`은 normal에서 기존 factory 의미를 유지하고 demo에서는 서명된 방문 세션을 서버 레지스트리에서 찾는다. 검증 순서는 mode → 쿠키 서명/방문 상태 → URL/header 세대·scope·auth_epoch → ready 확인 및 그 factory의 lease 획득 → lease 아래 현재 User 조회/권한 검사 → 나머지 DB 작업이다. User 부재/권한 거절에서도 finally로 lease를 해제한다(R10). 미방문/잘못된 쿠키는 `DEMO_SESSION_REQUIRED`, 만료는 `DEMO_EXPIRED`, 불일치는 `DEMO_STALE_GENERATION` 또는 `DEMO_STALE_ROLE`이다. 임의 경로/DB URL/visitor_id/user_id 입력은 `DEMO_INVALID_REQUEST`; 운영 factory fallback은 없다. 오류 렌더링·별도 세션·background도 동일하게 적용한다. 상태 동기화/기존 제어 요청 결과 조회만 §9의 DB 없는 별도 절차를 사용한다.

방문객 factory는 SQLite URI `mode=rw`로 이미 검증된 파일만 열며 파일 생성은 staging builder만 허용한다. lease가 유효해도 폐기 세대 factory의 신규 연결은 거절한다. 모든 이미 열린 읽기는 응답 직전 현재성을 재검사한다. 모든 쓰기는 §6의 commit 직렬화로 보호한다. raw factory의 호출만으로 이 보호를 생략하지 않는다.

## 4. 무방문 요청·startup·CLI·health (C03)

| 경계 | demo의 관찰 가능한 결과 / DB 선택 |
| --- | --- |
| `GET /demo`, 정적 파일, 표시 언어 선택 | 방문객 DB 없이 시작 설명과 CSRF bootstrap 가능. 자료·계정 자동 생성 없음 |
| `GET /demo/state` (신규) | 기본은 서명 방문 매핑의 제어 상태만 반환, DB 조회 없음. ready 상태에서 `include_tour=true`와 현재 namespace를 함께 보내는 경우에만 lease를 얻어 자료 본문 없는 투어 projection을 조회. 준비 전/만료/실패는 mapping만 사용 |
| 일반 자료 URL, 로그인 POST | 무방문 자료 요청은 401; demo의 username/password 로그인 POST는 `DEMO_ACTION_FORBIDDEN`, normal 로그인 유지 |
| HTML 오류의 사용자 조회 | 유효한 context가 있을 때만 그 factory로 조회. 미방문·stale·만료·reset이면 user 없는 오류 화면; 전역 factory를 열지 않음 |
| `GET /health` | 쿠키/방문객 DB 생성 없음. 검증된 seed를 read-only로 `SELECT 1`, manifest/root lock/cleanup 상태 검사. ready면 200 `{"status":"healthy","database":"healthy","uptime_seconds":number}`; 실패면 503, status=degraded/database=unhealthy. 경로·visitor 수·비밀 비노출 |
| readiness | 별도 URL을 추가하지 않고 `/health`의 200/503 사용. 수용 상한 도달은 기존 공간에 정상 서비스 가능하면 readiness를 내리지 않음; seed 실패/cleanup hard cap/root 오류는 503 |
| `GET /metrics` | 현재 방문객의 관리자만 허용, 현재 DB 집계. 무방문 401, 교사 403. 다른 방문객/프로세스 전체 DB 합계는 공개하지 않음 |
| startup | 전용 root/단일 worker/설정/manifest/seed 검증 후만 준비. 일반 `init_db`·bootstrap·운영 orphan 검색은 호출하지 않음 |
| shutdown / restart | 신규 admission 차단 → namespace별 취소·bounded drain → lease 0 엔진 dispose. 재시작은 이전 공간을 이어 붙이지 않고 만료시켜 격리 cleanup queue로 보냄. 이전 서명 cookie도 레지스트리 부재로 만료 처리 |
| DB 관리·일반 seed CLI | demo에서 실행 거절(exit 1, `DEMO_ACTION_FORBIDDEN`), 기본 DB 생성/읽기 전. seed 제작은 #197의 서버 전용 build API가 명시적 staging engine으로 정식 installer를 호출할 때만 허용 |
| comparison cleanup CLI | demo에서 dry-run/execute 모두 실행 거절(exit 1), 임의 `--database` 수용 없음. 방문객 폐기는 DB provider/lifecycle이 담당 |
| provider smoke CLI | demo에서는 계획/dry-run/execute 모두 실행 거절(exit 1); `TESTING=true`나 승인 파일로 예외 없음. normal의 기존 명시 승인 규칙 유지 |
| provider 재인증 | 위험 route guard가 비밀번호 parsing/재확인보다 먼저 거절. 방어용 `reauthenticate`도 demo에서 limiter/비밀번호 접근 전 거절; 공유 정수 user ID가 다른 방문객 실패 횟수를 쓰지 않음 |

로그는 code, operation, generation, 실행 correlation, 시간·cleanup 수치만 허용한다. 본문·암호·원문 cookie·키·실제 DB 경로를 기록하지 않는다. 접근 로그 URL에서 scope/query 식별자는 마스킹한다. 전역 한도/cleanup 수치는 서버 운영 로그에서 관측하며 방문객 `/metrics`에는 넣지 않는다.

## 5. DB provider와 seed 복제 (C04)

신규 DB provider의 공개 API는 아래 동작을 고정한다. 이는 Python skeleton이 아니다. `VerifiedVisitor`는 서명 검증 후 서버가 얻은 내부 참조이고 외부 문자열에서 직접 만들 수 없다. `GenerationRef`는 visitor_id/generation/factory/검증 manifest digest의 서버 참조다.

| API | 입력 → 결과 / 오류·경합 |
| --- | --- |
| `create_visit(verified_bootstrap, request_id, role)` | §5.1 reserve_start로 bootstrap당 하나의 방문/작업을 예약 → preparing 202 또는 검증된 GenerationRef(g=1). 같은 ID/다른 body는 충돌; 다른 ID 경합·실패 복구는 §7.1/9.1. 예약 quota에 staging 포함 |
| `resolve_visit(verified_visitor, expected_generation)` | 현재 ready 세대만 반환; lookup은 DB를 만들지 않음. 부재/만료/전환은 §11 오류 |
| `acquire_lease(ref, expected_auth_epoch)` | 전환 lock 밖에서 호출. #191이 lock을 획득하고 현재 ready/generation/auth_epoch/TTL 검사 → 고유 Lease(lease_id:UUID, visitor_id, generation, auth_epoch, released=false) 1개·refcount +1. 폐기 세대 신규 획득 거절 |
| `retain_lease(lease)` | 전환 lock 밖에서 호출하는 wrapper. #191이 해당 lock을 획득하고 handle.retain_lease(lease)에 위임. 이미 handle을 보유하면 wrapper 대신 아래 handle API만 사용 |
| `release_lease(lease)` | refcount를 한 번만 감소; 중복 해제는 no-op, 마지막 해제만 cleanup 가능 통지. 새 세대 생성 기능 없음 |
| `reset_visit(ctx, request_id)` | §5.1 transition/mark_reset으로 §7 전환을 시작/조회해 ResetResult 반환. 같은 request_id는 같은 reset operation. ResetResult는 §9 DemoState의 resetting/ready 응답 또는 §11 오류 envelope |
| `expire_visit(verified_visitor, reason)` | TTL/shutdown/version_replace 중 하나 → generation 폐기·취소·정리 예약; 반복은 no-op |
| `cleanup_retired()` | 검증 root 안의 폐기 세대만 dispose/삭제; 정리 수·대기 수·실패 code 반환, 방문객 factory 반환 금지 |

경로는 root/서버 UUID/세대에서 서버만 조합한다. symlink·`..`·root 밖·일반 DB명/운영 경로·동일 inode 참조는 거절한다. root는 별도 demo marker JSON(`kind="misconcept-demo-root"`, `marker_version=1`, `root_id=UUID`, `app_mode="demo"`)과 소유권·독점 lock을 요구한다. marker 자동 부착으로 기존 임의 디렉터리를 데모 자료로 신뢰하지 않는다. 승인된 신규 빈 root의 초기화만 허용한다.

각 DB에는 신규 `demo_metadata` 단일 row(`id=1`, `kind="demo_seed"|"demo_visitor"`, contract/content/fixture/schema 버전·manifest_sha256, visitor_id nullable, generation nullable)를 둔다. seed는 visitor/generation NULL, 복제 staging은 서버가 실제 visitor/generation을 써서 검증한다. 일반 DB에 marker가 없으면 열어 사용하지 않는다. marker만으로 허구성을 보증하지 않고 아래 검증도 수행한다.

seed는 #197이 정식 `run_all_migrations(db_engine=...)`로 새 staging DB에 허구 자료만 설치한다. 복제 방식은 **SQLite backup API**를 채택한다. live `.db`만 복사하는 방식은 금지한다. source read-only backup → destination 닫기 → `integrity_check=ok`, `foreign_key_check` 빈 결과, migration history/manifest hash/marker, key material·외부 모델 부재, 허구 seed 참조 검사 → 같은 filesystem의 원자적 rename → 레지스트리에 publish 순서다. 실패 staging은 연결을 닫고 검증 root 안에서 정리하며 factory를 공개하지 않는다.

방문객 연결마다 `foreign_keys=ON`, `journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=1000` ms를 적용한다. DB 본체·WAL·SHM·staging을 크기에 합산한다. 삭제는 namespace 취소/drain → lease 0 → 세션/연결 닫기 → engine dispose → 검증 경로의 DB/WAL/SHM 삭제다. 잠김은 대기 queue로 보내며 파일을 가진 동안 공간/디스크 quota를 반환하지 않는다. default DB 재생성은 없다.

### 5.1 방문 상태·전환 lock의 단일 권위 (R1–R2)

**#191의 신규 `src/demo/visitors.py`가 VisitorState와 방문객별 전환 lock의 유일한 소유자**다. database.py(#191)는 복제/engine/물리 정리, lifecycle.py(#192)는 실행 registry·취소/drain을 소유한다. #192/#193/#194/#198/#200은 아래 공개 경계만 사용하고 별도 전환 lock/상태 사본을 만들지 않는다.

`VisitorSnapshot`은 visitor_id/browser_scope_id/state/generation/auth_epoch/role/만료/현재 GenerationRef 또는 None/현재 operation 또는 None/last_error/csrf_revision를 가진 불변 값이다. `ControlContext`는 서명 bootstrap에서 얻은 verified_visitor와 요청의 expected_generation/expected_auth_epoch를 묶은 서버 제어 참조이며 factory/lease/User 권한이 없다. reset_failed의 자기 공간 재시도에만 사용한다. `OperationToken`은 서버 UUID op_id와 visitor_id/예약 generation/attempt_no/operation(start|reset)/request_id의 불변 capability다. 외부 입력에서 만들지 않는다.

| 공개 경계(#191) | 입력·결과·lock 책임 |
| --- | --- |
| `reserve_start(verified_bootstrap, request_id, role)` | bootstrap→visit index와 방문/디스크 예약을 하나의 admission lock 안에서 검사·갱신. leader만 새 OperationToken과 preparing snapshot을 받고 clone을 실행. replay/loser는 §9.1 결과만 받음. 기존 방문이 있으면 admission lock 다음에 그 방문 전환 lock 획득; 역순 금지 |
| `transition(verified_visitor)` | async context manager. 진입 시 방문객 전환 lock을 잡아 `TransitionHandle` 반환, 종료 시 해제. handle은 context 밖 사용 금지, caller가 lock을 직접 해제/재진입하지 않음 |
| `handle.snapshot()`, `handle.check(ctx)` | 불변 snapshot / ready·TTL·scope/generation·auth_epoch·미해제 lease 검사. 오류는 EXPIRED → STALE_GENERATION → 전환 state 오류 → STALE_ROLE 순서. DB lookup 전에도 사용 |
| `handle.retain_lease(lease)` | 이미 보유한 같은 방문 전환 handle에서만 호출. lock 재획득/재진입 없이 미해제 부모 lease와 현재 ready/generation/auth_epoch/TTL을 검사하고 새 lease_id의 child 1개·refcount +1을 원자 반환. 오류는 handle.check와 동일, 실패 시 증가0 |
| `handle.release_lease(lease)` | 보유 handle 안의 해제/등록 실패 복구용. lock 재획득 없이 그 lease_id를 한 번만 해제. 폐기 세대도 허용, 중복은 no-op. lock 밖의 release_lease는 lock을 획득해 이 연산에 위임 |
| `handle.mark_reset(ctx, request_id)` | ready에서는 DemoContext를, reset_failed 재시도에서는 ControlContext의 서버 방문/g/epoch를 검사하고 resetting/g+1/auth_epoch+1/operation을 한 번에 기록 → OperationToken + 폐기 scope. reset_failed의 새 ID retry는 예약 g/epoch 유지·attempt_no+1. 같은 ID는 기존 receipt, 다른 진행 ID는 RESET_IN_PROGRESS |
| `handle.publish(op, candidate_ref)` | op의 visitor/generation/attempt_no가 현재 preparing/resetting과 같고 candidate 검증·TTL이 유효할 때만 factory와 ready를 원자 연결. start는 예약 role, reset은 admin. 완료 receipt/CSRF revision도 함께 확정 → snapshot. expired면 EXPIRED, 오래된 op면 STALE_GENERATION, 검증 불일치 VERSION_MISMATCH; candidate는 정리 대상으로 반환 |
| `handle.fail(op, code)` | 현재 op일 때 factory=None으로 start는 prepare_failed, reset은 reset_failed 및 불변 실패 receipt 기록. 오류 status는 §7.1/11. 오래된 op의 fail은 상태를 덮지 않는 false 반환 |
| `handle.expire(reason)` | TTL/shutdown/version_replace로 generation/auth_epoch 증가·factory 폐기·expired(종료 중 closing) 표시 → 폐기 scope/정리 refs. 반복은 no-op. 준비 중 op도 publish 불가 |
| `handle.change_role(ctx, role, busy)` | check와 현재 User 존재/권한 확인 후 같은 역할은 busy와 무관한 200 no-op. 다른 역할이며 busy면 BUSY; 아니면 같은 lease 아래 대상 User도 확인 후 role/auth_epoch+1/CSRF revision을 원자 갱신. #193이 User 조회와 change_role을 같은 handle 구간에서 수행하며 deleted User는 ROLE_DELETED. busy 값은 §6 busy_locked(handle)에서 얻은 값만 허용 |
| `write_guard(ctx)` | async context manager로 TransitionHandle을 yield한다. ctx의 **generation과 auth_epoch 둘 다** check한 뒤 첫 DML부터 flush/commit 또는 rollback까지 같은 전환 lock 보유. old epoch는 STALE_ROLE, DB 변화 0. #192의 같은 이름 facade는 이 함수를 그대로 위임 |

#193의 create/reset orchestration은 reserve/mark를 먼저 수행한 뒤 **lock 밖에서** #192 cancel_scope/drain, #191 clone/검증을 실행하고 다시 transition 안에서 publish/fail한다. 만료 scheduler #191도 mark 후 #192 취소/drain을 lock 밖에서 요청한다. 아직 publish 전인 clone은 #191의 공개 `cancel_preparation(op)`가 lock 밖에서 중단 신호를 보내고 builder 종료를 기다린다. builder 종료 전 staging 사용 예약/bytes를 반환하거나 파일을 unlink하지 않는다. 취소 불응/늦은 완료도 publish의 op/TTL 검사에 실패하며 #191이 정리한다. 네트워크·stream·drain 대기 동안 handle을 보유하지 않는다. quota 예약/해제는 #191만 수행한다.

공개 API barrier 검사: (a) write_guard(ctx) 획득 → commit → mark_reset이면 old DB commit 1회 뒤 세대 폐기, 새 seed에는 결과 없음. (b) mark_reset → 같은 ctx write_guard면 STALE_GENERATION/rollback, old/new DB write 0. publish가 stale op로 호출되어도 원래 factory가 바뀌지 않는다. 이 검사는 #191/#192 단위 계약, #194 G1·#198 G3의 실제 연결에서 반복한다.

## 6. namespace·lease·현재성과 원자적 저장 (C05–C06)

신규 `ExecutionScope`는 normal에서 `("normal", 0)`, demo에서 `(visitor_id, generation)`이다. `ExecutionKey=(scope, resource_kind, resource_id)`로 resource_kind는 `run|analysis|probe|connection|session|comparison`을 허용한다. ID는 해당 자원의 기존 int/UUID 타입을 유지한다. demo key에 빈 scope/세대를 허용하지 않는다. normal 동작은 보존하되 기존 문자열 dict 접근은 공개 API가 아니다.

#192 lifecycle의 `TaskControl`은 `request_cancel: Callable[[],None]`(동기·멱등·대기 없음), `done:asyncio.Event`(실제 실행과 shield/자식 정리가 모두 끝난 후 한 번 set) 두 필드다. asyncio.Task/Event를 이 프로토콜로 명시적으로 연결하고 task 인자의 타입을 추측하지 않는다. `ExecutionToken`은 token_id:UUID/key/captured_auth_epoch/task_control/registry_lease/completed:bool을 가진 읽기 전용 참조이며 `request_cancel()`과 `wait_done()`를 노출한다. mutable 필드를 소비자가 수정하지 않는다.

| 공개 API(#192) | 입력·결과·경합 의미 |
| --- | --- |
| `register(ctx, kind, id, task:TaskControl)` | ctx에서 scope/epoch/lease 결정. #191 transition 안에서 check와 동일 key 중복 검사 → handle.retain_lease(ctx.lease) → token 등록 순서, ExecutionToken 반환. outer retain_lease를 handle 안에서 호출하지 않음. 중복은 BUSY/추가 lease0. retain 뒤 등록 실패는 같은 handle.release_lease로 되돌려 순증0. consumer는 반환 token을 보관 |
| `lookup(scope, kind, id)` | 해당 token 또는 None. 다른 namespace 검색 없음 |
| `pop(scope, kind, id, token)` | token 일치 AND done.set 이후만 제거/registry lease 1회 release, true 반환. 불일치·이미 제거는 false; 아직 done=false는 BUSY. 폐기 세대도 이 정리는 허용 |
| `busy(scope, kind=None, id=None)` / `busy_locked(handle, scope)` | 미제거 token 또는 실행 child lease 존재 여부. role 변경은 같은 transition의 busy_locked만 사용, 중간 await 없이 change_role 호출 |
| `cancel(scope, kind, id)`, `cancel_scope(scope)` | TaskControl.request_cancel 신호, token 수 반환. done을 대신 set하거나 lease를 미리 해제하지 않음. 부재0, 반복 안전 |
| `drain(scope, timeout_seconds)` | token.done과 registry pop, 요청/자식/shield lease 모두 0을 기다림 → `{drained:bool,remaining:int}`. timeout이면 false, 임의 제거 없음. 완료된 token은 identity를 확인해 pop 가능 |
| `resource_lock(scope, kind, id)` | 동일 자원 reservation 직렬화. lock 순서는 transition → resource → DB. 그 역순/같은 transition 재진입 금지 |
| `assert_current(ctx)`, `write_guard(ctx)` | #191 handle.check/guard facade. ctx에 고정된 generation/auth_epoch/lease를 검사, 최신 cookie로 치환하지 않음 |

normal은 동일 API에 `ctx={app_mode:"normal",scope:("normal",0),auth_epoch:0,lease:None,factory:기존factory}`를 전달한다. retain/release(None)는 no-op, demo 전환 check/lock은 적용하지 않고 기존 normal 자원 lock·권한·원장 의미/오류 코드를 유지하며 normal에 새 DEMO_ 오류를 주입하지 않는다. normal 전용 registry lock은 #192 소유다. demo의 lease=None은 INVALID_REQUEST다.

Event 기반 SSE 예: 기존 active_runs의 cancel Event를 만들고 `TaskControl(request_cancel=cancel_event.set, done=cleanup_done_event)`로 register한다. response 소유자가 ASGI 전송 종료/disconnect 시 producer 취소·shield된 원장/완료 cleanup을 끝내고 finally에서 done.set → token pop을 실행한다. Event.set만으로 drain이 완료되지 않는다. background 예: 생성한 asyncio.Task의 cancel을 request_cancel로 연결하고 task의 finally(자식/shield await 포함)가 끝난 뒤 완료 callback에서 done.set/pop한다. task가 취소 전에 시작되지 못한 경우도 소유자의 완료 callback이 cleanup을 수행한 뒤 같은 순서로 끝낸다.

요청 lease는 #194가 첫 DB 접근 전에 획득·응답 종료/거절에 해제한다. 단, reset 요청 자신의 lease는 §7.2의 조기 이관 순서로 해제한다. register는 registry child lease를 보유하므로 요청이 끝나도 실행 DB는 남는다. registry/자식 작업은 원래 ctx의 lease 필드만 각자 retain한 child lease로 바꾼 불변 child ctx를 사용한다(나머지 factory/g/epoch는 동일). 실행이 chunk/원장 shield/다른 background에 독립 작업을 넘기면 **그 작업 전용 child lease**를 먼저 retain하고 해당 finally에서 release한다. 요청/작업/registry lease는 서로 다른 lease_id이며 각각 한 번 해제한다. disconnect가 registry lease를 먼저 제거하지 않는다. 늦은 token A의 pop은 동일 key의 token B를 지우지 못한다. drain은 request_cancel만 호출한 상태에서 false, 실제 finally/done/pop/모든 child 해제 후에만 true다.

lease 연산의 단일 소유자는 #191이다(S1). handle 안의 caller는 handle.retain_lease/handle.release_lease만 사용하고, 밖의 caller는 outer 함수를 사용한다. #192가 이미 보유한 handle을 숨긴 채 outer 함수의 재진입을 기대하지 않는다. 유효 register 완료는 token 정확히1/추가 lease 정확히1, 중복 register는 token/lease 순증0이며 자기 lock 대기 없이 끝나야 한다. role/reset mark가 먼저면 retain/등록0이고 r2의 STALE_ROLE/STALE_GENERATION 결과를 유지한다.

`cancel_connection`(#198)은 connection_id에 keyword-only scope를 받아 같은 scope만 취소한다. seed 정수 ID/UUID 중복과 normal 동작을 보존한다. register와 role 변경은 같은 #191 전환 lock을 사용한다. 등록 먼저면 다른 역할 전환은 BUSY; 전환 먼저면 old ctx register가 STALE_ROLE이고 token/child lease가 생기지 않는다. 같은 역할 요청은 **current ctx에서만** busy 중에도 200 no-op이며 epoch/CSRF revision/등록 상태 불변이다. 같은 역할이라도 stale epoch는 STALE_ROLE이지 no-op가 아니다.

일반 CRUD는 busy registry에 넣지 않는다. 대신 모든 첫 DML부터 commit까지 write_guard(ctx)를 잡는다. admin CRUD가 guard를 먼저 얻으면 commit 후 teacher 전환, 전환이 먼저면 이미 읽기/권한 검사를 마친 admin 요청도 guard에서 STALE_ROLE·rollback·DB 불변이다. callback 입구 검사만으로 이를 대체하지 않는다. 원장 start/finish·chunk·최종 학생/멘토/분석의 성공/실패/취소 저장과 요청 dependency의 자동 commit에도 같은 규칙이다. 늦은 저장은 원래 factory를 사용하다 거절될 뿐 새/폐기 DB를 재생성하지 않는다.

## 7. reset·만료·종료 상태 전이 (C07)

서버 방문 상태는 `preparing|prepare_failed|ready|resetting|reset_failed|expired|closing`이다. 폐기 DB의 cleanup 상태 `queued|draining|locked|removed`는 별도이며 UI 성공 상태로 쓰지 않는다.

1. #193이 §5.1 handle.mark_reset을 호출해 ready 세대 g에서 reset을 수락하면 #191 전환 lock 안에서 `resetting`, generation=g+1, auth_epoch+1을 기록한다. 새 세대는 아직 factory 없음, old g는 영구 폐기다. 이후 old g의 요청/저장 거절이 즉시 적용되고 #192 namespace 취소는 lock을 놓은 직후 실행한다.
2. §7.2대로 reset 요청의 DB 세션/자기 lease를 먼저 닫고 해제한 뒤 old scope를 취소하고 최대 `DEMO_DRAIN_SECONDS` 동안 나머지 모든 lease를 기다린다. drain 실패는 reset_failed(`DEMO_DRAIN_TIMEOUT`), 새 seed를 연결하지 않는다. 잠김/복제/검증 실패도 reset_failed다.
3. 검증된 새 DB를 원자적으로 g+1에 publish하면 ready. 선택 역할은 admin으로 복귀하며 삭제된 교사도 seed로 복원된다. old DB 정리가 지연되어도 lease 0/dispose가 확인되면 ready 가능하나 cleanup quota는 계속 차지한다.
4. 실패 상태는 old DB로 rollback하지 않는다. 같은 request_id 조회는 같은 실패를 반환한다. 사용자가 새 request_id로 재시도하면 예약한 g+1을 다시 준비한다(추가 generation 증가 없음); 모든 실패 staging은 먼저 회수/계수한다.

동일 reset request_id와 동일 body는 §9.1의 유효 CSRF 조건에서 진행 중 202/완료 후 200/실패 후 같은 오류를 반환하며 generation을 두 번 늘리지 않는다. 다른 reset request_id는 resetting 중 409 `DEMO_RESET_IN_PROGRESS`; 정상 완료 후 old g로 온 요청은 stale다. reset 실패 시 `GET /demo/state`가 알려 준 예약 세대·auth_epoch로만 재시도한다. 이 조회는 DB 연결/역할 복원을 하지 않는다.

TTL 판정은 서버의 monotonic 경과와 UTC 표시에 기반하고 `now >= expires_at`에서 만료한다. 허용된 foreground 자료 조회/조작만 idle 시각을 갱신한다. health/state polling, static, background chunk, 거절 요청은 갱신하지 않는다. 절대 만료는 생성 시각 기준이며 reset/역할 전환으로 연장하지 않는다. 만료가 reset 준비와 경합하면 publish 직전 lock에서 TTL을 다시 검사해 expired가 이긴다. 만료 시 generation을 다시 증가시켜 tombstone을 남기고 새 호출/저장을 막는다. 새 체험은 새로운 visitor/scope로만 시작한다.

세션 종료는 기존 세션의 종료/분석 흐름, 세션 삭제는 기존 soft delete다. 어느 것도 방문객 generation을 바꾸지 않는다. shutdown은 closing으로 신규 작업을 차단한다. 재시작 시 전용 root에서 검증한 이전 세대만 orphan 정리하며 임의 파일은 격리·오류 처리하고 readiness를 내린다. cleanup은 §8 주기로 재시도하고 hard cap 초과 시 start/reset을 503으로 막으며 기존 세대의 안전한 조회/해제는 계속 허용한다.

### 7.1 첫 준비의 실패·복구와 bootstrap 경합 (R4–R5)

첫 reserve_start는 bootstrap_nonce→단일 visitor_id/OperationToken을 **방문객 quota 예약과 원자적으로** 연결한다. g=1/epoch=1/preparing/factory=None, 요청 role을 고정한다. #191 admission lock의 첫 승자만 clone하고, 같은 bootstrap의 다른 request_id/다른 role은 preparing 동안 409 `DEMO_START_IN_PROGRESS`, Set-Cookie 없음이다. 같은 ID/다른 body는 409 REQUEST_CONFLICT다. 다른 ID가 먼저 완료 응답을 받더라도 방문 공간/role을 새로 만들거나 교체하지 않는다. ready 뒤 새 start ID는 200 현재 공간/현재 role을 반환하며 요청 role로 전환하지 않는다.

`preparing→ready`는 publish만 가능하다. 검증 실패/복제 오류는 `prepare_failed` + `DEMO_PREPARE_FAILED` 503, 디스크 초과는 STORAGE_LIMIT 507, 잠김은 DB_LOCKED 503, 버전 오류는 VERSION_MISMATCH 503으로 fail한다. `/demo/state`는 200 prepare_failed/factory 없음/role=null/next_url=null/last_error를 반환한다. 실패 전 어떤 자원 URL도 사용할 수 없고 clone 완료 전에 성공 응답을 보내지 않는다. same-ID replay는 같은 실패만 반환하며 clone을 다시 하지 않는다. state 확인 뒤 **새 request_id의 start**는 같은 visitor/g=1/epoch=1에서 attempt_no+1로 준비를 재시도한다. 이전 실패 op의 늦은 publish/fail은 무효다.

prepare_failed에서도 방문 예약 1개는 원래 expires_at까지 유지하고 request receipt는 원래 absolute TTL까지 보관한다. 실패/retry가 idle/absolute deadline을 늘리지 않는다. 파일 bytes/실제 staging 예약은 close/dispose/unlink 후에만 반환한다. 새 clone 예약과 old 미정리 staging의 합이 quota를 넘으면 새 retry는 STORAGE_LIMIT/CLEANUP_BACKLOG이며 실패 상태를 유지한다. preparing/prepare_failed의 TTL 만료는 expire로 generation/epoch 증가, op 취소·폐기·410 EXPIRED다. 방문객 slot은 tombstone과 실제 파일/lease 정리 완료 후 반환하되 익명 bootstrap의 과거 receipt는 원래 absolute 만료까지만 남긴다. TTL 뒤 명시적 새 start는 새 visitor/scope를 예약하고 과거 op는 재실행하지 않는다.

반복 GET /demo는 유효한 bootstrap_nonce를 보존한다. bootstrap을 이미 가진 탭에 새 nonce를 발급하지 않는다. 세션 cookie는 §10의 **안정된 bootstrap 참조**이고 실제 visitor/role/g/epoch는 서버 mapping이므로, start 승패·응답 역순으로 cookie가 다른 공간/role을 가리킬 수 없다. cookie 없는 첫 GET은 익명 참조만 만들고, POST는 그 요청의 cookie/CSRF에 결합된 bootstrap으로만 실행한다. cookie가 없는 동시 GET 자체를 IP로 하나의 신원으로 묶지 않는다. 이후 공유 cookie가 채택한 bootstrap 이외의 오래된 페이지 token은 CSRF 거절/안전 GET 재동기화하며 다른 bootstrap의 공간을 합치지 않는다.

관찰 검사(#191/#193, 실제 middleware #194/#203): 동일 bootstrap의 request X(teacher)/Y(admin)를 barrier로 동시에 보내면 leader 하나/방문 예약1/clone1, loser409 START_IN_PROGRESS·cookie 변경0, 최종 role은 leader의 것. 실패-before-publication이면 ready 자료0, 같은 ID로 clone 증가0, 새 ID retry 성공 시 같은 visitor/g=1. publish 뒤 HTTP 응답을 잃어도 state GET/같은 ID replay로 같은 공간을 얻고 clone/slot 증가0이다.

### 7.2 reset 요청의 DB·lease 조기 이관 (S2)

**조기 해제 방식(a)을 채택한다.** #193은 ready DemoContext로 권한 검사와 mark_reset을 마친 직후, 같은 handle 구간에서 다음 await 전에 #194의 신규 동기 공개 `handoff_request_db(ctx)`로 요청 DB 자원을 handoff 상태로 표시하고 OperationToken에 결합한 orchestration Task를 소유·보관한다. 이 이관은 성공한 mark의 필수 후처리여서 요청 취소/연결 단절로 생략하지 않는다. 전환 lock을 놓은 뒤 그 Task의 첫 정리는 #194의 신규 async 공개 `release_request_db(ctx)`다. 이 함수는 전환 lock 밖에서 열려 있는 요청 transaction을 rollback(새 commit 없음), AsyncSession.close 완료, 요청 자신의 release_lease 순서로 처리한다. handoff 후에는 old ctx/factory로 읽기·User 조회·쓰기·자동 commit을 하지 않는다.

#194의 dependencies.py가 handoff 표식과 release_request_db의 단독 소유자다. 요청 dependency의 최종 정리도 같은 idempotent 함수를 호출하고, 요청별 정리 직렬화로 조기/최종 정리가 경합해도 session close와 lease 해제가 한 번만 완료되게 한다. handoff 표식이 있으면 yield 뒤 자동 commit은 건너뛴다. session close가 실패하면 lease를 해제된 것으로 표시하지 않고 reset을 안전한 DB_LOCKED/RESET_FAILED로 실패시킨다. 별도 세션/작업의 lease를 대신 해제하지 않는다.

자기 세션/lease 정리 완료 뒤 #193 Task는 **OperationToken과 폐기 scope만** 사용해 cancel/drain/clone/publish 또는 fail을 수행한다. 새 작업 lease로 reset 요청 lease를 대체하거나, 자기 lease를 drain 집계에서 몰래 제외하지 않는다. 유효한 token으로 공개 transition/publish/fail에 들어가는 것은 old DemoContext 재사용이 아니다. 살아 있는 최초 caller는 shield로 이 Task의 결과를 기다려 성공 시 200 ready(실패 시 해당 오류)를 받을 수 있다. 진행 중 동일 POST/조회는 기존 202, 응답 예산상 최초 요청을 202로 끝내도 같은 소유 Task가 계속한다. 따라서 §9/9.1의 200/202 계약은 유지된다.

연결 단절은 관찰자만 끝내며 mark 뒤의 Task를 취소하지 않는다. #193이 Task 참조와 결과/실패 receipt를 끝까지 보관하고 예외를 회수해 handle.fail에 연결한다. 이후 같은 ID/state GET은 그 결과를 조회한다. shutdown에 의한 Task 취소/실패도 #193이 취소 정리·clone 중단을 수행하고 아직 유효한 op를 fail(이미 expired/closing이면 덮어쓰기 없음)한다. 요청 최종 정리의 중복 release는 no-op다.

관찰 기대값(#191/#192 단위, #193/#194 연결, #203/#204 검증): 다른 작업 없는 reset은 자기 lease 때문에 timeout하지 않고 old 요청 lease0 뒤 drain=true/ready가 된다. **별도의** live 요청/child lease가 남으면 설정 시간 뒤 DRAIN_TIMEOUT이고 그 파일을 dispose/unlink하지 않는다. 그 lease의 실제 해제 전에는 삭제가 불가능하다. mark 후 caller가 끊겨도 조기 정리와 같은 reset 결과가 완료되며, 재조회로 확인하고 generation을 추가 증가시키지 않는다.

## 8. 상한·TTL와 운영 승인 입력 (C08)

아래는 **계약 기반 구현/단위 검사 기본값**이다. idle 30분/absolute 2시간은 계획의 제안을 명시적으로 채택한다. 5분 여정과 재열람 여유를 주면서 임시 저장을 제한하려는 선택이다. 아래 quota·용량·최종 TTL의 실제 배포값은 모두 **D5-02 approval input**이며 기본값만으로 운영 용량을 승인하지 않는다. 범위 밖·bool/소수/음수·무제한 sentinel은 `DEMO_CONFIGURATION_INVALID`다.

| 신규 설정 | 단위·기본값·허용 범위 | 초과/해제 책임 |
| --- | --- | --- |
| `DEMO_IDLE_TTL_SECONDS` | 초 1800, 300..7200 | 만료 410; #191/#192 정리 |
| `DEMO_ABSOLUTE_TTL_SECONDS` | 초 7200, 1800..28800, idle 이상 | 만료 410; reset 연장 없음 |
| `DEMO_MAX_VISITORS` | 공간 20, 1..1000 | preparing/prepare_failed/ready/resetting/reset_failed/미정리 expired를 합산; 503 CAPACITY, 물리 정리 후 반환 |
| `DEMO_MAX_DB_BYTES` | 세대당 bytes 33554432, 1048576..268435456 | staging+WAL/SHM 포함, seed 크기 이상 필수; 쓰기 admission 및 commit 전 검사, 507 STORAGE_LIMIT |
| `DEMO_MAX_DISK_BYTES` | root 총 bytes 1073741824, 67108864..10737418240 | seed+모든 세대+staging+예약 복제 여유 합산; 507, 실제 unlink 후 반환 |
| `DEMO_MAX_TURNS` | 세션당 예약 교사 턴 10, 2..100 | 미지원/실패 예약 포함; retry는 같은 턴, 새 턴 거절 429; 세션 삭제로 누적 공간 할당량을 되돌리지 않음 |
| `DEMO_MAX_SESSIONS` | 공간 누적 10, 2..100 | seed 포함, 새 세션 429; reset 때 복원 |
| `DEMO_MAX_USERS` / `DEMO_MAX_GROUPS` / `DEMO_MAX_SCENARIOS` / `DEMO_MAX_CONTRIBUTORS` | 현재 row 수 각각 10/5/5/5, 범위 2..100 / 1..20 / 1..20 / 1..20 | seed 포함; DB transaction에서 검사, 초과 429; 삭제 commit 후 반환 |
| `DEMO_MAX_UPLOAD_BYTES` / `DEMO_MAX_UPLOAD_ROWS` | bytes 65536(1024..1048576), 행 8(1..98) | 읽는 도중 바이트 차단 413; preview/등록 모두 검사, DB user 잔여 quota와 min |
| `DEMO_MAX_ACTIVE_GLOBAL` / `DEMO_MAX_ACTIVE_VISITOR` | 실행 slot 8(2..64) / 2(1..4), visitor ≤ global | 한도 `call_limit_reached` 429, queue 없음; task/lease 종료 후 반환 |
| `DEMO_REQUESTS_PER_MINUTE` / `DEMO_STARTS_PER_IP_PER_MINUTE` | 60(10..600) / 10(1..60), 고정 60초 window | 방문객 요청과 공유 IP의 start만 각각 429; Retry-After는 남은 window 초; #194. 신뢰 프록시만 IP 해석 |
| `DEMO_DRAIN_SECONDS` / `DEMO_CLEANUP_INTERVAL_SECONDS` | 초 5(1..30) / 30(5..300) | drain timeout 503, cleanup 다음 tick 재시도; #192/#191 |
| `DEMO_MAX_RETIRED_GENERATIONS` | root의 대기 세대 40, 1..2000 | 상한 도달 503 CLEANUP_BACKLOG; 해제 없는 삭제 금지 |

schema의 방문객 `AppSetting.limits`에는 기존 total/openai/anthropic/google/admin을 유지하고 `demo: int >= 2`를 추가한다(#195). demo seed는 total=2, demo=2, admin=1, 기존 외부 필드=2를 저장한다. 실제 admission은 전역 active_calls 상한, visitor 상한, 해당 DB total/demo/admin 한도를 **각각** 만족해야 한다. 역할별 timeout은 기존 AppSetting을 따르고 demo seed의 connect/first-output/total은 각각 2/5/15초(student/mentor), analysis_total=30초, model_list_total=5초다. 외부 catalog는 이 값과 무관하게 거절한다. demo AI 설정 POST는 읽기 전용으로 막으므로 다른 방문객/전역 한도를 바꿀 수 없다. 테스트에서 DB 설정을 직접 바꾸어도 서버 hard cap보다 넓어지지 않는다.

설정 교차 검증은 max disk가 seed bytes + 2 × max DB bytes 이상인지, seed가 max DB bytes 안에 드는지 확인하고 미달이면 부팅을 거절한다. 물리 디스크 부족/SQLite FULL도 507로 정규화해 rollback한다. DB 크기 hard limit은 main DB page 상한과 WAL 증가 감시·transaction 전후 예약을 함께 사용하고 모든 할당에 root budget을 예약한다. 백업 중 새 방문객을 받아 두 복제분을 초과 할당하지 않는다. 이 제한은 공유 IP를 방문객 신원으로 취급하지 않는다.

## 9. 시작·역할·reset HTTP (C09)

신규 demo router는 `APP_MODE=demo`에서만 등록한다. normal의 아래 경로는 404다. mutation은 JSON 또는 기존 form POST를 지원하되 동일 필드 의미, extra field 거절, CSRF 필수다. UUID request_id는 mutation마다 한 번 발급하며 재전송은 그대로 쓴다.

| method/path | 입력 | 성공 / 중복·실패 |
| --- | --- | --- |
| `GET /demo` | 없음 | 200 설명/역할 선택, 익명 bootstrap 및 CSRF cookie. DB 생성 없음 |
| `POST /demo/start` | `request_id:UUID`, `role:admin\|teacher` | 신규 201 ready 응답, 동일 요청 replay 200/준비 중 202. 기존 유효 방문이면 현재 공간 반환하고 역할 변경은 별도 `/role` 사용. 만료 후 새 명시 start만 새 공간 생성 |
| `POST /demo/role` | `request_id`, `role`, `expected_generation:int>=1`, `expected_auth_epoch:int>=1` | 200 현재 DB의 정상 User 선택, auth_epoch+1, 서버 CSRF revision 갱신 후 current-state GET으로 cookie 동기화. current epoch의 같은 역할은 busy여도 no-op, stale epoch는 거절. active 작업 있으면 409 BUSY, 삭제 교사는 409 ROLE_DELETED, 암묵 재생성 없음 |
| `POST /demo/reset` | `request_id`, `expected_generation`, `expected_auth_epoch`, `confirm:true` | 두 역할에서 허용, 202 resetting 또는 200 ready; §7 멱등/실패 계약. body의 role/user/path는 거절 |
| `GET /demo/state` | optional `request_id:UUID` (자신의 mutation 조회), `include_tour:bool=false` | 상태 조회 200, 만료는 410 envelope; 알 수 없는 요청 ID는 404. 안정된 bootstrap cookie로 현재 mapping 동기화; 과거 namespace의 자료 접근/권한 부여는 안 함 |

ready/state 응답의 신규 공통 객체 `DemoState`는 `contract_version`, `content_version`, `fixture_version`, `state`, `browser_scope_id`, `generation`, `auth_epoch`, `role:admin|teacher|null`, `expires_at`, `idle_ttl_seconds`, `absolute_ttl_seconds`, `request_id:UUID|null`, `csrf_cookie_name`, `csrf_header_name`, `next_url:string|null`, `active:bool`, `provenance`, `last_error:null|code`, `tour_facts:null|TourFacts`을 가진다. tour_facts는 ready/current context의 include_tour=true 조회에서만 채우며 제어 상태 응답에는 null이다. 최종 tour_steps는 §24에서 #199가 계산한다. preparing/실패의 start 대상에는 공개 role=null이지만 내부 예약 role은 유지한다. 준비/실패 상태의 role/next_url은 null, 만료 시에는 허용된 최소 상태와 복구 코드만 보낸다. factory/path/visitor_id/User ID는 보내지 않는다. 성공 next_url은 서버가 만든 현재 세대 URL이고 임의 return URL은 받지 않는다.

bootstrap은 별도 익명 서명 세션 nonce이며 자료 접근 권한이 없다. 방문 예약의 unique key는 bootstrap_nonce이고 그 안의 operation receipt key는 request_id여서 쿠키 응답 유실 후 replay도 새 공간을 만들지 않는다. 이 예약과 mutation 결과는 방문 수명(익명은 absolute 기본 TTL) 동안 보관한다. 방문객당 서로 다른 request_id는 최대 256개, bootstrap 예약은 방문 공간 quota 안에서만 허용한다. 한도 도달 시 새 ID만 LIMIT_REACHED로 거절하고 기존 결과를 임의 eviction해 중복 실행하지 않는다. 유효 방문의 request_id는 `(visitor_id,operation,request_id)`로 unique, 같은 ID/다른 body는 409 REQUEST_CONFLICT다. CSRF·방문 서명 검증은 replay보다 먼저이며 완료 후 회전 전 토큰을 허용하지 않는다. preparing/resetting 중 아직 회전하지 않은 유효 토큰의 replay는 아래 표대로 허용한다. 제어 mutation의 소유된 request_id 결과 조회는 그 뒤 expected_generation/epoch 거절보다 먼저 수행한다. 이 예외는 캐시된 제어 결과만 반환하며 stale 자료 조회나 새 작업을 허용하지 않는다. 만료/재시작 후 과거 mutation은 재실행하지 않고 새 start를 안내한다.

### 9.1 제어 요청 replay·CSRF·Set-Cookie 행렬 (R6)

#193 router와 #194 middleware는 이 표를 같은 우선순위로 소비한다. `N`은 **두 cookie 모두 Set-Cookie 없음**(일반 SessionMiddleware의 자동 재발급도 억제), `C`는 현재 bootstrap 참조 유지/서명 갱신 및 **현재 서버 CSRF revision의** cookie만 발급한다. 표의 POST는 올바른 Origin·서명·현재 유효 double-submit token을 전제로 하고 유효 서명 요청 안에서는 누락/오류 CSRF 행이 항상 replay/namespace 판정보다 우선한다. 위조/부재 서명은 먼저401 SESSION_REQUIRED다. GET에는 CSRF header를 요구하지 않는다. 아래 preparing는 start, resetting/reset_failed는 reset의 자기 operation을 가리킨다. prepare_failed는 실패 status가 §7.1이라는 차이 외에는 reset_failed 열과 같은 규칙이다.

| 요청 | preparing | resetting | reset_failed | 완료 후 ready | Set-Cookie |
| --- | --- | --- | --- | --- | --- |
| 수락된 동일 ID/동일 body POST replay | 202 preparing, clone 추가0 | 202 resetting, 세대 증가0 | 저장된 실패 status/code, 새 준비0 | 저장된 결과 receipt(성공200/실패 원래 status), 현재와 다르면 historical=true | N |
| 동일 ID/다른 body POST | 409 REQUEST_CONFLICT | 409 REQUEST_CONFLICT | 409 REQUEST_CONFLICT | 409 REQUEST_CONFLICT | N |
| 새 ID POST | start409 START_IN_PROGRESS; role/reset401 SESSION_REQUIRED | start/role/reset409 RESET_IN_PROGRESS | reset은 state의 예약 g/epoch로 202(빠른 성공200), start/role503 RESET_FAILED | start200 기존 공간; role200/동일역할 no-op 또는 BUSY; reset202/200. stale 신규 요청은409 STALE_GENERATION/STALE_ROLE | N |
| POST의 CSRF 누락/오류/회전 전 토큰 | 403 CSRF_INVALID | 403 CSRF_INVALID | 403 CSRF_INVALID | 403 CSRF_INVALID, replay 조회도 하지 않음 | N |
| `GET /demo/state`(request_id 없음) | 200 현재 preparing | 200 현재 resetting | 200 현재 reset_failed/last_error | 200 현재 ready | C |
| `GET /demo/state?request_id=<수락된 ID>` | 해당 현재 operation이면202 | 해당 현재 operation이면202; 과거 완료 ID면200 historical | 현재 실패 ID면 저장된 실패 status/code; 과거 완료면200 historical | 성공200/실패 원래 오류 status의 receipt, historical 여부 표시 | N |

prepare_failed에서 새 ID start만 같은 방문을 재준비하고 role/reset은503 PREPARE_FAILED다. 미방문/위조 서명은401 SESSION_REQUIRED, 알 수 없거나 다른 bootstrap의 request_id는404 RESOURCE_NOT_FOUND, expired는410 EXPIRED, 어느 경우도 N이다. 짧은 코드 표기는 모두 `DEMO_` prefix를 붙인다. 새 ID의 한도/설정/검증 오류는 §11이 우선하고 clone/token을 만들지 않는다.

저장된 결과는 `ControlReceipt={operation:"start"|"role"|"reset",request_id:UUID,result:DemoState|E,historical:bool,current:{state,generation,auth_epoch,role,browser_scope_id}}`라는 응답 형태로 반환한다. historical은 결과의 scope/g/epoch가 현재와 다를 때 true, next_url은 null로 강제한다. 현재 preparing/resetting의 replay는 DemoState + request_id 그대로다. 과거 실패 receipt는 저장된 오류 status를 유지한다. 브라우저는 receipt.result를 현재 상태로 적용하지 않고 current-state GET으로 동기화한다. **POST replay와 historical-result GET은 어떤 세대의 cookie도 발급하지 않는다.** 예: reset X 성공 뒤 reset Y 성공 후 X를 다시 조회하면 X 결과를 읽을 수 있지만 Y의 mapping/cookie/role은 변하지 않는다.

start/role/reset의 최초 완료 응답도 N이며 클라이언트는 완료/실패/결과 불명확 시 current-state GET(C)을 수행한 뒤 다음 mutation을 보낸다. 전환 시작 시 기존 CSRF revision은 그대로 유효하므로 preparing/resetting 동일 POST의202가 가능하다. publish/실제 role 변경 성공 시 서버 revision을 올리고 이후 기존 토큰은403이다. CSRF403은 작업을 다시 실행하는 신호가 아니며 안전 GET→같은 request_id 결과 확인 순서로 복구한다. #203은 응답 유실/중복 진행 POST/후속 reset 후 과거 결과 조회를 이 표대로 검사한다.

## 10. 쿠키·CSRF와 세대 전달 (C10)

| cookie | 속성·수명·내용 |
| --- | --- |
| `demo_session_id` | host-only(Domain 없음), Path=/, HttpOnly, SameSite=Lax, production Secure, Max-Age=28800. normal `session_id`와 이름·서명키 분리. 서명 payload는 안정된 bootstrap_nonce 참조만. visitor/generation/auth_epoch/role은 서버 mapping으로 결정하며 cookie 값에 넣지 않음; 비밀/DB 경로 없음 |
| `demo_csrftoken` | host-only, Path=/, HttpOnly=false(JS reader 필요), SameSite=Lax, production Secure. **session cookie: Max-Age/Expires 없음**, 브라우저 세션 수명. 8시간을 복사하지 않음 |

위 이름은 고정값이며 임의 cookie 이름 설정을 추가하지 않는다. normal cookie 이름·정책은 보존한다. cookie의 8시간은 DB TTL이 아니며 서버 만료가 우선한다. development의 명시적 loopback 검사만 Secure=false; demo에서 보안 middleware를 끄는 `TESTING`은 금지한다.

`GET /demo`와 current-state GET만 signed double-submit CSRF를 발급한다. token은 bootstrap_nonce와 서버 csrf_revision에 결합하며 준비 중에는 이전 유효 revision을 유지한다. mutation은 `x-csrf-token`과 cookie의 일치/서명/서버 revision을 검증하고 Origin이 DEMO_ORIGIN과 같은지 검사한다(Origin 없는 form은 동일 origin Referer 필수, 둘 다 없으면 거절). start/role/reset/삭제는 `/login` 예외가 아니다. GET /demo는 유효 bootstrap을 재사용하고 현재 revision만 동기화한다. role/reset 성공 뒤 current-state GET에서 session 서명을 갱신하고 새 CSRF revision을 cookie로 전달한다. POST/historical GET은 §9.1대로 Set-Cookie를 억제한다.

서버 visitor/권한/세대는 cookie 응답 순서로 rollback되지 않는다. 같은 bootstrap의 늦은 start/loser/과거 receipt 응답에는 Set-Cookie가 없어 다른 탭을 덮을 수 없다. 서로 다른 revision의 current-state GET 응답 자체가 역순 도착해 오래된 CSRF cookie가 남는 경우에는 다음 mutation403·작업0이고, 최신 current-state GET으로 복구한다. 오래된 token을 받아들이거나 세대/역할을 되돌리지 않는다. #199/#201은 제어 완료 뒤 state 응답의 g/epoch가 마지막 확인 값보다 작으면 JSON을 적용하지 않는다. CSRF 값은 응답 JSON/event/storage에 저장하지 않고 요청마다 cookie를 읽는다.

template 공통 `demo_config`의 `csrf_cookie_name="demo_csrftoken"`, `csrf_header_name="x-csrf-token"`을 JSON 안전 인코딩해 전달한다. #199가 template/inline reader, #201이 기존 JS reader에 적용한다. csrf 오류는 본문을 재전송하지 않고 안전한 state GET/토큰 갱신 후 사용자가 다시 실행하게 한다.

자료 URL에는 `ds=browser_scope_id&dg=generation&da=auth_epoch`를 넣는다. AJAX/HTMX/fetch는 `X-Demo-Scope`, `X-Demo-Generation`, `X-Demo-Auth-Epoch`를 보낸다. form은 `_demo_scope`, `_demo_generation`, `_demo_auth_epoch` hidden 값을 보낸다. 서버는 서명 bootstrap의 현재 mapping과 교차 검증하고 중복 전달 값이 다르면 409로 거절한다. 기존 route body parsing 전 #194가 이 메타데이터를 검증하고 메타데이터 필드만 분리해 기존 strict body schema에 전달하지 않는다. 자료 ID가 있는 URL에 namespace가 없으면 409 STALE_GENERATION이며 새 공간에 자동 연결하지 않는다. 새 자료 없는 진입/목록 URL만 현재 상태에서 링크를 만들 수 있다.

## 11. 오류·HTTP·API·SSE 대응표 (C11)

demo JSON 오류 `E(code)`는 `{"detail":{"code":code,"retryable":bool,"recovery":key,"generation":int|null,"auth_epoch":int|null}}`다. 유효한 같은 방문 매핑에서만 현재 세대를 공개하고, 아니면 null이다. `Retry-After`는 초 정수이며 retryable은 사용자 확인 뒤 재시도 가능 의미다. 임의 자동 재전송 허가는 아니다. HTML도 같은 HTTP status/안내 key를 사용하고 DB 없는 오류를 렌더링한다. 모든 demo 상태·자료·오류 응답은 `Cache-Control: no-store`다.

이미 SSE 200을 보냈으면 HTTP status를 바꾸지 않는다. 기존 `run.failed|run.cancelled|run.interrupted` terminal frame의 `code`, `retryable`, `message`에 `recovery`, `generation`, `auth_epoch`, `browser_scope_id`를 더한다. `S=failed/cancelled/interrupted`는 해당 `run.S` event를 뜻한다. CallEvent/attempt 원장의 timed_out/refused와 GenerationRun의 상태를 혼동하지 않는다. timeout/refused의 공개 run 상태는 failed이며 새로운 run.timed_out/run.refused event를 추가하지 않는다. 기존 `run_id`, `turn_id`, `seq`를 유지한다. 새 demo 성공 terminal을 만들지 않는다. 모든 frame 전송 직전 현재성을 재검사한다. invalid/revoked context에서는 본문 delta·message를 추가 전송하지 않고 본문 없는 terminal만 보낸다. source namespace는 요청에 고정하며 오류의 현재 세대 안내가 있더라도 그 요청의 결과를 새 세대 자료로 적용하지 않는다.

| code | HTTP status | API/SSE payload | retryable | UI recovery hint(key) | raised by | consumed by |
| --- | --- | --- | --- | --- | --- | --- |
| `DEMO_SESSION_REQUIRED` | 401 | E / S=cancelled | false | `demo.start_required` | resolver/session | #193/#199/#201/#203 |
| `DEMO_EXPIRED` | 410 | E / S=cancelled | false | `demo.expired` | TTL/lifecycle | #193/#199/#201/#204 |
| `DEMO_STALE_GENERATION` | 409 | E / S=cancelled | false | `demo.stale_generation` | resolver/write_guard | #198/#199/#201/#203 |
| `DEMO_STALE_ROLE` | 409 | E / S=cancelled | false | `demo.stale_role` | auth_epoch guard | #199/#201/#203 |
| `DEMO_START_IN_PROGRESS` | 409 | E | false | `demo.preparing` | #191 reserve_start | #193/#199/#201/#203 |
| `DEMO_PREPARE_FAILED` | 503 | E | true | `demo.prepare_failed` | #191 fail(start) | #193/#199/#201/#203 |
| `DEMO_RESET_IN_PROGRESS` | 409 | E / S=cancelled | false | `demo.resetting` | provider/session | #193/#199/#201 |
| `DEMO_RESET_FAILED` | 503 | E / S=failed | true | `demo.reset_failed` | seed/reset orchestration | #193/#199/#201/#203 |
| `DEMO_DRAIN_TIMEOUT` | 503 | E / S=cancelled | true | `demo.reset_failed` | drain | #193/#199/#201/#203 |
| `DEMO_DB_LOCKED` | 503 | E / S=failed; Retry-After=1 | true | `demo.retry_later` | SQLite/cleanup | #193/#198/#201/#203 |
| `DEMO_ROLE_DELETED` | 409 | E / S=cancelled | false | `demo.role_deleted` | role/User lookup | #193/#199/#201/#204 |
| `DEMO_BUSY` | 409 | E / S=failed | true | `demo.wait_or_stop` | lifecycle/role | #193/#199/#201 |
| `DEMO_ACTION_FORBIDDEN` | 403 | E / S=failed | false | `demo.action_forbidden` | route/CLI/reauth guard | #200/#201/#203/#204 |
| `DEMO_CSRF_INVALID` | 403 | E; stream 시작 전에만 | false | `demo.csrf_refresh` | middleware | #193/#199/#201/#203 |
| `DEMO_LIMIT_REACHED` | 429 | E / S=failed | false | `demo.limit_reached` | per-space quota | #198/#200/#201/#203 |
| `DEMO_RATE_LIMITED` | 429 | E; Retry-After=window 잔여 초 | true | `demo.retry_later` | request/IP limiter | #199/#201/#203 |
| `DEMO_CAPACITY` | 503 | E; Retry-After=30 | true | `demo.capacity` | 방문 공간 capacity | #193/#199/#201/#206 |
| `DEMO_STORAGE_LIMIT` | 507 | E / S=failed | false | `demo.storage_limit` | DB/root quota | #193/#198/#200/#203 |
| `DEMO_UPLOAD_TOO_LARGE` | 413 | E | false | `demo.upload_limit` | bulk guard | #200/#201/#203 |
| `DEMO_CLEANUP_BACKLOG` | 503 | E; Retry-After=cleanup interval | true | `demo.retry_later` | cleanup admission | #193/#199/#206 |
| `DEMO_VERSION_MISMATCH` | 503 | E / S=failed | false | `demo.unavailable` | manifest/model validator | #195–198/#201/#206 |
| `DEMO_CONFIGURATION_INVALID` | 503(startup/CLI는 exit 1) | E / S=failed; 안전한 code만 | false | `demo.unavailable` | config/root/key scan | #194/#195/#198/#206 |
| `DEMO_UNSUPPORTED_CONFIGURATION` | 422 | E, 호출/턴 없음 | false | `demo.supported_only` | snapshot/fixture selector | #195/#198/#199/#201 |
| `DEMO_INVALID_REQUEST` | 422 | E, 필드 이름만 | false | `demo.check_input` | request validator | #193/#200/#201 |
| `DEMO_REQUEST_CONFLICT` | 409 | E | false | `demo.refresh_state` | idempotency reservation | #193/#198/#201 |
| `DEMO_RESOURCE_NOT_FOUND` | 404 | E, 본문 없음 | false | `demo.not_found` | current DB/ownership | #198/#200/#201/#203 |
| `configuration_unavailable` | 422 | E / S=failed | false | `demo.unavailable` | common admission | #198/#200/#201 |
| `invalid_output`, `invalid_json`, `invalid_reference`, `empty_response` | 422 | E / S=failed | false | `demo.output_failed` | 기존 출력 검증 | #196/#198/#201/#203 |
| `timeout_connect`, `timeout_first_output`, `timeout_total` | 504 | E / S=failed(아래 SSE code 대응) | true | `demo.retry_later` | deadline/실패 fixture | #196/#198/#201/#203 |
| `interrupted`, `disconnected`, `session_ended` | 409 | E / 기존 S=interrupted/failed/cancelled(아래 대응) | false | `demo.stopped` | 취소/종료 | #192/#198/#201 |
| `refused` | 422 | E / S=failed | false | `demo.output_refused` | failure fixture | #196/#198/#201 |
| `call_limit_reached`, `rate_limited`, `transient` | 429 / 429 / 503 | E / S=failed; rate/transient는 Retry-After | true | `demo.retry_later` | 기존 admission/테스트 fixture | #195/#196/#198/#201 |
| `context_limit`, `output_limit` | 422 | E / S=failed | false | `demo.supported_only` | budget/output validator | #195/#196/#198/#201 |

기존 공개 SSE code 대응도 보존한다. 학생 stream은 `empty_response→empty_output`, `timeout_first_output→first_output_timeout`, `timeout_total→run_timeout`, `output_limit→incomplete_response`, `interrupted→configuration_unavailable`로 표시하고 status=failed다. 학생의 session_ended는 run.cancelled이며 disconnect 중 전송 불가이면 terminal 전달을 보장하지 않고 정리/상태 조회로 복구한다. 멘토는 원래 출력 code를 유지하고 interrupted는 run.interrupted, session_ended는 run.cancelled, 나머지는 run.failed다. 새로운 demo 경계 오류는 표의 대문자 code 그대로 전달한다. `storage_error`, `provider_error`는 기존 public 실패 code로 유지하며 HTTP 503 / run.failed / retryable=true / recovery=demo.retry_later를 쓴다. 원장에는 기존 provider-neutral code/status를 보존한다. API의 canonical code와 SSE alias가 달라도 같은 recovery 의미로 소비한다.

normal의 `AUTH_EXPIRED`와 demo의 미방문/만료를 혼동하지 않는다. 알 수 없는 예외는 `DEMO_PREPARE_FAILED`(start 준비) 또는 `DEMO_RESET_FAILED`(reset) 또는 `configuration_unavailable`(AI)로 안전하게 변환하고 원래 예외/SQL/secret을 반환하지 않는다. 미지원 **입력문**은 §16의 정상 완료 콘텐츠 안내이며 422/transport 실패가 아니다. 부분 분석도 HTTP 성공의 별도 자료 상태이며 정상 분석으로 승격하지 않는다.

## 12. keyless schema·migration (C12)

[ADR-0001](../adr/0001-provider-keys-encrypted-in-db.md)의 외부 제공자 암호화 키·매 요청 재인증과 [ADR-0005](../adr/0005-provider-catalog-role-verification.md)의 역할 검증은 유지한다. keyless 예외는 **`APP_MODE=demo`이고 provider=`demo`인 로컬 예시 연결만** 허용한다. normal에서는 DB에 demo row가 있어도 조회용 표식 외에 선택/admission/실행하지 않는다.

| 저장 계약 | 결정 |
| --- | --- |
| `ProviderConnection.provider` | 기존 3종 + `demo`, 기존 UNIQUE 유지 |
| key material CHECK | 외부 3종의 기존 CHECK를 그대로 적용. demo는 encrypted_key/nonce/encryption_key_version/masked_hint **모두 SQL NULL**(빈 bytes/문자열도 금지), credential_revision=0, connection_version≥1. enabled true/false는 허용하되 준비됨은 추가 검증 필요 |
| 준비됨 | demo enabled + mode=demo + marker/manifest 호환 + 모델의 현재 역할 evidence가 모두 일치할 때만 ready. decrypt_key를 호출하지 않으며 가짜 암호화 키/마스터 키를 만들지 않음 |
| `ProviderAuditLog` | provider enum에 demo 추가. 기존 change_kind는 외부 provider에서 그대로 유지; 신규 `demo_seeded`는 provider=demo에서만 허용. seed에 actor_id=NULL, 이전/현재 credential_revision=0, 이전/현재 connection_version=1인 비밀 없는 1행. demo에서 key_saved/key_replaced/enabled/disabled/deleted 변경 행을 만들지 않음 |
| `GenerationRun.provider` | 최종 provider CHECK에 demo 추가. 기존 session/comparison 중 정확히 한 소속, 실행/턴 unique·partial index·FK·원장 연결 모두 유지. demo 비교 실행을 허용하는 근거가 아님 |
| `ApiUsageLog` / 모델 FK | provider TEXT에 demo 기록, 기존 FK 의미 유지. ModelConfig는 demo connection FK. provider 문자열을 모델 PK 대신 쓰지 않음 |
| `ResolvedModelConfig.provider` | 기존 Literal에 demo 추가, provider별 옵션 allowlist에도 demo 추가. 외부 provider 옵션/검증 유지 |
| `CallLimits.demo` | 기존 문서/DB의 필드 부재는 seed/일반 기존 데이터에서 2로 읽는 호환 기본값. demo 필드만 ge=2, 기존 total/admin 제약 유지; 서버 hard cap은 §8 |

마지막 migration은 입력 기준 036이다. #195 착수 시 최신 번호를 다시 확인해 **새 migration**을 추가하고 기존 001–036을 수정하지 않는다. provider_connection/provider_audit_log/generation_run의 필요한 CHECK만 재구축하며 `demo_metadata`도 새 migration으로 추가한다. runner의 rebuild allowlist/through 범위를 갱신하고 기존 정상 DB의 row/ID/FK/인덱스/트리거·migration history·031 receipt 검증을 보존한다. normal 설치/upgrade에 demo row·사용자·fixture를 자동 seed하지 않는다. fresh install과 기존 자료 upgrade 보존을 따로 검사한다. 기존 0012 ADR 두 파일은 수정/재번호하지 않는다.

## 13. 모델·옵션·역할 검증 (C13)

로컬 provider ID는 `demo`, MVP 모델 ID는 **`demo-classroom-v1` 하나**로 고정한다. 계획의 `demo-student-v1`/variant 제안은 채택하지 않는다. ADR-0012의 공통 시나리오 모델이 세 역할을 함께 담당하게 해 역할마다 별도 모델을 발명하지 않기 때문이다. 제목은 ‘준비된 수업 예시 v1’, 외부 모델 별칭·자동 대체 없음이다.

| 항목 | 허용값 |
| --- | --- |
| roles | student(text/stream), mentor(structured MentorOutput), analysis(structured UnifiedAnalysisOutput/MergeAnalysisOutput) |
| capability definition | `demo-cap-v1`; text/stream/structured=true, tools/image/audio=false. 입력 문맥 한도 65536 로컬 추정 토큰, max output 8192, 기준은 추정이며 실제 usage 아님 |
| 옵션 | `max_output_tokens:int` 4096..8192만 허용, 역할별 seed 기본값 4096. temperature/reasoning/thinking/output_config/service_tier 및 알 수 없는 옵션 거절. 생략은 작성 기본값 확정 후 스냅샷 저장, 실행 시 재병합 없음 |
| 역할 출력 계약 | student=`s1-v1`, mentor=`s3-v1`, analysis=`s4-v2`; 분석 JSON schema_version=2, 시나리오 config_schema_version=1과 별개 |
| role evidence | 기존 RoleVerification의 status=succeeded, verified_at=고정 seed 시각, credential_revision=0, connection_version=1, capability_definition_version=demo-cap-v1, role_contract_version=위 역할값, probe_request_id는 생략. secret/외부 probe 없이 로컬 fixture 검증 후 생성 |
| evidence 유효성 | mode/연결 revision·version/capability·역할 계약/manifest 호환 tuple 모두 일치. 불일치 stale 및 VERSION_MISMATCH, enabled만 켜서 복구하지 않음 |

로컬 evidence의 succeeded는 출력 형식 검사 통과이며 실제 제공자 검증·교육 품질 인증이 아니다. fixture가 바뀌면 capability definition 버전도 올리고 seed evidence를 재검증한다. 새 시나리오 작성/편집은 세 역할에 같은 demo 모델 PK/connection PK/model_id를 저장하고 역할별 옵션은 독립 보존한다. 정수 PK는 seed builder가 실제 row에서 얻어 manifest의 논리 ref에 연결한다. 존재하지 않는 숫자를 계약에 하드코딩하지 않는다.

## 14. 외부 AI fail-closed (C14)

설정·startup·clone에서 marker/schema/manifest와 함께 모든 제공자 연결의 key material을 검사한다. 외부 연결의 비활성·무키 placeholder는 정식 installer가 만든 경우만 허용하고 외부 ModelConfig/스냅샷 모델·키 material은 하나라도 있으면 거절한다. demo connection의 비어 있지 않은 material도 거절한다. 외부 환경 키(OPENAI_API_KEY/ANTHROPIC_API_KEY/GOOGLE_API_KEY/GEMINI_API_KEY 및 SDK 자격·base URL override), 운영 DB URL/mount/secret을 demo에 주입하지 않는다. 환경 검사는 `.env` 자동 흡수를 끈 뒤 수행한다.

`approve_call`/`admit_call`/`recheck_call`은 서버 mode, 현재 context/lease, provider/model/evidence를 확인한다. normal은 demo를 거절, demo는 demo 외 provider 및 catalog/probe/comparison operation을 거절한다. `execute_call`도 permit과 요청 provider/model/role/mode를 독립 재검사하고 demo일 때 로컬 adapter만 등록한다. secret은 None이며 fake permit로 외부 SDK에 진입할 수 없다.

기존 `student_bot.py`와 `tutor_bot.py`의 공통 admission/execution 호출, retry/readmission, catalog/probe, 관리자 직접 요청, startup/cleanup까지 동일 정책을 적용한다. 실제 외부 SDK 생성 경계(`openai_generation.py`, `anthropic_generation.py`, `google_generation.py`, 각 catalog와 `google_client.py`)에 도달하기 전에 demo 외부 호출을 거절하며 로컬 fixture로 조용히 성공 대체하지 않는다. `src/utils/openai_helpers.py`는 응답 파서이고 `src/utils/session_feedback.py`는 전달받은 DB의 저장 보고서 reader이므로 SDK 생성 guard를 추가할 대상이 아니다. reader에는 검증된 방문객 DB를 전달하고 기존 접근·삭제 정책을 유지한다. 공통 경계의 거절을 잡아서 외부 경로로 fallback하지 않는다.

SDK constructor·HTTP transport·outbound socket 감시에서 외부 AI 시도가 **0**이어야 통과다. 키를 비운 사실만으로 통과하지 않는다. 인프라 outbound allowlist/운영 mount 부재·권한 증명은 #206 문서와 D5 승인 범위에서 추가 검증한다. 이 문서로 인프라를 변경하지 않는다.

## 15. provider 요청·event·실패 fixture (C15)

기존 [invocation_types.py](../../src/services/invocation_types.py)의 필드를 유지한다. `TextRequest`의 provider/model_id/role/system_instruction/messages/validated_options/request_id와 keyword-only service_tier/context_budget_json, `StructuredRequest.output_schema/validation_context`를 재사용한다. demo 호출에는 신규 keyword-only `demo_context`(서버의 고정 context/lease 참조), `demo_selection`(아래 불변 선택)를 추가한다(#198). normal은 둘 다 None이며 외부 SDK payload에 전달하지 않는다. 클라이언트가 이 객체를 직접 공급할 수 없다.

`demo_selection`은 `contract_version`, `content_version`, `fixture_version`, `manifest_sha256`, `scenario_ref="unit-fraction-v1"`, `content_state="initial"|"after_first"|"after_compare"`, `branch:null|"A"|"B"|"C"`, `input_kind:"A"|"B"|"C"|"common"|"unsupported"`, `trigger:null|"auto"|"manual"`, `target_turn_id:UUID|null`, `analysis_phase:"none"|"single"|"chunk"|"merge"`, `fixture_case`를 가진다. 공개 runtime의 fixture_case는 `normal`만 허용하고 실패 case는 테스트가 서버 객체를 직접 구성할 때만 가능하다. request query/header/본문/TESTING으로 case를 선택하지 않는다. 값·버전·실제 이력 불일치는 VERSION_MISMATCH/INVALID_REQUEST로 거절한다.

신규 adapter 공개 함수는 기존 제공자 adapter의 호출 형태를 따른다: `generate_text(request, secret, timeouts, *, deadline) -> CallEvent`, `stream_text(...) -> async iterator[CallEvent]`, `generate_structured(...) -> CallEvent`. secret이 None 이외거나 mode/provider/model이 틀리면 거절한다. catalog adapter는 제공하지 않는다. 요청의 system_instruction/messages는 원래 스냅샷·실제 메시지로 만들며 fixture가 입력을 다른 문장으로 교체하지 않는다. `service_tier`는 None만 허용한다.

| CallEvent.type | 의미·저장 계약 |
| --- | --- |
| text_delta | 학생 본문을 Unicode code point 기준 최대 32자씩 순서대로 전달. 선택 본문은 고정, 대기는 청크당 최대 10ms의 취소 가능한 대기, 총 대기 1초 이하. deadline/cancel을 청크마다 검사 |
| usage | 측정한 값 없음. event 생략 가능; 전달하면 기존 `empty_usage()`와 동일 null/false 의미만 |
| completed | text는 전체 본문, structured는 검증한 dict. 학생/멘토/분석의 실제 저장 성공 후만 공개 `output.completed` 또는 분석 완료 상태 통지 |
| refused | error_code=refused, 본문을 정상 메시지로 승격하지 않음 |
| interrupted | error_code=interrupted, 부분 본문은 실행 기록에만; 취소/lease 정리 수행 |
| error | invalid_output/invalid_json/invalid_reference/empty_response/timeout_* 등 기존 code. 성공 fixture를 대신 반환하지 않음 |

event iterator는 종료 event를 정확히 한 번 내고 더 이상 delta를 보내지 않는다. 소비자는 terminal 부재도 invalid_output으로 처리한다. `CallEvent.completed` 자체가 DB commit 증거는 아니다. 공개 SSE는 기존 `run.accepted`, `output.delta`, `output.completed`, 실패 terminal을 재사용한다. 각 demo frame은 §11 namespace 필드를 가지며 normal frame은 바꾸지 않는다.

필수 테스트 전용 `fixture_case`는 `interrupted_after_delta`, `refused`, `invalid_output`, `invalid_json`, `invalid_reference`, `empty_response`, `timeout_connect`, `timeout_first_output`, `timeout_total`, `analysis_partial`, `analysis_failed`, `transient`다. 동일 case·입력은 같은 지점에서 종료한다. transient는 매 attempt 같은 일시 실패를 내며 분석의 최대 1회 자동 retry/원장 2회를 검사한다. 숨은 adapter 전역 카운터로 다음 결과를 바꾸지 않는다. timeout은 주입 clock/deadline으로 재현하고 긴 실제 sleep은 요구하지 않는다. analysis_partial은 유효한 일부와 누락/오류를 함께 만들어 기존 degraded 판정 경로를 통과시킨다. analysis_failed는 채택 가능한 결과가 없고 기존 정상 채택 결과를 보존한다.

## 16. 결정성·재전송·retry·콘텐츠 진행 (C16)

콘텐츠는 D0-02 §4–6 원문 그대로다. selector는 `text.strip()` 후 **현재 단계의 원문 완전 일치만** 인식한다. 내부 공백 축약·유사도·키워드/의도 추정·자동 번역은 하지 않는다. initial에서 A/B/C, after_first에서 공통 입력만 지원하고 after_compare에서는 모든 추가 입력이 unsupported다. 미지원은 D0-02 §5의 일반 학생봇 응답으로 정상 완료 턴을 저장하되 교육 진행 상태를 유지한다. 실패/부분 출력은 진행을 바꾸지 않는다.

선택은 `(fixture_version, snapshot의 지원 콘텐츠 hash, 완료된 지원 경로 상태, strip 입력, role, trigger, target_turn)`의 함수다. request_id는 예약/재전송을 구분할 뿐 응답을 무작위화하지 않는다. unsupported 턴의 사이 삽입도 A/B/C→공통 합류를 바꾸지 않는다. state/branch를 browser storage가 정하지 않고 실제 저장 이력에서 재구성한다.

학생/멘토/분석의 기존 request_id 예약 계약(ADR-0004/0008)을 유지한다. 같은 scope/session/operation/request_id와 같은 입력은 기존 run·turn·결과 반환/조회이며 새 멘토 검사·원장 attempt를 만들지 않는다. 같은 ID에 다른 body는 REQUEST_CONFLICT. 명시적 retry만 실패한 같은 turn_id에 새 generation run UUID를 만들고 교사 메시지는 복제하지 않는다. 정상 완료 run을 retry해서 추가 턴을 만들지 않는다.

학생/멘토·관리자 분석은 자동 retry 0, 교사 분석은 기존 정책대로 response_received=false이고 출력 전의 transient/rate_limited/timeout_connect에서 최대 1회만 재시도한다. deadline/slot 재획득/lease 현재성을 다시 검사한다. refused/invalid_output/invalid_reference/이미 받은 부분 응답은 자동 retry하지 않는다. timeout_total을 새 성공 예시로 바꾸지 않는다. refresh/back/GET은 예약/생성을 하지 않는다.

지원하지 않는 **설정 조합**은 저장/게시 규칙을 통과할 수 있지만 새 세션 실행 전 `DEMO_UNSUPPORTED_CONFIGURATION`으로 안내한다. 지원 hash는 §16.1의 정확한 projection을 hash한 값이다. 제목/그룹·역할별 output-token 옵션은 제외하지만 student.name/public_profile 등 명시된 콘텐츠는 포함한다. 기존 전체 snapshot config_hash와는 다른 값이다. 제목만 편집한 지원 시나리오는 실행 가능하며 임의 rubric을 준비된 결과로 분석하지 않는다. 세션 스냅샷은 실행 후 관리자 편집과 무관하게 유지한다.

### 16.1 supported_config_sha256의 정확한 projection (R9)

공유 helper의 단독 소유자는 **#195의 기존 `src/services/model_configuration.py`**다. 신규 공개 `supported_config_projection(config_schema_version, config) -> dict`, `supported_config_sha256(config_schema_version, config) -> hex64`를 제공한다. #196/#197/#198은 이 helper를 import/호출하고 독립 복제하지 않는다. G2 전에는 아래 고정 vector를 module-boundary stub의 기대값으로 사용할 수 있다.

입력은 config_schema_version의 strict int=1 및 기존 ScenarioConfig 검증 결과다. default를 채운 model_dump와 같은 값을 사용하고 extra/explicit null 금지 등 기존 strict schema 검증은 먼저 수행한다. nullable인 resolved_model_config/rubric.level만 null을 허용한다. projection은 **아래 열거한 필드만** 포함한다.

| projection의 정확한 경로 | 포함 값·기본/null 처리 |
| --- | --- |
| `config_schema_version` | strict int 1 |
| `config.problem.public_text`, `.learning_objective` | str, 생략 기본 각각 빈 문자열 |
| `config.student.name`, `.public_profile`, `.internal_profile`, `.misconception`, `.behavior_instruction` | str, 각각 기본 빈 문자열. 공개 학생 소개/이름도 포함하며 ‘표시 metadata’로 빼지 않음 |
| `config.mentor.mode`, `.name`, `.welcome_message`, `.behavior_instruction` | 각각 기본 manual, 멘토, 빈 문자열, 빈 문자열 |
| `config.mentor.intervention_policy.condition`, `.start_turn`, `.min_interval_turns`, `.window_turns`, `.max_interventions` | 각각 기본 빈 문자열/3/2/10/3, strict int 규칙 유지 |
| `config.analysis.context`, `.expected_understanding`, `.instruction`, `.rubric_name`, `.rubric_description`, `.category_name`, `.classification_enabled` | 각각 기본 빈 문자열 6개/true |
| `config.analysis.rubric` | 배열 순서 그대로, 각 원소의 id/name/criteria/level만. 기본 `[]`; 원소의 str 기본 빈 문자열, level 생략은 명시 null로 채움. 정렬/중복 제거 없음 |
| 세 역할의 `config.<role>.resolved_model_config` | 없거나 null이면 명시 null. 있으면 **provider/model_id 두 필드만** 가진 object, 문자열 원문 그대로. model_config_id/provider_connection_id와 options 전체는 projection에서 제외. 실제 PK/옵션 검증은 별도로 반드시 유지 |

`title/subject/target_grade/groups/is_active/action/expected_version/acknowledge_review` 등 config 밖 metadata와 `config.runtime` 전체(보존용 context_turn_limit), 모든 모델 options는 포함하지 않는다. 값의 trim/NFC/NFKC/대소문자 변경·기존 모델 별칭 변환은 하지 않는다. `json.dumps(projection, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("utf-8")` 바이트의 SHA-256 소문자 hex다. UTF-8 불가 문자열/비정상 타입/지원 안 되는 config_schema_version은 INVALID_REQUEST이며 hash를 만들지 않는다.

다음은 **projection 단위 검사 vector**다. model 미선택 초안이므로 실행 지원 승인이 아니며 제품 manifest의 실제 D0-02 hash를 이 값으로 대체하지 않는다. canonical 입력 JSON(생략 필드는 위 기본값 적용):

```json
{"config_schema_version":1,"config":{"problem":{"public_text":"문제"},"student":{"behavior_instruction":"지시"},"mentor":{},"analysis":{},"runtime":{}}}
```

정확한 hash 대상 바이트(아래 한 줄을 UTF-8로 인코딩, 마지막 newline 없음):

```json
{"config":{"analysis":{"category_name":"","classification_enabled":true,"context":"","expected_understanding":"","instruction":"","resolved_model_config":null,"rubric":[],"rubric_description":"","rubric_name":""},"mentor":{"behavior_instruction":"","intervention_policy":{"condition":"","max_interventions":3,"min_interval_turns":2,"start_turn":3,"window_turns":10},"mode":"manual","name":"멘토","resolved_model_config":null,"welcome_message":""},"problem":{"learning_objective":"","public_text":"문제"},"student":{"behavior_instruction":"지시","internal_profile":"","misconception":"","name":"","public_profile":"","resolved_model_config":null}},"config_schema_version":1}
```

기대 digest: `84c3988b5ab0c5ad9aa47e1c7c66edb755909e8b65a820eac9456bcf56dfaadb`. python3의 json/hashlib만 사용해 계산했다(앱 import 없음). 같은 vector의 mentor 기본값/각 null을 명시해서 넣어도 같다. 실제 승인된 D0-02 config에서 title 또는 4096→8192 output-token 변경은 hash 동일(모델 옵션 허용 범위 검증은 여전히 필요), PK 교체도 provider/model_id가 같으면 동일하다. rubric criteria/배열 순서나 student/mentor behavior_instruction 변경, model_id 변경은 hash가 달라져 실행 시 UNSUPPORTED_CONFIGURATION이다. 잘못된 옵션을 ‘hash에 없으니 허용’하지 않는다.

## 17. fixture·seed manifest·호환 버전 (C17)

신규 `src/demo/fixtures/manifest.json`(#197)은 제품 실행의 버전 원본이며 테스트 fixture를 import하지 않는다. JSON object의 필수 필드는 아래와 같다. 알 수 없는 필드·중복 key·상대 경로 탈출·파일 hash 불일치는 빌드/시작 실패다.

| 필드 | 타입·값/검증 |
| --- | --- |
| `manifest_version`, `contract_version`, `content_version`, `fixture_version` | int 1, `d0-03-v1-r3`, `d0-02-v1`, `demo-fixture-v1` |
| `db_schema_version` | `_migrations` 최종 filename 문자열. #195의 실제 새 migration명과 정확히 일치해야 함; 현재 036을 demo 호환으로 선언하지 않음 |
| `migration_set_sha256` | 정식 baseline+적용 migration 파일의 경로/내용을 정렬해 만든 SHA-256. 단순 마지막 번호만으로 호환 판단 금지 |
| `scenario_config_schema_version`, `analysis_schema_version` | int 1, int 2 |
| `provider`, `model_id`, `capability_definition_version`, `role_contract_versions` | demo, demo-classroom-v1, demo-cap-v1, `{student:s1-v1,mentor:s3-v1,analysis:s4-v2}` |
| `scenario_ref`, `supported_config_sha256` | unit-fraction-v1, §16 canonical 실행 콘텐츠의 64자리 소문자 hex |
| `seed_version`, `seed_timestamp`, `seed_refs` | demo-seed-v1, 고정 `2026-10-18T00:00:00Z`, 논리 admin/teacher/group/scenario/completed_session/model/connection → 양의 PK 또는 기존 UUID 타입 |
| `files` | 정렬된 `{path:POSIX상대경로,sha256:hex64,kind:student\|mentor\|analysis\|failure\|seed}` 배열, manifest 자신 제외, 중복 경로 금지 |
| `lookup_version`, `responses` | int 1 및 §17.1의 **정확한 flat key→상대 file path** object. 도달 가능한 A/B/C/common/unsupported·실패 case/phase 조합을 전부 열거, 인용은 symbolic ref |
| `seed_provenance` | `{source:"synthetic",generator:"src/demo/seed.py",content_version:"d0-02-v1"}`; 운영 DB·테스트 서버 자료 참조 금지 |

개별 응답 fixture JSON도 공통 envelope를 쓴다: `fixture_version:string`, `case_id:string`, `role:student|mentor|analysis`, `content_state:initial|after_first|after_compare`, `branch:null|A|B|C`, `input_kind:A|B|C|common|unsupported`, `trigger:null|auto|manual`, `analysis_phase:none|single|chunk|merge`, `terminal:completed|refused|interrupted|error`, `error_code:null|string`, `text:null|string`, `structured:null|object`, `partial_text:null|string`. extra field는 거절한다. 학생 normal은 text만, 멘토/분석 normal은 structured만 채우고 terminal=completed/error_code=null이다. 실패 envelope는 §15 case와 기존 error code의 정해진 조합만 허용한다. interrupted_after_delta만 partial_text를 실제 응답 prefix로 채우며 invalid_* case의 고의 잘못된 structured는 검증이 실패해야 한다는 expected case로만 허용한다. seed 설정 파일은 별도 ScenarioConfig 스키마로 검증한다.

structured template는 기존 역할 출력과 같은 필드 구조를 유지하되 `message_id` 위치의 값만 `@teacher_first|@student_first|@teacher_common|@student_common|@unsupported_teacher:<완료순서>|@unsupported_student:<완료순서>` symbolic 문자열로 허용한다. binder는 지정 위치에서만 실제 int로 바꾸고 다른 문자열/원문에는 치환하지 않는다. §17.1의 phase별 projection 후 현재 입력에 남은 refs를 바인딩하고 역할·인용·순서가 틀리면 invalid_reference다. single에서 필수 ref 부재는 invalid_reference, chunk/merge의 범위 밖 항목은 §17.1대로 바인딩 전에 제외한다. 단일 분석/chunk/merge는 현재 입력에 들어 있는 ref만 쓴다. A/B/C의 내용·코칭·분류 template는 D0-02 원문을 사용하며 unsupported 메시지 목록은 실제 메시지에서 생성해 non_analyzable로 확장한다. 이 envelope와 symbolic 바인딩 결과를 #196/#197의 공통 단위 입력으로 쓴다.

`manifest_sha256`은 저장된 manifest UTF-8 bytes 자체의 SHA-256이며 manifest 자신의 필드는 아니다. seed builder 출력 receipt가 `source_commit`(실제 전체 commit), `manifest_sha256`, `db_schema_version`, `seed_sha256`, `logical_data_sha256`, `command`를 기록한다. commit/hash/실제 migration명은 생성 결과로만 채우며 미래 값을 발명하지 않는다. 논리 데이터의 row/order/고정 시각/seed ID는 결정적이고 password hash salt·SQLite 물리 page 차이로 물리 DB hash가 달라도 logical_data_sha256은 같아야 한다. DB hash는 해당 생성 산출물의 무결성 확인에 쓴다. 허구 User는 웹 로그인 불가이며 비밀번호를 공개 자격 증명으로 발급하지 않는다.

유효 조합은 위 contract/content/fixture/config/analysis/capability/role tuple **하나**와 #195의 새 migration set이다. G2가 실제 migration명과 hash를 제공하면 #197이 manifest에 넣고 G3/G4 통합 기록이 동일성을 확인한다. 이것은 미정 설계가 아니라 산출물 참조의 equality 규칙이다. 다른 조합을 암묵 지원하지 않는다. fixture 변경은 fixture/seed/capability 버전 상승·검증 재실행을 요구한다.

seed는 허구 관리자/교사 각 1명, 배정 그룹 1개, 게시 대표 시나리오 1개, demo 연결/모델 각 1개와 B→공통 완료 예시 1개를 포함한다. 완료 예시의 메시지·분류·정상 분석·원장도 실제 FK/역할 계약을 충족한다. 외부 AI usage는 unknown이다. seed 완료 세션의 출처는 seed_prepared, 방문객의 신규 완료 세션은 visitor_generated로 구분한다. 정상 여정 5단계는 완료 세션 2개, 신규 세션의 메시지 5개(교사2/학생2/멘토1)+summary 1행이다.

배포 시 기존 공간은 version_replace로 모두 만료하고 새 seed를 검증한 후 새 방문을 받는다. 활동 중 방문객 DB에 migration을 반복 적용하지 않는다. 앱/seed/fixture 호환 실패 시 readiness 503, 기존 DB로 fallback하지 않는다.

### 17.1 manifest lookup·분석 phase·chunk projection (R8)

#197은 responses를 작성하고 #196 provider.py의 신규 `fixture_lookup_key(selection)`가 다음 문자열을 만든다. **필드 순서/구분자/대소문자를 고정**한다.

`<fixture_case>/<role>/<content_state>/<branch-or-none>/<input_kind>/<trigger-or-none>/<analysis_phase>`

fixture_case는 §15 enum, role은 student/mentor/analysis, content_state/input_kind는 §15 enum, branch는 A/B/C 또는 null을 뜻하는 `none`, trigger는 auto/manual 또는 `none`이다. student는 trigger=none/phase=none, mentor는 auto 또는 manual/phase=none, analysis는 trigger=none/phase=single|chunk|merge다. analysis의 branch/state/input_kind는 최신 chunk 메시지가 아니라 **전체 실제 세션의 교육 진행 상태**에서 고정한다. after_compare이면 input_kind=common, after_first이면 input_kind=선택된 A/B/C, initial이면 branch=null/input_kind=unsupported다. 사이에 낀 unsupported 입력은 이 key를 바꾸지 않고 실제 classification에만 반영한다. student의 첫 지원 입력은 응답 전 state=initial/branch=선택 A/B/C/input_kind=같은 A/B/C로 선택하고, 미지원이면 branch는 기존 진행 분기(없으면 null)를 유지한다. 따라서 A→공통과 B→공통은 다른 key다. branch가 아직 없으면 none이고 최초 지원 분기 전의 unsupported 이력만 사용한다. 멘토는 완료된 target_turn의 input_kind/당시 branch/state를 사용한다(더 늦은 세션 상태로 바꾸지 않음).

responses[key]는 files에 정확히 1회 등록된 fixture-root 상대 POSIX path이고 그 파일의 case_id는 **그 key 전체와 동일**해야 한다. envelope의 role/state/branch/input_kind/trigger/phase도 key와 일치한다. 누락/중복/불일치는 VERSION_MISMATCH이고 유사 key/다른 branch/자동→수동 fallback은 없다. 분석 single/chunk는 StructuredRequest.output_schema=UnifiedAnalysisOutput, merge는 MergeAnalysisOutput이어야 하며 다르면 실행 전 VERSION_MISMATCH다.

아래는 네 selection을 위한 responses 필드의 완전한 lookup 예제다(전체 제품 manifest는 이 밖의 도달 가능한 key도 열거한다). 모든 path는 신규 `src/demo/fixtures/` 아래 제안이다. 각각 files에 같은 path/hash/kind로 등록한다.

```json
{"normal/analysis/after_compare/A/common/none/single":"analysis/A-single.json","normal/analysis/after_compare/B/common/none/single":"analysis/B-single.json","normal/mentor/after_compare/A/common/auto/none":"mentor/A-common-auto.json","normal/mentor/after_compare/A/common/manual/none":"mentor/A-common-manual.json"}
```

| 전체 lookup key(위 예제) | output_schema / 구체적인 template 결과 |
| --- | --- |
| `normal/analysis/after_compare/A/common/none/single` | UnifiedAnalysisOutput, teacher_first→r1/quote=이유를 말해 줄래?, teacher_common→r3/공통 질문의 실제 인용; student_first의 A 원문 근거 |
| `normal/analysis/after_compare/B/common/none/single` | UnifiedAnalysisOutput, teacher_first→r2/quote=이 답을 기억해 봐., teacher_common→r3/공통 질문의 실제 인용; student_first의 B 원문 근거. A 인용이 남으면 invalid_reference |
| `normal/mentor/after_compare/A/common/auto/none` | MentorOutput `{"should_intervene":false,"feedback":"","reason_summary":"같은 전체와 한 조각 크기를 연결하는 표현이 나와 추가 자동 코칭을 생략한다."}` |
| `normal/mentor/after_compare/A/common/manual/none` | MentorOutput `{"should_intervene":true,"feedback":"학생봇이 같은 전체에서 한 조각의 크기를 설명했습니다. 이 표현만으로 실제 이해를 확정하지 말고, 다음 연습에서는 다른 단위분수도 같은 방법으로 설명하도록 물어보세요.","reason_summary":"완료된 대상 턴에 대한 수동 도움 요청에 코칭한다."}` |

예컨대 첫 key의 envelope는 fixture_version=demo-fixture-v1, case_id=그 key, role=analysis, content_state=after_compare, branch=A, input_kind=common, trigger=null, analysis_phase=single, terminal=completed, error_code/text/partial_text=null, structured=D0-02 §6의 **A 전용 전체 UnifiedAnalysisOutput template**다. B는 해당 key/branch와 D0-02 B 전체 template로만 바뀐다. 두 멘토 envelope는 role=mentor, branch=A, state/input_kind=after_compare/common, phase=none이며 trigger와 위 structured만 auto/manual에 맞게 달라진다. 이 예제의 모든 필드/선택 차원은 mandatory이고 나머지 structured 본문도 D0-02에 정해진 전체 필드를 사용한다.

chunk/merge도 같은 key 문법으로 마지막 phase만 바꾼 명시 파일을 사용한다. chunk 파일은 해당 branch의 symbolic template 원본이다. #196 output_binding.py가 **현재 chunk validation_context.messages와 owned_message_ids로** projection한다: `message_classifications`는 owned teacher만(unsupported는 실제 해당 메시지의 non_analyzable), `strengths`/`improvements`/`dialogue_coaching`은 owned ID만 남긴다. `misconception_findings`의 각 항목은 모든 근거가 현재 chunk messages에 존재하고 **모든 kind에서 적어도 하나의 근거가 owned**여야 한다. changed 이외(maintained/deviated/insufficient_evidence)는 **모든 근거가 owned**여야 한다. changed는 overlap 근거를 함께 쓸 수 있지만 적어도 하나는 owned이고, 서로 다른 학생 메시지 두 개 이상의 유효한 인용·시간순 조건도 만족해야 한다(S3). 조건을 만족하지 않는 항목/현재 chunk에 없는 symbolic ref는 검증 전에 제외하고 다른 chunk의 가짜 ID를 채우지 않는다. 실패 fixture 이외에는 모든 owned teacher의 classification이 한 번씩 있어야 한다. coverage와 기존 validate_item은 바꾸지 않는다.

merge 파일은 `message_classifications` 필드가 **없고** 정확히 `schema_version`, `misconception_findings`, `brief_feedback`, `strengths`, `improvements`, `dialogue_coaching` 여섯 JSON key만 갖는 MergeAnalysisOutput template다(S4). `findings`/`coaching` 별칭은 허용하지 않으며 기존 verified_sections의 exact key-set 검증을 유지한다. binder는 검증된 chunk 결과와 allowed_evidence에 있는 실제 (message_id,quote)만 골라 시간순으로 묶고, 모든 근거가 검증 목록에 없는 항목은 제외한다. 새 인용/분류/메시지는 만들지 않는다. single은 전체 실제 세션, chunk는 현재 owned 범위, merge는 검증된 범위만 사용한다. 예: B 첫 턴만 owned인 chunk에는 r2 하나, 공통 턴만 owned이면 r3 하나다. 공통 턴 chunk에서 B 첫 학생 응답은 overlap/미소유, 공통 학생 응답은 owned라면, overlap만 인용한 maintained 항목은 **projection에서 제거되어 validation error를 만들지 않는다**. 앞 overlap+뒤 owned를 시간순 인용한 유효 changed 항목은 남고 classification 중복은0이다. #197/#198/#203은 이 정상 chunk/merge 기대값을 소비한다. phase별 출력 검증·누락/부분 판정은 기존 계약에 맡기며 실패를 정상으로 수선하지 않는다.

## 18. 실제 근거 바인딩·멘토·분석 (C18)

신규 output binding은 symbolic refs(`teacher_first`, `student_first`, `teacher_common`, `student_common`, `target_turn`)를 **현재 context의 현재 세션에서 조회한** 실제 message/turn로 바꾼다. seed·다른 방문객·다른 세션 ID를 재사용하지 않는다. 같은 숫자 ID라도 scope/session/역할/완료 여부·내용·시각 순서를 모두 검증한다. 인용은 원문 부분 문자열이며 실패하면 invalid_reference다. 사실을 맞추기 위해 입력/인용을 고치지 않는다.

멘토는 `StructuredRequest(output_schema=MentorOutput)`와 기존 trigger=auto/manual, 필요 시 expected_should_intervene 검증 context를 사용한다. A/B/C 첫 지원 턴 자동 개입 true, 공통 턴 자동 개입 false/feedback="", unsupported 자동 false다. 수동 도움은 해당 완료 대상 턴에 true와 D0-02 원문을 사용하고 no_intervention을 재사용하지 않는다. 내부 reason_summary는 실행 기록에만 보존하며 교사·관리자 공개 투어/상태 API/CSV에서 제외한다(ADR-0007). 저장 result_kind는 코칭이 있으면 기존 `message`, 없으면 `no_intervention`을 쓰고 미개입은 코칭 메시지를 만들지 않는다. ‘멘토 개입’은 사용자 용어이며 `intervention`이라는 새 DB enum을 만들지 않는다.

사후 분석은 종료 세션만, 스냅샷의 rubric/지시·실제 완료 교사/학생 메시지로 `UnifiedAnalysisOutput(schema_version=2)`를 검증한다. 기존 validation_context의 `messages`(id/role/content), `labels`, `classification_enabled`, chunk의 `owned_message_ids`, merge의 `allowed_evidence`를 그대로 적용한다. actor_id도 실제 현재 User다. 멘토·환영 문구·불완전 학생 응답은 분석 입력이 아니다.

추천 두 턴의 input/reviewed_message_ids=교사2+학생2, teacher/classified_message_ids=교사2, missing/invalid 목록은 빈 값이어야 한다. B는 r2/r3, A/C는 r1/r3. unsupported 교사는 non_analyzable/rubric_id=null, 일반 학생 응답은 검토 범위에 포함하되 학습 주장 근거로 쓰지 않는다. changed는 서로 다른 앞/뒤 학생 메시지의 실제 시간순 근거 두 개 이상을 요구하며 하나라도 없으면 만들지 않는다. 필요하면 insufficient_evidence를 사용한다.

부분/실패/취소는 기존 실행 상태와 coverage로 남기며 정상 채택 결과를 덮어쓰지 않는다. 새 채택 시 분류·피드백·요약을 §6의 동일 transaction으로 교체한다(ADR-0008). 장문은 ADR-0010의 bounded 분할(최대8+merge), 범위·호출 수·추정 안내와 확인을 유지한다. 공개 투어에 장문/실패 주입 기능은 추가하지 않되 검증 fixture에서는 누락·부분·기존 채택 보존을 검사한다. 실시간 오개념 점수/교육 효과 확정은 만들지 않는다.

## 19. usage와 provenance (C19)

로컬 adapter의 실제 외부 AI 호출은 없음이다. 기존 `empty_usage()`의 input/output/total/cache/reasoning 토큰·estimated_cost_usd/pricing_*·raw_usage_json은 null, usage_complete=false다. DB/화면/CSV에서 unknown을 0으로 변환하지 않는다. 로컬 context token 추정은 기존 context_budget_json에 추정으로만 남긴다. 로컬 처리 시간은 서버 경과일 뿐 실제 AI 속도 보증이 아니다.

신규 공통 provenance 객체는 `{mode:"demo",response_source:"prepared_example",external_ai_called:false,contract_version:"d0-03-v1-r3",content_version:"d0-02-v1",fixture_version:"demo-fixture-v1",record_origin:"seed_prepared"|"visitor_generated"}`다. template·demo 상태·실행/분석 JSON·export에 같은 의미를 전달한다. provenance는 서버의 seed ref/생성 메타데이터에서 계산하고 클라이언트 입력을 신뢰하지 않는다. 완료 예시도 실제 체험 생성 자료와 명확히 구분한다.

화면 배너와 이력·분석·CSV 안내는 D0-02의 ‘데모 · 준비된 예시 응답 · 실제 AI 품질 평가용 아님’ 의미를 유지한다. UI 영어 전환은 콘텐츠/출처 버전을 바꾸지 않는다. unknown 토큰/가격은 ‘알 수 없음/미측정’으로 표시한다.

## 20. 위험 endpoint 정책 (C20)

모든 자료 요청은 먼저 context/User/CSRF(unsafe method)를 검증한다. 유효한 demo 관리자도 아래 금지 요청은 서버에서 `DEMO_ACTION_FORBIDDEN` 403을 받는다. demo guard는 body의 키·비밀번호 처리와 외부 SDK/재인증/작업 예약보다 먼저다. 교사는 기존 admin 권한 거절을 유지한다. normal route 동작은 보존한다.

| method / 기존 path·행위 | demo 관리자 정책 | 결과·소유 |
| --- | --- | --- |
| GET `/admin/ai/state`, AI 화면·저장된 모델/역할 상태 | 방문객 DB의 정적 예시만 허용 | 200 provenance/unknown usage; #200 |
| POST `/admin/ai/providers/{provider}/key`, `/enabled`, `/delete` | 모든 provider에서 거절(교체 포함) | 403; #200 |
| POST `/admin/ai/providers/{provider}/catalog` | 거절 | 403, 외부 호출/원장 예약 없음; #200 |
| POST `/admin/ai/models`, `/admin/ai/models/{model_id}/update` | 등록·활성/옵션 변경 모두 읽기 전용 | 403; #200. 시나리오의 허용 demo 모델 선택은 §13으로 별도 허용 |
| POST `/admin/ai/models/{model_id}/probes` | 로컬/외부 역할 시험 예약도 거절 | 403; #200 |
| GET `/admin/ai/probes/{request_id}` | 같은 방문객 DB/권한의 기존 상태만 | 200 또는 RESOURCE_NOT_FOUND 404; #200 |
| POST `/admin/ai/probes/{request_id}/cancel` | 자신의 namespace에 이미 존재하는 작업의 안전한 취소만 | 기존 성공 응답/부재 404, 신규 호출 없음; #200 |
| POST `/admin/ai/settings/update` | 읽기 전용 | 403; #200 |
| POST `/admin/comparisons`, `/{comparison_id}/panels/{panel_id}/retry` | 구형 단일 역할 비교 실행 거절 | 403; #200 |
| POST `/admin/comparison-conversations`, `/{identifier}/turn`, `/adopt`, `/mentor`, `/end`, `/children/{child_id}/panels/{panel_id}/retry` | 새 비교 대화의 생성·실행·채택 모두 거절 | 403; #200 |
| GET 비교 목록/상세/panel model, `/admin/model-comparison` | 방문객 DB의 읽기 전용/지원 예정 안내 | 목록 200(기본 비어 있음), 없는 ID 404; 실행 유발 없음; #200 |
| POST 두 비교 API의 `/stop` 및 구형 panel `/stop` | 현재 namespace의 이미 존재하는 작업 취소만 | 기존 결과/부재 404, 다른 scope 취소 0; #200 |
| DELETE `/admin/comparisons/{comparison_id}`, `/admin/comparison-conversations/{identifier}` | 기존 권한·비용 원장 보존의 방문객 기록 삭제만 | 기존 204/404; 실행·채택 체험을 추가하지 않음; #200 |
| GET 관리자 세션/분석/통계/사용량 및 `/metrics` | 현재 DB 관리자 조회만 | 200, 삭제/권한 정책 §22; #200/#194 |

외부 모델을 시나리오 API/스냅샷/공통 호출로 직접 주입하면 선택 단계 422 UNSUPPORTED_CONFIGURATION, 오염된 저장 자료 발견은 503 CONFIGURATION_INVALID다. 거절된 요청은 catalog/probe/비교 run/원장·사용량을 새로 만들지 않는다. 기본 seed에는 비교 자료/활성 probe가 없다. 안전한 stop/delete 허용은 비교 실행의 MVP 포함을 뜻하지 않는다.

## 21. 자유 탐색·관리자 조회와 수정 (C21)

유효한 교사는 배정된 시나리오/자신의 세션·이력·분석/CSV만 기존 권한으로 이용한다. 관리자는 현재 방문객 DB의 계정/그룹/시나리오/기여자/about/세션·분석·통계·usage를 조회한다. 직접 URL·HTMX modal·집계·export도 같은 factory를 사용한다. 모든 query에 tenant 컬럼을 추가하지 않고 DB 선택 경계를 사용한다.

| 기존 행위 | 허용 범위·검사 |
| --- | --- |
| `/admin/users` create/update/delete | 허구 계정 CRUD, §8 quota와 기존 중복/권한/삭제 제한. demo 역할 선택 대상은 seed_refs의 admin/teacher만이며 새 계정을 임의 user_id로 로그인시키지 않음 |
| `/admin/users/bulk/preview`, `/admin/users/bulk/register`, GET template | 기존 parser/필드 검증 재사용, §8 bytes/rows/현재 user 수를 preview와 최종 transaction에서 모두 검사. 실제 사람 자료 입력 금지 안내, 자격 증명은 외부 로그인 권한이 아님 |
| `/admin/groups` 및 `/{group_id}/update\|delete` | 허구 그룹 편집/배정 허용, quota/기존 참조 제한/버전 검사 유지 |
| `/admin/scenarios` 및 기존 draft/update/delete 경로 | 저장·게시·편집 허용, 기존 completeness/review/expected_version 검사와 §8 quota. 공통 demo 모델 검증 적용. 지원 외 콘텐츠는 저장 성공과 실행 지원 여부를 따로 표시 |
| `/admin/about` 및 contributor update/delete | 허구 기여자 편집 허용, quota·기존 필드 검증 유지. 외부 URL을 서버에서 fetch하지 않음 |
| 시나리오 공개/그룹 변경 | 현재 권한을 새 요청에 적용, 과거 세션 설정 스냅샷은 유지; 새 실행은 §16 지원 hash 검사 |

여기서 허용하는 모든 mutation은 §6 write_guard와 하드 quota를 통과해야 한다. 병렬 생성은 count 확인과 insert/commit을 한 transaction/전환 lock 안에서 수행해 두 요청이 마지막 자리를 중복 소비하지 못한다. 방문객 AppSetting을 통한 전역 quota/다른 방문객 설정 변경은 금지한다.

## 22. 계정 삭제·세션 삭제·reset (C22)

교사 삭제는 기존 관리자 자기 삭제/진행 중 세션/모델 검증 기록 제한을 유지한다. 정상 여정은 먼저 대화를 종료한 뒤 교사를 삭제한다. 종료 세션의 teacher_id는 NULL로 바꾸고 본문·정상 분석은 보존한다. 삭제 교사 역할 전환은 ROLE_DELETED이며 선택만으로 재생성하지 않는다. seed admin은 남아서 reset할 수 있다.

세션 soft delete는 대화 본문·분석·직접 URL·상태/분석 API·modal·선택/전체/단일/사용자별/교사 CSV에서 해당 내용을 숨긴다. 기존 tombstone 표시는 보존할 수 있으나 API/단일 다운로드는 RESOURCE_NOT_FOUND다. bulk는 삭제된 행을 제외한다. 사용자별 export가 삭제 교사 ID에서 보존 내용을 우회 노출하지 않도록 기존 권한/부재 정책을 유지하고 관리자의 세션 export로만 보존 자료를 확인한다. 삭제 중 실행 취소는 scope를 포함하며 원장 보존 의미를 바꾸지 않는다.

reset은 현재 공간 전체의 새 seed 교체다. 사용자 삭제의 이력 보존이나 soft delete와 동일 동작이 아니다. reset 후 신규 공간에 사용자 입력·추가 실행/분석은 없고 seed 완료 예시만 남는다. 다른 방문객/normal 자료·진행 작업은 불변이다. 이미 받은 CSV를 회수한다고 안내하지 않는다. 공간 만료/폐기로 임시 원장도 함께 정리하지만 normal 비용 원장 보존 정책을 완화하지 않는다.

## 23. CSV 호환 provenance (C23)

기존 [export.py](../../src/services/export.py)의 관리자 필수 열 27개와 교사 필수 열 20개, 그 순서·값 의미·행 구조는 유지한다. 기존 `meta_json`·snapshot_origin·제목·본문을 provenance로 덮어쓰지 않는다. **demo CSV에만 아래 5열을 마지막에 덧붙이는 호환 확장**을 채택한다. normal CSV는 byte 의미/헤더를 보존한다. 필수 열 이름으로 읽는 기존 DictReader 파서는 그대로 동작하며 정확한 전체 헤더를 고정한 소비자는 demo의 suffix를 명시적으로 허용해야 한다.

| 신규 suffix 열(이 순서) | 모든 message/summary 행의 값 |
| --- | --- |
| `demo_response_source` | `prepared_example` |
| `demo_external_ai_called` | 문자열 `false` |
| `demo_content_version` | `d0-02-v1` |
| `demo_fixture_version` | `demo-fixture-v1` |
| `demo_record_origin` | seed 세션=`seed_prepared`, 새 체험 세션=`visitor_generated` |

관리자 단일·전체·선택·사용자별 export와 교사 단일·다중 공통 exporter에 적용한다. route는 `admin_session_export.py`의 `export_sessions`, `export_selected_sessions`, `export_user_conversations`, `download_session`과 `session_analysis.py`의 교사 export를 포함한다. 공통 exporter는 #200, 교사 route의 전달/안내는 #198이 소비한다. provenance는 서버 context와 seed refs에서 만들고 normal exporter 호출에 demo 값을 기본 주입하지 않는다.

파일명은 `demo_d0-02-v1_<admin|teacher>_<session-ID|selected|all|user-ID>.csv`다. ID는 실제 요청 자원, 사용자 삭제 후 세션 export에는 user-ID를 만들어 넣지 않는다. `Content-Disposition`은 이 고정 ASCII 패턴과 검증 ID로 구성한다. UTF-8, 기존 csv writer quoting/줄바꿈, 수식 시작 문자(=,+,-,@,tab,CR) 방어를 유지한다. 주석/배너 행·별도 summary 행을 추가하지 않는다. confidence는 미측정 빈 값, unknown 가격/토큰을 임의 수치로 쓰지 않는다.

기본 두 턴 신규 세션은 teacher2/student2/tutor1/summary1=6행, 자동 미개입은 행 없음이다. 분류/analysis_schema_version=2/status=ok/coverage·findings summary JSON은 D0-02 §8대로다. 모든 내부 설정/reason_summary/secret은 배제한다. `csv.DictReader`로 관리자 필수 prefix 27개·교사 prefix 20개·고정 suffix 5개, 행수/role/분기 분류/실제 현재 session_id/provenance/JSON parse를 검사한다. 교사 message_id 열을 새로 넣지 않는다. 삭제 이후 전 export에서 삭제 본문/분석이 없고 타 방문객 자료가 없음을 각각 검사한다.

## 24. UI key/data/event와 여섯 단계 (C24)

#199는 template와 inline reader·신규 tour JS/CSS, #201은 기존 JS/i18n의 단독 소유자다. 공통 template 변수 `demo_config`는 mode, §9 DemoState의 표시 가능 필드, `start_url`, `role_url`, `reset_url`, `state_url`, `public_url`, `csrf_cookie_name`, `csrf_header_name`, `ui_language:ko|en`, `tour_facts`를 가진다. `tour_steps`는 서버 필드가 아니라 아래 #199 계산 결과다. normal은 `{mode:"normal",public_url:string|null}`만 전달한다. `<script type="application/json" id="demo-config">`에 안전 JSON으로 넣고 DB factory/내부 reason/secret을 넣지 않는다.

DOM 계약은 root `data-demo-mode`, `data-demo-scope`, `data-demo-generation`, `data-demo-auth-epoch`, 투어 `data-demo-step="start|scenario|dialogue|analysis|admin_csv|reset"`, 조작 `data-demo-action="start|role|reset|close|skip|resume|restart"`다. 번역 key namespace는 `demo.*`, 주요 키는 `demo.banner`, `demo.content_language`, `demo.supported_only`, `demo.reset_confirm`, `demo.role_deleted`, `demo.expired`, `demo.start_required`, `demo.tour.start`, `.scenario`, `.dialogue`, `.analysis`, `.admin_csv`, `.reset`와 §11의 모든 recovery key다. #201이 한영 쌍을 추가하고 D0-02 §9 의미를 그대로 따른다.

### 24.1 투어 사실·관찰·최종 map의 단독 소유 (R7)

**최종 `tour_steps` 계산의 유일 소유자는 #199의 신규 `static/js/demo-tour.js`**다. 서버 #193의 신규 session.py는 자료 기반 `TourFacts`만 계산하며 complete/current/pending map이나 다운로드 수신 사실을 계산하지 않는다. #201은 기존 페이지/JS의 아래 관찰 이벤트만 #199에 전달한다. 서버에 브라우저 관찰을 보내는 ACK API는 없고 서버는 그것을 알았다고 기록하지 않는다.

`GET /demo/state?include_tour=true`의 TourFacts는 `{schema_version:1,browser_scope_id:string,generation:int,auth_epoch:int,journey_session_id:int|null,teacher_entered:bool,scenario_created:bool,dialogue_complete:bool,analysis_ready:bool,teacher_deleted:bool,session_deleted:bool}`다. started_at/body/분석/이름/내부 reason은 담지 않는다. teacher_entered는 이 세대에서 teacher 역할에 성공 진입한 이력이며 현재 역할 조건이 아니다. scenario_created는 대상의 스냅샷 존재, dialogue_complete는 해당 세션의 지원 두 턴·첫 자동 코칭/공통 미개입 저장, analysis_ready는 지원 경로 완료+종료+정상 채택 분석 존재다. analysis_ready는 dialogue_complete/scenario_created/teacher_entered를, dialogue_complete는 scenario_created/teacher_entered를 함의한다.

**여정 대상은 현재 generation에서 seed_refs.completed_session을 제외한, 지원 스냅샷으로 만든 visitor 세션 중 가장 작은 session PK**다. #193이 최초 조회 시 고정하고 namespace-bound journey_session_id를 제공한다. start가 아직 teacher로 전환되지 않은 admin이면 teacher_entered=false다. 이후 세션을 더 만들거나 현재 다른 세션 화면을 열어도 대상을 바꾸지 않는다. soft delete/교사 삭제 후에도 그 ID와 긍정 milestone을 보존하며 다른 신규 세션/seed로 대체하지 않는다. 새 대상 선택은 reset/새 체험뿐이다. #198의 신규 세션 생성 결과와 #199 tour는 이 규칙을 소비하며 ID를 클라이언트가 선택하는 query는 없다.

#193의 공개 `capture_tour_facts(ctx, handle)`는 caller가 이미 보유한 transition/write_guard handle과 current lease 아래(재진입 없음) 실제 자료에서 그 대상의 사실을 계산하고(현재 teacher 역할 이력은 #193의 start/role 성공 시 함께 기록), `retain_tour_facts(handle, facts)`는 해당 generation의 **증명된 true만 합집합**으로 보관한다. 보관은 #193의 tour cache, 동기화는 #191 handle을 사용하며 별도 전환 lock은 만들지 않는다. 일반 상태 조회도 transition 안에서 capture→retain한다. #200은 교사/대상 세션 삭제 transaction 전에 같은 handle 아래 capture하고, **commit 성공 후 lock을 놓기 전에** retain 및 teacher_deleted/session_deleted를 실제 삭제 결과로 확정한다. rollback이면 갱신0이다. 따라서 이후에는 삭제 본문/분석을 읽거나 API에 내보내지 않고 저장한 boolean만 돌려준다. 세대 만료/reset/restart는 이 cache를 버린다. #193은 대상 row가 tombstone으로 처음 발견되면 ID는 유지하되 증명되지 않은 과거 true를 발명하지 않는다.

브라우저 관찰은 `demo:tour-observed` 이벤트의 `{schema_version:1,browser_scope_id,generation,auth_epoch,journey_session_id:int,kind:"history_viewed"|"admin_record_viewed"|"csv_received",request_id:UUID}`다. #199가 현재 namespace/대상과 응답 성공을 확인한 이벤트만 수용한다. history_viewed는 종료된 대상의 이력/분석 상세가 정상 응답으로 실제 DOM에 열렸을 때다. admin_record_viewed는 관리자 화면에서 **그 대상**의 종료 기록/분석을 열었을 때이며 이 동작은 history_viewed도 충족한다. seed나 두 번째 신규 세션의 열람으로 첫 대상 관찰을 채우지 않는다.

csv_received는 대상 단일 관리자 CSV 또는 **여정 세션 하나만 선택한 CSV**를 fetch해 status200, 전체 body EOF 수신, UTF-8 CSV parse, 관리자27+suffix5 헤더·모든 행의 대상 session_id·출처/버전·summary행·행수 검증을 끝내고 Blob 다운로드를 시작한 뒤다. #200은 demo의 단일 `download_session`과 POST `/admin/sessions/export-selected`의 `export_selected_sessions` **두 응답 모두** 클라이언트의 `X-Demo-Export-Request-ID:UUID`를 같은 값으로 echo하고(수신 관찰에서 요청/응답 ID 일치 필수), `X-Demo-Export-Row-Count:int>=1`(header 제외 실제 행수)을 제공한다. #199의 tour 단일 다운로드는 링크 navigation 대신 fetch를 사용하고 template 연결을 소유한다. #201은 기존 선택 다운로드에서 요청 시점의 선택 session ID 집합과 namespace를 캡처하고 같은 헤더/본문 검증 후 이벤트를 발행한다(S5).

선택 export의 csv_received는 **요청 선택 집합도 `{journey_session_id}`이고 파싱한 행의 session_id 집합도 정확히 같을 때만** 허용한다. 다른 세션을 섞은 export는 기존 권한/삭제 제외 정책대로 유효한 다운로드지만 여정 관찰은0이다. 삭제 제외 결과 우연히 대상만 남아도 원래 선택 집합이 혼합이면 관찰0이다. abort/부분 body/parse 실패/다른 세션/오류 응답도 관찰0이다. 기존 관찰 payload/storage/단계 소유는 그대로다. **사용자가 로컬 저장 대화상자에서 저장했는지는 앱이 알 수 없으며 csv_received가 그것을 보증하지 않는다.** #204는 별도로 다운로드 파일을 실제 파싱한다.

관찰의 localStorage key는 `demo:<browser_scope_id>:<generation>:tour-v1`이고 payload는 `{schema_version:1,journey_session_id:int|null,observations:[위 이벤트 객체],closed:bool,skipped:bool}`다. 관찰은 kind별 첫 성공 한 건만 보관한다. #199는 localStorage를 쓰기 직전 읽은 값/현재 메모리/다른 탭의 storage event를 같은 대상의 kind별 합집합으로 병합하고, 순서 차이로 검증된 관찰을 false로 덮지 않는다. body/CSV/secret은 저장하지 않는다. auth_epoch는 캡처 검증용이고 **투어 key에는 넣지 않으므로 역할 전환/refresh/언어 전환에도 같은 대상의 관찰을 보존**한다. 이것은 일반 draft/run storage의 epoch 격리에 대한 유일한 예외다. 늦게 도착한 old-epoch 응답으로 새 관찰을 만들지 않지만, 전환 전에 검증·보관한 관찰은 계속 쓴다. storage 불가/삭제 시 관찰은 메모리에서만 유지되고 refresh 후 재열람/재수신을 요구한다. 서버 사실은 유지된다. 다른 브라우저 context/새 generation에는 공유하지 않는다.

| 단계 | #199가 계산하는 완료 predicate |
| --- | --- |
| start | teacher_entered(또는 아래의 더 나중 단계 완료가 함의) |
| scenario | 대상 scenario_created. ‘첫 응답 전’은 안내 시점이며 응답 후 완료를 취소하는 조건이 아님 |
| dialogue | 대상 dialogue_complete |
| analysis | 대상 analysis_ready AND history_viewed |
| admin_csv | analysis 완료 AND admin_record_viewed AND csv_received. 현재 role이 teacher여도 과거 관리자 관찰을 취소하지 않음 |
| reset | 위 다섯 완료와 teacher_deleted/session_deleted를 확인한 뒤 그 scope의 명시 reset 성공 응답 관찰 |

계산은 대상 하나의 사실/관찰만 결합하고 뒤 단계가 complete면 앞 단계도 complete로 채운다. complete가 아닌 첫 단계 하나만 current, 뒤는 pending이다. ready 자료가 잠시 로딩 중이면 마지막 유효 map을 유지하고 요청을 만들지 않는다. reset 성공 시 이전 map의 여섯 complete 표시는 일회 완료 안내에만 쓰고 저장 관찰/cache는 폐기한다. 새 generation은 새 facts와 빈 관찰로 시작한다. 일찍 reset한 경우 과거 여정을 완주했다고 표시하지 않는다.

기대 map 표기 순서는 start/scenario/dialogue/analysis/admin_csv/reset이고 C=complete, X=current, P=pending이다.

| 관찰 가능한 상황 | 대상/기대 map |
| --- | --- |
| 교사로 시작하고 새 세션 전 | target=null, `C X P P P P` |
| 교사 두 턴·정상 분석, 이력 아직 안 열음 | target=S1, `C C C X P P` |
| 완료 교사 여정+이력/분석 재열람 | S1, `C C C C X P` |
| 위 상태에서 admin 전환/refresh | S1과 같은 map, epoch만 증가; 단계 회귀0 |
| S1은 1턴, S2만 두 턴/분석 완료 | target=S1, `C C X P P P`; S2 사실/CSV로 합성하지 않음 |
| S1이 정상 완료된 뒤 S2 생성 | S1 유지, 기존 map 유지 |
| S1 관리자 열람+CSV 성공 수신/parse | S1, `C C C C C X` |
| S1만 선택 다운로드, 선택/파싱 집합 모두 S1·헤더/본문 검증 성공 | S1, `C C C C C X`; 해당 행 CSV와 같은 관찰 |
| S1/S2 혼합 선택 다운로드 성공 | 유효 다운로드, 새 관찰0. 이전 성공 없으면 `C C C C X P`, 있으면 기존 완료 유지 |
| CSV status200 뒤 abort/parse 실패 | 이전 성공이 없으면 `C C C C X P`, 이미 성공이면 완료 유지 |
| CSV 성공 후 교사 삭제→S1 soft delete, reset 전 | S1 tombstone 유지, `C C C C C X`; 본문/분석 비노출 |
| CSV 전에 S1 삭제 | target 변경 없음, 미확인 관찰은 미완료; 새 대상은 reset으로만 시작 |
| CSV 성공 뒤 refresh/다시 teacher 전환 | 같은 관찰·facts의 `C C C C C X`(교사가 삭제됐으면 role 전환은 ROLE_DELETED) |
| 안내대로 reset 성공 | 이전 scope 일회 `C C C C C C`; 새 admin 세대 `X P P P P P` |

신규 문서 이벤트는 `demo:state`, `demo:role-changed`, `demo:reset-started`, `demo:reset-completed`, `demo:expired`다. detail은 `{browser_scope_id,generation,auth_epoch,state,role,contract_version,content_version,fixture_version,reason:null|code}`이고 CSRF 값/자료 본문은 없다. #193/#194 응답을 #199 tour가 이벤트로 연결하며 기존 JS #201이 소비한다. reset-completed는 ready 응답 이후만 보낸다. 만료/stale 오류는 같은 복구 이벤트로 연결한다.

닫기/건너뛰기/재개는 안내 상태만 변경한다. restart는 안내와 실제 자료로 완료 상태 재계산, 자료 reset은 별도 확인 POST다. reset 완료 알림은 이전 안내 패널에서 일회 표시하고 새 공간의 단계 상태는 다시 시작한다. 한국어 콘텐츠는 영어 UI에서 번역하지 않는다. keyboard focus 복귀·명시 label/aria-live 안내, 390px·데스크톱·200% 확대에서 입력/확인/종료 조작이 가려지지 않아야 한다.

## 25. 브라우저 stale 상태와 복구 (C25)

투어의 사실/관찰은 §24.1의 별도 generation key를 사용한다. 그 외 storage key prefix는 `demo:<browser_scope_id>:<generation>:<auth_epoch>:`다. 이어서 기존 chat-run/chat-draft/chat-mentor/analysis의 자원 ID를 붙인다. 원래 session ID만의 key에서 새 세대 자료로 자동 이관하지 않는다. draft는 같은 scope/generation/auth_epoch에서만 보존하고 stale이면 이전 초안임을 표시한 뒤 사용자가 명시 복사할 수 있으나 전송은 하지 않는다. 역할 전환 시 교사 초안을 관리자 DOM에 재노출하지 않는다. 언어/패널 접힘 등 내용 없는 환경설정만 기존 범위를 유지한다.

모든 pending fetch/SSE/타이머는 시작 namespace와 request_id를 캡처한다. 응답과 DOM의 scope/generation/auth_epoch가 다르면 반영하지 않고 controller abort, polling/timer 중단, run/분석 modal 참조 폐기 후 안내한다. 새 세대의 같은 정수 ID로 재연결하지 않는다. 자료 응답은 위 원칙을 따른다. 명시 제어 operation의 응답/현재-state GET만 마지막 확인 g/epoch 이상으로 단조 전진할 수 있으며, 요청에 고정된 reset operation이 성공하면 old scope 완료 관찰 후 새 scope를 적용한다. historical=true receipt는 적용하지 않는다. 상태 GET은 현재 namespace만 동기화하고 과거 자료 URL을 고쳐 자동 실행하지 않는다. 사용자가 현재 공간의 목록으로 이동하도록 한다.

탭 통지는 `BroadcastChannel("misconcept-demo-v1")`, 미지원 시 localStorage의 `demo:notification` storage event로 §24 detail만 전달한다. 다른 scope event는 무시, 같은 scope의 더 큰 generation/auth_epoch에서만 invalidation한다. 이벤트는 권한이 아니며 수신 탭은 `/demo/state`로 확인한다. 쿠키 공유 탭은 다음 요청 때도 서버 검증을 받는다. 채널 실패/닫힌 탭은 서버 오류가 최종 방어다.

pageshow(BFCache)/back/forward/refresh 때 먼저 상태를 확인하고 namespace 불일치면 이전 DOM 내용과 복구 버튼을 무효화한다. 새로고침은 저장된 실행 조회만 하며 POST/명시 retry를 재발행하지 않는다. 연결 단절의 결과가 불명확하면 기존 request_id 상태를 조회하고 새 ID로 다시 생성하지 않는다. 언어 전환은 같은 namespace의 draft/단계/실행을 보존하고 기존 미저장·진행 중 전환 guard를 존중한다. 만료 후 새 체험은 명시 start로 새 scope를 받는다.

## 26. 일반 서비스 demo 링크·단독 파일 소유 (C26–C27)

normal은 검증된 `DEMO_PUBLIC_URL`을 template의 `demo_config.public_url`로 전달하고 #199가 `<a>` 링크만 표시한다. 빈 값은 숨긴다. 배포 승인 기록과 실제 URL이 일치하지 않으면 D5 리허설/공개를 막는다. 해당 링크가 normal 인증/DB/provider를 전환하지 않는다. 실제 주소·서비스·mount·secret 주입 위치는 신규 `docs/demo/d5-02-approval.md`(#207)의 **D5-02 approval input**이다.

고정 입력 문서 `docs/demo/d0-01-boundary-survey.md`는 #188, `docs/demo/d0-02-experience-spec.md`는 #189 단독 소유이며 이 작업과 후속 구현에서는 읽기 전용이다.

아래는 계획 §9.5와 위임 답변의 단독 소유를 보존한 **파일별 쓰기 경계**다. 한 셀에 여러 경로가 있으면 전부 같은 단독 소유자이며 다른 이슈의 공동 쓰기를 허용하지 않는다. 기존 파일은 input SHA에 존재한다. 후속 구현의 변경 필요 범위만 수정하며 읽기 참조는 소유권이 아니다.

| 기존 파일(완전 상대 경로) | 단독 소유 | 허용 책임 |
| --- | --- | --- |
| `src/api/routes/session_helpers.py`, `src/services/generation_lifecycle.py`, `src/services/analysis_runs.py`, `src/services/probe_execution.py`, `src/services/probe_lifecycle.py` | #192 | namespace/lease/currentness/task·orphan 수명 |
| `src/services/comparison_stream.py`, `src/services/comparison_stop.py`, `src/services/comparison_history.py`, `src/services/comparison_conversation_history.py`, `src/services/comparison_conversation_recovery.py` | #192 | registry 호환만; 비교 실행 추가 없음 |
| `src/config.py`, `src/main.py`, `src/db/connection.py`, `src/api/dependencies.py` | #194 | config/middleware/DB resolver/오류/lifespan |
| `src/models/provider_connection.py`, `src/models/generation_run.py`, `src/api/schemas/scenario_config.py`, `src/api/schemas/ai_settings.py`, `src/services/model_capabilities.py`, `src/services/model_verification.py`, `src/services/model_configuration.py`, `src/services/lesson_connections.py`, `src/db/migrations/migrate.py` | #195 | schema·새 migration/installer·mode/capability/evidence |
| `src/services/call_admission.py`, `src/services/call_execution.py`, `src/services/invocation_types.py`, `src/api/routes/student_generation.py`, `src/services/generation_runs.py`, `src/services/mentor_generation.py`, `src/services/student_stream.py`, `src/services/mentor_stream.py`, `src/services/analysis_invocations.py` | #198 | 공통 호출/취소 scope·고정 context 전달·저장 |
| `src/api/routes/session_analysis.py`, `src/services/analysis_pipeline.py`, `src/services/analysis_chunks.py`, `src/services/analysis_results.py`, `src/services/invocation_ledger.py`, `src/services/student_bot.py`, `src/services/tutor_bot.py` | #198 | 조사에서 확인한 생성/분석 전달·원장/shield/chunk/최종 저장 방어. #192 lifecycle을 재작성하지 않음 |
| `src/api/routes/auth.py`, `src/api/routes/scenarios.py` | #199 | 진입 링크/안내 연결만; 인증·DB 정책 변경은 #194 요청 |
| `src/templates/layout.html`, `src/templates/login.html`, `src/templates/scenarios.html`, `src/templates/chat.html`, `src/templates/session_history.html`, `src/templates/analysis.html`, `src/templates/error.html` | #199 | 배너/투어/data/inline CSRF reader |
| `src/templates/admin/ai.html`, `src/templates/admin/scenario_editor.html`, `src/templates/admin/sessions.html`, `src/templates/admin/users.html`, `src/templates/admin/groups.html`, `src/templates/admin/about.html`, `src/templates/admin/analysis.html`, `src/templates/admin/comparison_conversation.html` | #199 | 관리자 표시와 inline reader |
| `src/templates/partials/analysis_result.html`, `src/templates/partials/analysis_modal.html`, `src/templates/partials/session_detail.html` | #199 | 실제 저장 자료/출처·삭제 표시 |
| `src/api/routes/admin_providers.py`, `src/api/routes/admin_catalog.py`, `src/api/routes/admin_models.py`, `src/api/routes/admin_probes.py`, `src/api/routes/admin_ai_settings.py`, `src/api/routes/admin_comparisons.py`, `src/api/routes/admin_comparison_conversations.py` | #200 | 위험 endpoint와 registry API 소비 |
| `src/api/routes/admin_sessions.py`, `src/api/routes/admin_session_stats.py`, `src/api/routes/admin_session_export.py`, `src/api/routes/admin_analysis.py`, `src/api/routes/admin_users.py`, `src/api/routes/admin_session_actions.py`, `src/services/export.py` | #200 | 관리자 조회·삭제·CSV/registry 소비 |
| `src/i18n/__init__.py`, `src/i18n/catalog.json` | #201 | 한영 UI key와 기존 언어 전환 |
| `static/js/chat.js`, `static/js/student-stream.js`, `static/js/mentor-stream.js`, `static/js/display-language.js`, `static/js/scenario-comparison.js`, `static/js/comparison-conversation.js`, `static/js/comparison-history.js`, `static/js/comparison-results.js` | #201 | 기존 JS cookie/세대/복구 |
| `static/js/sessions.js`, `static/js/scenario-editor.js`, `static/js/admin-scenarios.js`, `static/js/analysis-toggle.js`, `static/js/ai-connections.js`, `static/js/student-sse.js` | #201 | 위임 답변의 추가 reader/오류·복구 |
| `static/js/analysis-result.js`, `static/js/scenario-models.js`, `static/js/ai-render.js`, `static/js/modal-lifecycle.js` | #201 | 조사에서 확인한 기존 DOM/응답·modal의 stale 무효화 |
| `.github/workflows/tests.yml` | #205 | 기존 필수 검사와 normal/demo 연결의 최소 CI 변경만 |

추가 패치 필요성(a)과 소유(b)는 아래로 확정한다. 기존 경계로 충분한 항목은 변경하지 않는다.

| 기존 파일/경계 | (a) 패치 필요? 이유 | (b) 필요 시 단독 소유 |
| --- | --- | --- |
| `src/api/routes/health.py` | 예. 방문객 dependency 없는 seed readiness와 visitor metrics 분리 | #194 |
| `src/cli/cleanup_comparisons.py` | 예. demo CLI를 DB 열기 전 거절 | #192 |
| `src/cli/provider_smoke.py` | 예. dry-run/TESTING/execute 모두 demo 거절 | #198 |
| `src/db/seed.py`, `src/db/init_schema.py`, `src/db/rotate_default_passwords.py`, `src/db/convert_scenarios.py`, `src/db/s2_cutover.py`, `src/db/s6_cutover.py`, `src/db/s6_credentials.py`, `src/db/migrations/migrate_015.py` | 예. standalone CLI의 demo 실행을 파일/DB 접근 전 거절; normal 변환/보존 의미 유지 | #194 |
| `src/db/migrations/migrate.py` | 예. CLI demo 거절, 명시적 staging engine의 정식 seed installer는 허용 | 기존 소유 #195 유지 |
| `src/db/s2_verification.py`, `src/db/s6_verification.py`, `src/db/s6_legacy.py` | 아니오. 공개 demo 요청에 연결하지 않고 위 CLI entry 차단; demo builder는 필요한 정식 installer만 호출 | 패치 없음, 읽기 참조 |
| `src/services/provider_reauthentication.py` | 예. demo에서 비밀번호/전역 limiter 전에 fail-closed; normal 제한은 보존 | #200 |
| `src/api/routes/admin_groups.py`, `src/api/routes/admin_scenarios.py`, `src/api/routes/admin_about.py` | 예. 허용 CRUD quota/현재성/지원 범위 표시 연결 | #200 |
| `src/services/admin_scenario_ops.py`, `src/services/admin_user_bulk.py` | 예. 최종 transaction의 quota·지원 상태, upload의 bounded 읽기/rows 재검사 | #200 |
| `src/api/routes/admin_api_usage.py` | 예. unknown usage/provenance의 방문객 집계 표시, 삭제 내용 비노출 확인 | #200 |
| `src/utils/openai_helpers.py`, `src/utils/session_feedback.py` | 아니오. 각각 응답 파싱과 전달받은 DB의 저장 보고서 조회이며 직접 SDK 생성·외부 호출이 없음. 외부 호출 차단은 §14의 실제 호출 경계에서 유지 | 패치 없음, #198 읽기 참조; 추가 수정 필요 시 §27 변경관리 |
| `src/services/role_output_contracts.py`, `src/services/analysis_output_contract.py` | 아니오. 기존 strict 출력/근거 검증 그대로 소비; demo 예외 추가 없음 | 패치 없음, #196/#198 읽기 참조 |
| `static/js/htmx.min.js` | 아니오. 앱의 event/header와 cache 복구로 연결 | vendored 수정 없음 |

신규 모듈·검사·evidence의 단독 소유는 다음과 같다(전부 **신규 제안**, 현재 구현 존재를 뜻하지 않음).

| 신규 경로 | 단독 소유 |
| --- | --- |
| `docs/demo/d0-03-contract-v1.md`, `docs/demo/d0-03-g0-acceptance.md`, `docs/adr/0013-demo-isolation-and-keyless-provider.md` | #190; 제품 파일 소유 없음 |
| `src/demo/database.py`, `src/demo/visitors.py` | #191; visitors.py가 VisitorState/transition lock·handle/guard·bootstrap admission의 유일 권위, database.py는 물리 DB 수명 |
| `src/demo/lifecycle.py`, `tests/test_demo_d1_02_lifecycle.py`, `docs/demo/d1-02-lifecycle-evidence.md` | #192; TaskControl/token/registry/cancel/drain, guard는 #191 위임 |
| `src/api/routes/demo.py`, `src/demo/session.py` | #193; 제어 HTTP/receipt·role orchestration/서버 TourFacts, 최종 tour map은 #199 |
| `tests/test_demo_config.py` | #194 |
| 새로운 번호의 migration(착수 시 번호 확정), 신규 demo_metadata 모델, `tests/test_demo_d2_01_schema.py`, `docs/demo/d2-01-schema-evidence.md` | #195; 모델의 정확한 신규 파일은 `src/models/demo_metadata.py`로 제안 |
| `src/demo/provider.py`, `src/demo/output_binding.py`, `tests/test_demo_provider.py` | #196 |
| `src/demo/seed.py`, `src/demo/fixtures/`(내부 `manifest.json` 포함) | #197 |
| `tests/test_demo_d2_04_calls.py`, `docs/demo/d2-04-calls-evidence.md` | #198 |
| `static/js/demo-tour.js`, `static/css/demo-tour.css`, `tests/browser_demo_d3_01_ui.mjs`, `docs/demo/d3-01-ui-evidence.md`, `docs/demo/d3-01-evidence/` | #199; 최종 tour_steps·관찰 보관/합성·tour 다운로드의 단독 소유 |
| `docs/demo/d3-02-endpoint-policy.md`, `tests/test_demo_d3_02_guards.py`, `tests/test_demo_d3_02_exports.py`, `docs/demo/d3-02-guards-evidence.md`, `docs/demo/d3-02-evidence/` | #200 |
| `tests/browser_demo_d3_03_recovery.mjs`, `docs/demo/d3-03-recovery-evidence.md`, `docs/demo/d3-03-evidence/` | #201 |
| `tests/browser_demo_d3_04_smoke.mjs`, `docs/demo/d3-04-integration-evidence.md`, `docs/demo/d3-04-integration-manifest.json`, `docs/demo/d3-04-evidence/` | #202; 통합 branch `demo/d3-04-integration`, 제품 공동 소유 없음 |
| `tests/test_demo_isolation.py`, `tests/test_demo_security.py`, `tests/test_demo_reset.py`, `docs/demo/d4-01-security-evidence.md`, `docs/demo/d4-01-evidence/` | #203 |
| `tests/demo_d4_02_server.py`, `tests/browser_demo_isolation.mjs`, `tests/browser_demo_journey.mjs`, `tests/browser_demo_recovery.mjs`, `docs/demo/d4-02-browser-evidence.md`, `docs/demo/d4-02-evidence/` | #204; #203은 helper 읽기/호출만 |
| `docs/demo/d4-03-release-acceptance.md`, `docs/demo/d4-03-evidence/` | #205; 제품 결함은 원 소유자에 반환 |
| `docs/demo/d5-01-deployment.env.example`, `docs/demo/d5-01-operations.md` | #206 |
| `docs/demo/d5-02-approval.md`, `docs/demo/d5-02-evidence/`, `docs/demo/d5-02-rehearsal-evidence.md` | #207 |
| `docs/demo/d5-03-approval.md`, `docs/demo/d5-03-evidence/`, `docs/demo/d5-03-handover.md` | #208; #207 승인 경로는 읽기 참조 |

기존 테스트 수정은 `tests/test_config.py`/`tests/test_html_error_pages.py`/`tests/test_seed.py` → #194, `tests/test_migrations.py`/`tests/test_provider_migration.py`/`tests/test_provider_startup.py` → #195, `tests/test_probe_lifecycle.py`/`tests/test_comparison_lifecycle.py` → #192, `tests/test_student_lifecycle.py`/`tests/test_mentor_lifecycle.py`/`tests/test_analysis_runs.py`/`tests/test_analysis_chunk_lifecycle.py`/`tests/test_admission_missing_configuration.py` → #198, `tests/test_deleted_session_access.py`/`tests/test_export_deleted_teacher.py`/`tests/test_s4_csv.py`/`tests/test_comparison_security.py` → #200이 단독 소유한다. 여기 없는 기존 파일의 수정이 실제 필요하면 #190 변경관리로 먼저 한 소유자를 정한다. 신규 전용 fixture/test 파일도 그 이슈가 소유하며 공용 conftest를 임의 공동 수정하지 않는다. #202는 통합 smoke, #205는 최종 회귀·증거 취합 책임이며 제품 소유를 가져오지 않는다.

## 27. gate·추적표·변경관리 (C28)

| gate | 소유/승인과 통과 증거 |
| --- | --- |
| G0 | #190의 고정 입력·계약에 DB/provider/UI/검증 소비자 리뷰와 작성자와 별도 reviewer 결과, 결함 해결, @DomineYH 명시 승인. 기록은 G0 문서 §3–5. v1-r3 재검토 ACCEPT·승인 완료; §31의 v2 변경은 보조 독립 검토 ACCEPT·명시 workflow 예외·maintainer 변경 승인 완료; CI/통합은 별도 확인 |
| G1 | #194: #191–193 lifecycle/실제 DB resolver/middleware/no-context/CSRF 연결. 작은 테스트 seed 허용, 실제 AI 저장 성공은 G3에서 검사 |
| G2 | #195: 실제 새 migration/FK·인덱스·자료 보존, 명시적 mode의 keyless/schema/model/evidence. G1과 상대 병합 순서는 독립 |
| G3 | #198: G1+G2+#196+#197의 실제 학생/멘토/분석·원장·최종 저장/reset race/egress 0 |
| G4 | #202: #198–201의 고정 통합 SHA, 실제 seed/FastAPI/CSRF의 짧은 여섯 단계·normal 로그인 smoke |
| G5 | #203/#204가 G4 실제 SHA에서 독립 보안/브라우저 검사 → #205 별도 리뷰·최종 전체 회귀·결함 재검증 → @DomineYH 승인 |

#203/#204 검사 작성과 #206 운영 초안은 G0 뒤 시작 가능하나 실제 통합 acceptance는 각각 G4/G5 뒤다. G1에 G3의 역방향 의존성을 만들지 않는다. 아래 gate 표기는 최초 실제 연결 확인과 독립 재검증 시점이다.

| C-item / 요구 출처 | 계약 절 | 소비 이슈 | gate | 소비자가 내부 구현 없이 검사할 방법 |
| --- | --- | --- | --- | --- |
| C01 #190/계획2.1·4.1·9.2/#195답2 | §2 | #194–199/#203/#206 | G1/G2/G5 | 서버 mode 인자 두 값, production+demo, client mode 주입·TESTING 전환 거절 |
| C02 #190/조사6.2·7 | §3/5 | #191–194/#198/#200 | G1/G5 | A/B 동일 PK seed에서 factory·scope 분리, 쿠키/URL 조작 시 DB open 0 |
| C03 조사6.3·7/계획2.3·5 | §4/26 | #191/#192/#194/#198/#200/#203/#206 | G1/G3/G5 | 무쿠키 health, 오류 user 조회·CLI·bootstrap의 DB 선택/거절 관찰 |
| C04 #191/조사7/계획2.2·2.4 | §5 | #191/#193/#194/#197/#203 | G1/G4/G5 | backup 중 WAL 자료 보존, FK 검사, publish 실패 staging 비노출·lease0 삭제 |
| C05 #192답1/조사6.5·7 | §6 | #192/#198/#200/#203 | G1/G3/G5 | 같은 connection/probe/run ID의 두 scope에서 cancel/pop이 B 불변 |
| C06 #192답3/조사6.6·7 | §6/7 | #191/#192/#194/#198/#200/#203 | G1/G3/G5 | commit barrier 양쪽 순서, shield/chunk/callback late write 0·old DB 재생성 0 |
| C07 #191–193/명세8·11 | §7/9 | #191–194/#198–201/#203/#204 | G1/G3/G4/G5 | 중복 reset·두 탭·만료/요청·잠김/drain timeout·restart 상태 전이 |
| C08 #191/#195답3/#206답2/조사7 | §8 | #191/#192/#194/#195/#198/#200/#203/#206 | G1/G2/G3/G5 | 마지막 quota 자리 병렬 예약·해제, TTL clock, 물리 정리 전 budget 미반환 |
| C09 #193/#199답2/#201답2/명세3 | §9 | #193/#194/#199/#201/#203/#204 | G1/G4/G5 | POST schema·idempotency·role busy·삭제 교사 거절, 임의 user_id 거절 |
| C10 조사6.8·7/계획5 | §10 | #193/#194/#199/#201/#203/#204/#206 | G1/G4/G5 | 실제 cookie 속성·세션28800/CSRF session 수명, 잘못된 토큰/origin 403 |
| C11 #190/#200답1/#201답2 | §11 | #191–201/#203/#204 | G1/G2/G3/G4/G5 | pre-SSE HTTP와 post-SSE terminal code/recovery 동일성·단일 terminal |
| C12 #195답1·3/#190답1/조사5 | §12/ADR-0013 | #195–198/#200/#203 | G2/G3/G5 | demo key NULL만 허용, 외부 무키 enabled 거절, normal demo 차단·migration 보존 |
| C13 #195답2·3/명세2·11 | §13 | #195–200/#203 | G2/G3/G5 | 공통 모델의 역할 evidence/옵션 whitelist·tuple 불일치 stale |
| C14 #190/조사6.7/계획5·10D | §2/14/20 | #194–198/#200/#203/#206 | G1/G3/G5 | key/.env/외부 row/startup/legacy/direct request에 SDK·HTTP·socket 시도0 |
| C15 #196/계획4.1·4.2·10G | §15 | #195/#196/#198/#199/#201/#203 | G2/G3/G5 | text/stream/structured의 delta 합·terminal·취소·실패 case·invalid schema |
| C16 #193/#196/명세4·5·11 | §16 | #192/#193/#196–199/#201/#203/#204 | G3/G4/G5 | A/B/C→common, strip 외 미지원, replay run/turn/attempt 수 불변·명시 retry |
| C17 #197/명세11/계획2.4·11 | §17 | #191/#195–198/#203/#204/#206 | G2/G3/G4/G5 | manifest/migration/files hash 변경 시 시작 거절, logical seed 재생성 비교 |
| C18 #196/명세4–6·11/ADR2·3·7·8·10 | §18 | #196–198/#200/#203 | G3/G5 | 실제 ID/역할/인용/순서 검사, no_intervention 메시지0, 부분/실패의 기존 채택 보존 |
| C19 #196/명세7·8·11/ADR5·9 | §19 | #195–201/#203/#204 | G3/G4/G5 | 원장/화면 null·false와 추정 분리, seed/new origin 표시 |
| C20 #200답1/조사6.4/계획5 | §20 | #194/#198–201/#203/#204 | G3/G4/G5 | 모든 위험 method 직접 요청403, run/원장/egress 증가0, 정적 조회만200 |
| C21 조사6.4/명세3·5·8/#200답3 | §21 | #194/#199/#200/#203/#204 | G1/G4/G5 | CRUD와 bulk quota/권한, 목록·통계·usage·metrics가 A DB만 집계 |
| C22 명세8·11/조사6.9/계획7 | §22 | #192/#193/#198–201/#203/#204 | G3/G4/G5 | 교사삭제 teacher_id NULL·기록 보존, 세션삭제 전 접근/export 비노출 |
| C23 #200답2/명세8·11/계획10H | §23 | #197/#199/#200/#203/#204 | G4/G5 | CSV prefix/suffix·6행·인용/UTF8/수식 방어·normal 불변·삭제/타방문 제외 |
| C24 #199답2/#201답2/명세7·9 | §24 | #193/#194/#199/#201/#204 | G4/G5 | DOM data/key/event schema·실제 자료로 6단계 재계산·한영/키보드/390px |
| C25 #201답2/조사6.8/명세7 | §25/10 | #193/#194/#199/#201/#203/#204 | G4/G5 | old URL/저장/pending/BFCache·2탭 role/reset 응답 무효화, draft·동일 실행 유지 |
| C26 #199답3/#206답1/계획2.1 | §2/26 | #194/#199/#206 | G1/G4/G5 | URL 비설정 링크0·잘못된 URL 설정실패·링크가 normal mode 불변 |
| C27 #190/조사6.3·6.4/#182/답변소유표 | §26 | #191–201/#203–206 | G1–G5 | 변경 diff 경로→단독 소유 표 대조, CLI/health/reauth/CRUD 패치 필요성 확인 |
| C28 #190답2·3/계획9.2·9.3·9.5·10 | §27/G0기록 | #191–206 | G1–G5 | 고정 후보·소비자·독립/maintainer 승인·변경 영향/재검토 기록 확인 |
| C29 #203/#204/#206/계획10·11 | §28 | #203–206 | G4/G5 | 실제 서버·CSRF·독립 보안/E2E, 후보 tuple·운영 인계 기대값 대조 |

계약을 바꾸면 #190 소유자가 변경 이유·기존/새 버전·C-item·소비 이슈·SHA·무효 evidence 범위를 기록한다. G0 승인 전 후보 정정은 이번 `d0-03-v1-r3`처럼 revision을 올린다. 승인 후 의미가 바뀌는 필드/오류/기본값/소유/버전 조합은 `d0-03-v2` 이상으로 올리고 manifest 참조도 갱신한다. 의미 없는 오탈자만 v1 유지 가능하나 후보 SHA는 갱신한다. 영향받는 소비자와 독립 reviewer가 다시 검토하고 @DomineYH가 해당 후보의 G0 변경을 승인한다. 관련 없는 작업을 일괄 중지하지 않는다. 앱/DB/보안 공통 계약 변경은 해당 G1/G2/G3 및 G4/G5 증거 유효성을 다시 평가한다. 구현자가 자기 파일에서 계약값을 조용히 바꾸지 않는다.

## 28. 독립 검증·운영 인계 계약 (C29)

#203은 제품 코드 수정 없이 격리·보안·경합 검사를 독립 작성한다. #204는 실제 FastAPI+demo backend+`TESTING=false` session/CSRF middleware와 #197 seed를 쓰는 신규 helper `tests/demo_d4_02_server.py`를 단독 소유한다. #203은 helper를 읽기/호출하거나 자기 fixture로 독립 작성할 수 있고 완료를 기다리는 역방향 blocker를 만들지 않는다. 기존 `tests/browser_server.py` 화면 fixture/브라우저 interception만으로 실제 backend 통과를 주장하지 않는다.

필수 검증 matrix의 기대값:

| 검증 | 기대 결과 |
| --- | --- |
| 두 browser context A/B의 동일 seed/정수·UUID ID | A 제목/계정/대화/분석/CSV/reset 변화에도 B 목록·총계·상세·설정·실행/원장 불변. A scope+ID의 B 요청은 본문 없는 거절 |
| normal 부정 | demo route404, client mode 전환 불가, demo 모델 사용 거절; 기존 로그인/권한/분석/삭제·외부 제공자 규칙 유지 |
| AI 무호출 | constructor/transport/socket guard에서 localhost 서버 통신 외 외부 시도0. 학생/멘토/분석/retry/legacy/catalog/probe/비교직접요청/startup/cleanup 전부 포함 |
| 실제 CSRF/cookie | missing/invalid/cross-origin start·role·reset·삭제403, namespace/서명 위조 거절, 정상 서비스 쿠키를 demo에서 인증하지 않음 및 역방향도 동일 |
| reset race | stream/mentor/분석 chunk·commit·shield 원장 직전 barrier 양쪽 순서, old DB 재생성/새 DB write0. 중복 reset 단일 증가, 잠김·timeout·만료·restart·backlog 안전 상태 |
| 출력/재전송 | §15 모든 실패 case, 같은 ID 실행1회, 명시 retry 같은 turn·새 run, 미완료 본문 비승격, 실제 evidence, 정상 채택 보존 |
| 삭제/export | 교사 NULL 보존, 삭제 세션 모든 경로 비노출, CSV parse prefix/suffix·현재 자료·normal 호환, 다운로드 회수 보장 없음 |
| 실제 여정/복구 | A/B/C 각 완주·B 기본 경로, 6단계 실제 완료 근거, role/reset 두 탭·refresh/BFCache/언어·키보드·390px·200% 확대·중복 방지 |

#202 통합 manifest와 #205 릴리스 기록은 `candidate_commit`, `normal_commit`, `demo_commit`(동일 전체 SHA), `contract_version/contract_commit`, `content_version`, `fixture_version/manifest_sha256`, `db_schema_version/migration_set_sha256`, `seed_sha256/logical_data_sha256/생성명령`, secret 없는 `settings_sha256`, 검사 실행 SHA·명령/결과/증거 링크를 묶는다. settings hash는 비밀 값을 제외한 정규화 설정과 비밀 주입 **참조/버전만** hash한다. 비밀 자체 hash/값은 공개 증거에 넣지 않는다. 아직 없는 후보/배포 tuple 값은 실제 산출물 시 기록하며 만들어 내지 않는다.

#206은 G0 후 운영 초안을 쓰고 G5 승인 후보와 실제 결과를 반영한 뒤 확정한다. 문서는 별도 origin/host-only 쿠키, 전용 root/marker·운영 mount 부재, 단일 worker lock, §8 상한·TTL, cleanup 주기/잔량·잠김 처리·디스크 admission, health 200/503, seed/fixture 교체와 기존 공간 만료, secret 분리·egress 정책을 포함한다. D5-02 승인에는 실제 host/origin/URL/root/mount/용량/quota/최종 TTL·서비스/주입 권한/rollback 대상을 @DomineYH가 기록한다. 범위 검증 실패나 승인값 부재는 **배포 착수 차단**이며 G0 문서 초안의 blocker가 아니다.

리허설 실패 시 신규 start 차단·demo 중단·진행 작업 취소/drain/dispose 후 승인된 이전 동일 tuple로 복구한다. 첫 배포라 이전 tuple이 없으면 중단 상태를 정상 복구 목표로 기록한다. normal DB/설정은 변경하지 않는다. 공개는 별도 승인과 동일 후보의 smoke 뒤 수행하며 코드가 같다는 이유로 두 서비스를 자동 동시 갱신하지 않는다.

후속 구현 검사는 README의 targeted pytest/관련 integration을 우선하고 G5에서 고정 최종 후보의 전체 `uv run --frozen python -m pytest -q`, `uv run --frozen ruff check .`, `uv run --frozen black --check .`, `npm run test:browser`, `uv run --frozen python tests/check_student_live.py`와 normal/demo 검증을 연결한다. passed/failed/not run·이유/환경/SHA를 분리하고 승인 시 main 변경에 영향받은 증거를 재검사한다. 이번 문서 작성에서는 이 명령·앱·migration·브라우저·네트워크를 실행하지 않았으며 이 계약 자체는 격리/egress0/reset 안전성 증거가 아니다.

## 29. 후보 r2 변경 기록·재검토 범위

`d0-03-v1` 후보 `83f7ec0fff0895aed872114ad4e5d66ce3b38a5c`에 대한 1차 독립 검토 뒤, conditional_repair impl-190-r2로 **`d0-03-v1-r2`**를 작성했다. 아래는 설계 정정이며 구현/실행 증거가 아니다. 당시 수정 commit과 후속 재검토 결과는 G0 기록 §2–5에 고정했다. r2의 잔여 지적은 r3에서 해결되어 ACCEPT·maintainer 승인이 완료되었다. 기존79 소유 배정·ADR-0001/0005·CSV27/20+5·비교 제외·mode 전환 금지·session/CSRF 수명 분리는 유지한다.

| repair / 검토 finding | 규범·관찰 기대값 위치 | 영향을 받는 소비자·단독 구현 소유 |
| --- | --- | --- |
| R1 / DB-01 | §5.1 transition handle/mark/publish/fail/expire·commit-first/mark-first | #191 권위, #192/#193/#194/#198/#200 소비; #203 검증 |
| R2 / DB-02/V2/F1 | §5.1/6 guard(ctx)의 epoch·role/register/CRUD 양쪽 순서·동일역할 no-op | #191/#192/#193, #194/#198/#200 연결, #203/#204 검증 |
| R3 / DB-03 | §6 TaskControl/ExecutionToken/lease/normal=None·SSE/Task·drain 예 | #192 API, #194/#198/#200 소비, #203 검증 |
| R4 / DB-04 | §7.1 preparing/prepare_failed/expiry·quota/재시도/응답 유실 | #191/#193/#194, #199/#201 안내, #203/#204 검증 |
| R5 / DB-05 | §7.1/9.1/10 bootstrap unique/admission·loser409·cookie 안정 참조 | #191/#193/#194, #199/#201, #203/#204 |
| R6 / DB-06/V1 | §9.1 전체 replay/CSRF/status/Set-Cookie 표·과거 receipt 비회귀 | #193/#194, #199/#201 복구, #203/#204 검증 |
| R7 / DB-07/UI-1/UI-2/V3/F2 | §24.1/25 facts와 관찰·단일 대상·단조 map·저장/CSV 중단/refresh 예 | #193 facts, #199 최종 map, #200 export/삭제 facts, #201 이벤트, #202/#204 검사 |
| R8 / P1 | §15/17.1 exact lookup key/branch/trigger/phase·chunk projection | #196 selector/binder, #197 manifest, #198 전달, #203 검증 |
| R9 / P2 | §16.1 exact projection·679-byte canonical vector/digest·편집 예 | #195 공유 helper, #196/#197/#198 재사용, #203 검증 |
| R10 / F3 | §3 lease-before-User·거절 해제 | #194 resolver, #191/#192 lease, #203 검증 |

r1 리뷰에서 위 인터페이스/여정에 걸린 미결 판정은 재검토로 해소해야 하며 r1의 긍정 검토를 r2 승인으로 재사용하지 않는다. G1/G2/G3의 source API 및 G4/G5 검사 입력은 새 계약 버전을 소비한다. G0 기록의 1차 판정은 보존한다. r2 재검토 결과는 G0 §3.2에 기록하고 후속 S1–S5 정정 및 완료된 r3 재검토는 아래 §30과 G0 기록 §3.3–5에 기록한다.

## 30. 후보 r3 변경 기록·재검토 범위

후보 `e0b8cc38f491e1178f8d39c7fe7bacedd02bcf76`(`d0-03-v1-r2`)의 다섯 재검토 결과는 모두 ACCEPT_WITH_FIXES다. 기존 R1–R10의 정정은 보존하고, 마지막 자동 conditional_repair impl-190-r3에서 아래 다섯 공백만 정정해 **d0-03-v1-r3**로 올렸다. r3 후보 `d6455d10c2d75acde811f85a53143be086d67545`의 다섯 lens 재검토는 ACCEPT이며 @DomineYH G0 승인은 완료되었다(G0 기록 §3.3–5).

| repair / finding | 규범·관찰 기대값 위치 | 영향 소비자·구현 소유 |
| --- | --- | --- |
| S1 / DB-08 | §5/5.1/6 handle 안/밖 retain·release 구분, 유효 등록 token1/child lease1·중복 순증0·재진입 대기 없음 | #191 lease 권위, #192 registry, #198 소비, #203 검증 |
| S2 / DB-09/V4 | §6/7/7.2 reset 자기 DB/lease 조기 해제 후 OperationToken 이관. idle reset 성공, 별도 live lease는 DRAIN_TIMEOUT, disconnect 후 결과 보존 | #193 Task/orchestration, #194 요청 DB handoff/정리, #191/#192 수명, #203/#204 검증 |
| S3 / P3 | §17.1 모든 finding의 owned 근거≥1, non-changed는 모두 owned, changed의 overlap 조건. 공통 턴 chunk의 overlap-only maintained 제외·유효 changed 보존·제외로 인한 검증 오류0 | #196 projection, #197/#198 정상 fixture 기대값, #203 검증 |
| S4 / P4/F4 | §17.1 MergeAnalysisOutput의 정확한 여섯 key 및 chunk의 literal 필드명. message_classifications는 merge에 없음, validator 불변 | #196/#197/#198, #202/#203/#205 phase 호환 검증 |
| S5 / UI-3 | §24.1 단일/선택 export의 같은 echo/row-count, S1-only 관찰 인정·혼합은 다운로드만 허용·기대 map | #200 응답, #201 선택 검증/event, #199 최종 map, #202/#204 검증 |

79개 기존 소유·고정 입력·ADR 원칙·CSV27/20+5·cookie 분리·mode 전환 금지·비교 제외는 바꾸지 않았다. 신규 제품 구현/실행 증거가 아니라 계약 문구 정정이다. 실제 r3 후보에 대한 소비자·독립 재검토 완료와 승인 기록은 G0 기록 §3.3–5를 따른다.


## 31. D0 마감 변경 후보 — C14/C27 정정

§27 변경관리에 따라 승인된 `d0-03-v1-r3`에서 `d0-03-v2`로 C14/C27을 정정한다. 확인 기준 main은 `427890033d206a4f043ffc946009bd8ce6a437c2`다. 승인된 입력·후보 SHA와 G0 기록 §1–5는 변경하지 않는다. 기존 승인은 취소하지 않으며 새 변경 승인은 아래 정확한 후보에 별도로 기록한다.

- 이유/변경: §14/26이 파서 `src/utils/openai_helpers.py`와 저장 보고서 reader `src/utils/session_feedback.py`를 SDK 직접 생성 경계로 잘못 분류했다. 두 파일의 불필요한 SDK guard 패치 요구만 제거한다. 실제 SDK/admission/execution 차단과 외부 fallback 금지, 방문객 DB·접근/삭제 보호는 유지한다
- C-item/소비자: C14/C27, #198 및 독립 검증 #203/#205. C03의 `s6_credentials.py` 조사 보완은 이미 승인된 #194 소유·CLI 차단을 재확인하며 배정을 바꾸지 않는다
- 무효 evidence 범위: v1-r3의 ACCEPT·승인은 그 정확한 후보에 보존한다. 이를 이 v2 변경의 승인 증거로 재사용하지 않는다. §14/26 패치 필요성 판단은 영향 소비자와 독립 reviewer가 변경 후보 전체 SHA로 다시 검토해야 한다. 다른 인터페이스·오류·기본값·보안 불변식의 의미는 바꾸지 않는다
- SHA/결과: 실제 변경 후보 전체 SHA·PR·검사 결과는 게시 후 #182/#190에 기록한다. 자기 참조 SHA를 만들지 않는다. 영향 소비자·지정 독립 reviewer 재검토, @DomineYH의 정확한 v2 후보 G0 변경 승인, 이후 대상 branch 통합 확인 전에는 이 변경을 승인된 구현 입력으로 승격하지 않는다
- manifest: 후속 구현에서 v2를 소비하려면 위 승인 후 `contract_version/contract_commit`을 함께 갱신한다. 이 문서 정리에서는 제품·manifest·D1 구현·배포를 수행하지 않는다

### 31.1 정확한 변경 후보 승인

- 승인 대상: 입력/main `427890033d206a4f043ffc946009bd8ce6a437c2`, `d0-03-v2` 변경 후보 `e9610b532216ff29259e0bd274883c19693c1166`, PR #213의 문서 4개
- 검토: 작성자와 별도 보조 독립 검토 ACCEPT, 지적 없음. 지정 `agy/gemini-4-argon/high` 실행은 불가하여 maintainer가 이번 문서 변경에 한해 이 보조 검토로 대체하는 workflow 예외를 명시 승인했다. 지정 도구를 실행했다고 주장하지 않는다
- 승인: @DomineYH, 2026-10-19 UTC, [승인 기록](https://github.com/DomineYH/misconcept_platform_v2/pull/213#issuecomment-6257186149). C14/C27 개정안 및 CI 통과 후 PR 병합·#182 완료 처리 승인. 제품 코드·D1·배포 승인으로 확대하지 않는다
- 후속 상태: 이 승인 상태 기록만 반영하는 후속 head도 정확한 CI를 확인한다. 최종 head/통합 SHA·post-merge CI 결과는 #182/PR #213에 기록하며 통합 확인 전 완료 처리하지 않는다
