# AI 연결·모델 화면 계약 (#41 → #42–#55)

`GET /admin/ai`는 기존 관리자 인증으로 템플릿만 반환한다. 화면 진입,
새로고침, 재접속은 제공자를 호출하거나 시험을 시작하지 않는다.
`static/js/ai-connections.js`가 아래 API를 소비한다. A2는 연결 조회와 키 저장/교체/활성 변경/삭제를 실제 DB에 연결한다. A3는 OpenAI 비생성 목록·모델 등록/편집·singleton 설정을 연결한다. 역할 시험은 A5 이후 범위다. 없는 API는 안전한 설정 불가 안내를 표시한다.
합성 상태는 `tests/browser_ai_*.mjs`의 Playwright 응답에만 존재한다.

## 읽기: `GET /admin/ai/state`

비밀 없는 JSON 객체. A3는 `models_available: true`, `probes_available: false`,
DB의 모델 배열과 singleton `settings`를 반환한다. 초기 모델은 없고 역할
기본값은 모두 null이다. OpenAI만 `catalog.available: true`이며 나머지 목록과
역할 시험 버튼은 비활성화한다. singleton이 없는 미설치 DB의 설정은 null이다.
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
  A3의 `probe_budgets`는 빈 객체다. 후속 `probe_budgets[role]`은 호출별 출력 상한이며 해당 역할 계약과 모델 기본값/
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

A3의 impact는 등록 모델과 현재 역할별 작성 기본값 참조를 표시한다.
DB 키로 시작한 생성 호출은 아직 없으며 활성 호출 참조는 A6가 추가한다.
활성 호출 취소는 A6, 기존 수업 호출의 DB 자격 증명 전환은 A12–A15의
출시 게이트다. 기존 환경 키 호출을 이번 연결 화면의 결과로 오해하지 않는다.
백업/복구 절차는 README의 Provider connection setup and recovery를 따른다.

## A3 목록·모델·설정 저장 계약

`026_model_settings.sql`은 연결 행에 목록 캐시를 추가하고 `model_config`,
`app_setting`을 만든다. 이전 연결 ID/암호문/revision과 수업 기록은 보존한다.
singleton은 id=1, settings_version=1이며 모델은 자동 등록하지 않는다.
역할 기본값/한도/시간의 초기값은 위 읽기 계약과 같다.

OpenAI 연결 확인과 갱신은 동일한 `providers/openai/catalog` POST다.
복호화한 DB 키로 요청별 `AsyncOpenAI(max_retries=0)`를 열고 닫는다.
`list_models`는 SDK 공개 비동기 반복자를 끝까지 소비한다. 고정 SDK 3.26.0의
Models API는 현재 pagination 없는 `AsyncPage`다. 지원하지 않는 cursor를
추측해 요청하지 않는다. 응답 읽기/항목 검증이 끝난 뒤 목록 전체를 한 번에
커밋한다. 실패·취소·revision/connection_version 충돌은 기존 목록을 덮어쓰지
않는다. 현재 버전의 실패는 안전한 오류와 verified_at=null을 저장하며 기존
목록/갱신 시각/등록 모델/역할 상태는 보존한다. 목록 누락도 모델을 삭제하지 않는다.
연결 비활성·키 교체/삭제는 늦은 결과를 409로 차단한다.

