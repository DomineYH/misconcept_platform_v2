# AI 연결·모델 화면 계약 (#41 → #42–#55)

`GET /admin/ai`는 기존 관리자 인증으로 템플릿만 반환한다. 화면 진입,
새로고침, 재접속은 제공자를 호출하거나 시험을 시작하지 않는다.
`static/js/ai-connections.js`가 아래 API를 소비한다. A2는 연결 조회와 키 저장/교체/활성 변경/삭제를 실제 DB에 연결한다. 모델/시험/설정과 SDK 호출은 후속 티켓 범위다. 없는 API는 안전한 설정 불가 안내를 표시한다.
합성 상태는 `tests/browser_ai_*.mjs`의 Playwright 응답에만 존재한다.

## 읽기: `GET /admin/ai/state`

비밀 없는 JSON 객체. A2의 아직 제공되지 않은 기능은 `models: []`,
`models_available: false`, `settings: null`, 각 `catalog.available: false`로
반환한다. 화면은 해당 작업을 비활성화하고 준비 중 안내를 표시한다.
연결 API 상태 조회는 `Cache-Control: no-store`이며 관리자 인증이 없으면
401, 교사면 403이다. 마스터 키 오류도 조회를 막지 않는다.

- `master_key_available`: 마스터 키가 저장/호출 가능한 상태인지 boolean.
- `providers`: openai/anthropic/google 각 연결의 배열.
  `provider`, `connection_version`, `credential_revision`, `key_registered`,
  `masked_hint` (끝 4자 이하), `enabled`,
  `status` (unconfigured/ready/decryption_failed), `verified_at` (nullable),
  `error_code` (nullable, 허용된 안전 코드), `impact` (비밀 없는 설명 문자열 배열).
  `catalog`: `available`, `stale`, `fetched_at` (nullable), `models` (`model_id` 객체 배열).
  서버가 TTL 24시간, 이전 credential revision, 조회 실패를 반영해 stale을
  계산한다. 기존 성공 목록은 실패 시 유지한다. 확인 시각과 목록 시각은 별개다.
  `impact`는 현재 역할 설정/작성 기본값/활성 호출의 영향이다. 민감 작업 전에
  보여 주며 변경 서버는 expected_version과 실제 참조를 다시 확인해야 한다.
- `models`: `id` (정수), `provider`, `model_id`, `display_name`, `enabled`,
  `config_version`, `capabilities` (정의 없으면 null), `default_options`,
  `verification_state`, `probe_budgets`.
  역할 키는 student/mentor/analysis. 각 검증 객체는 `status`
  (unverified/verifying/succeeded/failed/stale), 선택적 `verified_at`,
  `error_code`, 진행 중이면 `probe_request_id`를 포함한다. 서버는 검증의
  credential/connection/capability/role-contract 버전 유효성을 반영한 상태를
  반환한다. 성공을 클라이언트가 목록 등록에서 추측하지 않는다.
  `probe_budgets[role]`은 호출별 출력 상한이며 해당 역할 계약과 모델 기본값/
  기능 한도에 맞춘 서버 계산 값이다.
- `settings`: `settings_version`, `defaults`, `limits`, `timeouts`.
  `defaults[role]`은 null 또는 `{model_config_id, available}`.
  사용 불가 참조도 보존한다. `limits` 키는 total/openai/anthropic/google/admin.
  초기값은 8/4/4/4/3. `timeouts` 키는 connect/student_first_output/
  student_total/mentor_first_output/mentor_total/analysis_total/model_list_total.
  초기 초 단위 값은 5/60/180/60/180/300/30.

### 옵션 필드

`capabilities.fields`는 서버의 공식 기능 정의 스냅샷에서 만든 허용 필드만
담는다. 각 필드: `name`, `label`, `type` (integer/number/string), 선택적
`min`, `max`, `choices` (문자열 enum 배열). name은 max_output_tokens,
지원되는 temperature, reasoning.effort, thinking.budget_tokens처럼 서버가
허용한 옵션 경로다. 내부 예약 객체 키를 경로로 허용하지 않는다.
화면은 이 정의만 편집하며 capabilities 자체를 제출하지 않는다.

빈 옵션은 생략하고 숫자 0은 보존한다. 점 경로는 중첩 JSON으로 제출한다.
서버는 타입/범위/모델별 옵션 조합/기능 정의와 목록 메타데이터 불일치를
최종 검증한다. UI 제약은 서버 검증을 대체하지 않는다. ID는 등록 후 불변이다.

## 쓰기 및 시험

아래 경로는 모두 `/admin/ai/` 기준, JSON POST다. 모든 쓰기는 기존
관리자 권한·CSRF(`x-csrf-token`)를 서버에서 확인한다.

| 경로 | 요청 | 의미 |
| --- | --- | --- |
| providers/{provider}/key | expected_version, current_password, api_key | 저장/교체만 수행; 목록/시험 자동 실행 없음 |
| providers/{provider}/enabled | expected_version, current_password, enabled | 매 요청 재인증; 활성화만으로 검증 복원 금지 |
| providers/{provider}/delete | expected_version, current_password | 키 재료만 삭제; 모델/과거 기록 보존 |
| providers/{provider}/catalog | expected_version | 비생성 확인 및 수동 목록 갱신 |
| models | provider, model_id, display_name | ID trim 후 등록, 초기 비활성·미검증 |
| models/{id}/update | expected_version, display_name, enabled, default_options | 모델 ID/capabilities 변경 불가 |
| models/{id}/probes | expected_version, role, request_id | 명시적 시작; 202 응답 |
| probes/{request_id}/cancel | {} | 명시적 중단 요청; 실제 과금 중단 보장 없음 |
| settings/update | expected_version, defaults, limits, timeouts | defaults[role]은 모델 정수 ID/null; 비밀번호 불필요 |