캐시는 `model_id`, `created`, `owned_by`, nullable `shutdown_date`만 보관한다.
[공식 Models 목록 API](https://developers.openai.com/api/reference/resources/models/methods/list)는
출력 토큰/temperature/스트리밍 제한을 제공하지 않으므로 목록 ID나 prefix로
기능을 추론하지 않는다. 명시된 shutdown_date가 지났으면
`capabilities.metadata_conflict=true`를 표시해 활성화/새 기본값/후속 시험·실행을
차단한다. 목록이 없다는 사실은 불일치가 아니다.

성공한 비생성 확인은 connection_version을 올리지 않는다. 따라서 목록 갱신은
유효 역할 검증을 무효화하거나 실패를 성공으로 복원하지 않는다. TTL은 24시간이며
캐시 revision이 현재 키와 다르거나 확인 실패/복호화 실패이면 참고 목록으로
표시한다. 외부 대기 동안 DB 트랜잭션을 유지하지 않는다. 시작 시 읽은
connect/model_list_total 설정을 HTTPX2 connect timeout/전체 asyncio deadline에
적용하며 진행 중 변경은 다음 조회부터 적용한다. SDK 원문 오류/디버그 헤더는
공개 응답이나 로그에 넣지 않는다.

모델 등록은 `model_id` trim 후 빈 값/제어문자/잘못된 UTF-8을 거부한다.
연결+ID 중복은 409, ID/capabilities/verification_state 편집 시도는 422다.
표시명/활성/옵션 수정은 모델 config_version CAS로 충돌을 409로 반환한다.
정의 없는 모델은 빈 옵션으로 비활성 표시명 수정만 가능하다. 활성화는 현재
연결의 유효 역할 성공 하나 이상을 요구한다. 지원 범위 안 옵션 변경은 정적
검증만 요구하며 역할 성공을 새로 만들지 않는다. 모델 삭제 경로는 없다.

`validate_model_and_options(provider, model_id, options)`는 후속 어댑터가 재사용할
정적 검증 경계다. 정의 없는 ID는 빈 옵션이라도 차단한다. 반환 값은 입력의
유효 옵션을 보존하며 잘못된 필드/타입/범위/조합은 ValueError다. HTTP에서는
안전한 422로 바꾼다. 등록/비활성 표시명 수정만 이 실행 검사를 생략한다.

`effective_roles`는 저장된 구조와 credential_revision, connection_version,
capability_definition_version, role_contract_version을 검사한다. 버전 불일치는
stale이며 연결 재활성화로 복원되지 않는다. `ROLE_CONTRACT_VERSIONS`의 초기값은
역할별 s1-v1이다. A5/A7이 실제 시험 계약을 정하고 변경 시 올린다.
새 기본값은 활성 모델/연결과 지정한 역할의 현재 성공을 요구한다.
기존 사용 불가 참조는 보존하며 다른 설정 수정이나 명시적 null 해제는 허용한다.
singleton 설정은 settings_version CAS, 엄격한 정수/필드, 관리자 예약 여유와
첫 본문≤전체 시간을 검증한다. 별도 비밀번호 확인은 없다.

### OpenAI 기능 표

확인일 **2026-10-09**, 정의 버전 **openai-2026-10-09-v1**.
서버 코드 `src/services/model_capabilities.py`가 원본이며 등록/편집 시 DB
capabilities_json에 스냅샷을 저장한다. 읽기는 현재 정의를 반환하고 이전 정의
버전으로 얻은 역할 성공은 stale로 처리한다. 클라이언트가 표를 제출할 수 없다.

| 정확한 ID와 공식 snapshot | 텍스트/스트리밍/구조화 | max_output_tokens | reasoning.effort | temperature |
| --- | --- | --- | --- | --- |
| gpt-5-mini, gpt-5-mini-2025-08-07 | 모두 지원 | 1–128000 정수 | minimal/low/medium/high, 생략 기본 medium | 미지원 |
| gpt-5.2, gpt-5.2-2025-12-11 | 모두 지원 | 1–128000 정수 | none/low/medium/high/xhigh, 생략 기본 none | 0–2 숫자, effort=none일 때만 |

모델 기능/최대 출력/정확 snapshot은
[GPT-5 Mini 모델 문서](https://developers.openai.com/api/docs/models/gpt-5-mini),
[GPT-5.2 모델 문서](https://developers.openai.com/api/docs/models/gpt-5.2)를 따른다.
Mini effort는 [GPT-5 가이드의 기존 모델 절](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5),
sampling 조합은 [GPT-5.4 가이드의 parameter compatibility 절](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.4)을 따른다.
temperature 0–2 범위는 고정 공식 SDK의 Responses 요청 정의에도 명시돼 있다.
top_p/logprobs/verbosity/thinking 및 다른 모델 변형은 이번 허용 표에 없다.
미확인 모델/옵션은 추측하지 않고 차단한다. Mini 공식 페이지의 Deprecated
표시는 접근 보장이 아니며, 기존 모델을 다른 ID로 자동 대체하지 않는다.

A3의 생성 시험은 API/버튼 모두 사용할 수 없고 `probe_budgets={}`다.
호출 슬롯·시도 원장·취소를 목록에도 붙이는 작업은 A5/A6가 맡는다.
기존 학생/멘토/분석 SDK 전송 테스트만 HTTPX2로 바꾸며 FastAPI와 Google용
HTTPX는 유지한다. 이번 단계로 기존 수업의 DB 연결 전환을 승인하지 않는다.