일반 쓰기는 2xx JSON(예: `{status:"saved"}`) 또는 204.
시험 예약은 `{request_id, status:"verifying"}`를 반환한다.
`GET /admin/ai/probes/{request_id}`는 비밀·출력 본문 없는
`{request_id, status}`를 반환한다. 진행 중 상태의 명시적 조회로 재접속을
지원한다. 같은 request_id 재전송은 기존 시험을 반환하며 새 호출을 만들지
않는다. 새로운 시험 확인 화면을 여는 명시적 동작만 새 UUID를 만든다.
시험 묶음은 최대 2회 생성, 자동 재시도 0회이며 첫 실패 후 다음 단계 없음.
학생은 텍스트/스트리밍, 멘토는 판단 JSON/코칭, 분석은 분류/종합 JSON이다.

서버는 401/403/409/422/429를 구분한다. UI는 이 HTTP 상태에 대한 고정
안내만 표시하고 raw detail/validation input/SDK 예외를 표시하지 않는다.
원문 비밀/암호문/nonce/비밀번호는 읽기 응답·초기 HTML·로그·CSV에 넣지 않는다.
키·비밀번호 입력은 매 제출 즉시 및 finally에서, 닫기·pagehide에서 지운다.
URL/브라우저 저장소에는 기록하지 않는다. 성공 후 상태를 다시 읽으며
변경 충돌 시 조용히 재제출하거나 버전을 바꾸지 않는다.

## 후속 구현의 책임

화면만으로 인증·재인증 제한·암호화·감사·버전 경쟁·호출 한도·원장·역할
검증을 구현했다고 간주하지 않는다. 후속 서버 티켓이 명세 #40을 적용해야
한다. 기능 정의 없는 모델 시험과 활성화, 다른 역할만 성공한 기본값,
잘못된 옵션과 제한, 잘못된 마스터 키의 저장/생성을 반드시 서버에서도 막는다.
현재 선택된 사용 불가 기본값은 참조를 그대로 표시하며 자동 대체하지 않는다.
재접속 시 시험의 기존 상태를 복원하고 검증 중 프로세스 재시작은
failed/interrupted로 정리한다. A1은 운영 전환을 승인하지 않는다.

## A2 연결 저장 계약

`025_provider_connection.sql`은 세 연결 행을 비밀 없이 만들고
`provider_audit_log`를 추가한다. 초기 credential_revision=0,
connection_version=1이며 ID는 키 삭제/재등록 후에도 유지한다.
키 저장/교체/삭제는 credential_revision을 증가시키고 모든 성공한 변경은
connection_version을 증가시킨다. 역할 검증은 이후 티켓에서 이 버전들을
비교해야 하며 재활성화만으로 이전 성공을 복원하지 않는다.

비활성화와 삭제는 복호화 성공에 의존하지 않는다. 키 교체는 기존 키를
복호화할 수 있어야 하므로 손상/다른 마스터 키/버전 불일치는 원래 키·버전
복원을 안내한다. 복원이 불가능하면 재인증 삭제 후 재등록한다.
재활성화도 저장 키의 복호화 성공을 요구하며 실패하면 변경 없이 안전한
503을 반환한다. 활성/비활성 변경 성공 시 verified_at과 error_code를 비운다.
API는 원문 키를 다시 조회하는 기능을 제공하지 않는다. 8자 이하 입력은
일반 마스킹이며 그보다 긴 키는 끝 4자만 힌트로 보인다.

재인증 실패 제한과 감사 행은 서버 책임이다. 기존 limits의 메모리 이동
윈도우로 관리자 ID와 클라이언트 주소별 각각 5분/5회 실패를 제한하며
성공 요청은 실패 횟수를 소비하지 않는다. 429는 Retry-After: 300을 포함한다.
권한·CSRF·매 요청 비밀번호 검증 후 expected_version을 조건부 갱신한다.
키 변경과 비밀 없는 감사 행은 같은 트랜잭션에서 커밋하며 감사 실패도 전체
롤백한다. 입력은 엄격한 버전 정수/활성 boolean/비밀 문자열이며 추가 필드를
거부한다. 422는 고정 코드만 반환하고 validation input을 반환하지 않는다.
마스터 키 오류/저장 장애는 안전한 503이며 SQL 예외를 반환하거나 기록하지 않는다.

마스터 키 형식은 표준 base64로 인코딩한 32바이트이며 별도의
PROVIDER_SECRET_ENCRYPTION_KEY_VERSION이 필요하다. 암호화는
`src/services/provider_secrets.py`의 cryptography AES-256-GCM, 새 12바이트
nonce, UTF-8 JSON 배열 `[provider, connection_id, credential_revision]`
(compact separators) 인증 데이터다. decrypt_key는 잘못된 마스터 키/버전,
태그/nonce/revision 손상을 ProviderSecretUnavailableError로 차단한다.
이 복호화 도구는 활성/역할 검증/호출 예약을 대신하지 않는다. 이후 호출
티켓은 연결/역할/버전 검사를 함께 수행하고 원문 비밀을 요청 범위에서만
사용해야 한다. Python 메모리의 완전한 영점 삭제를 보장하지 않는다.

A2에는 모델/작성 기본값/DB 키로 시작한 활성 호출 참조가 없으므로 impact는
빈 배열이다. 해당 저장·호출 티켓이 실제 참조를 이 필드에 채워야 한다.
활성 호출 취소는 A6, 기존 수업 호출의 DB 자격 증명 전환은 A12–A15의
출시 게이트다. 기존 환경 키 호출을 이번 연결 화면의 결과로 오해하지 않는다.
백업/복구 절차는 README의 Provider connection setup and recovery를 따른다.
