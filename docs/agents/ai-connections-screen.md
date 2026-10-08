# AI 연결·모델 화면 계약 (#41 → #42–#55)

`GET /admin/ai`는 기존 관리자 인증으로 템플릿만 반환한다. 화면 진입,
새로고침, 재접속은 제공자를 호출하거나 시험을 시작하지 않는다.
`static/js/ai-connections.js`가 아래 API를 소비한다. A2는 연결 조회와 키 저장/교체/활성 변경/삭제를 실제 DB에 연결한다. A3는 OpenAI 비생성 목록·모델 등록/편집·singleton 설정을 연결한다. A5는 OpenAI 학생 역할 시험과 시도 원장을, A7은 멘토/분석 시험을 연결한다. 없는 API는 안전한 설정 불가 안내를 표시한다.
합성 상태는 `tests/browser_ai_*.mjs`의 Playwright 응답에만 존재한다.

## 읽기: `GET /admin/ai/state`

비밀 없는 JSON 객체. A7은 `models_available: true`, `probes_available: true`,
`probe_roles: ["student", "mentor", "analysis"]`,
DB의 모델 배열과 singleton `settings`를 반환한다. 초기 모델은 없고 역할
기본값은 모두 null이다. OpenAI만 `catalog.available: true`이며 나머지 목록과
그 제공자의 역할 시험 버튼은 비활성화한다. singleton이 없는 미설치 DB의 설정은 null이다.
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
  `probe_budgets[role]`은 min(역할 상한, 저장된 출력 상한, 모델 기능 상한)이다.
  역할 상한은 student=1024, mentor=1500, analysis=2500이다.
  기능 정의/옵션이 유효하지 않으면 빈 객체이며 시험 버튼도 비활성화한다.
  후속 `probe_budgets[role]`도 해당 역할 계약과 모델 기본값/기능 한도에
  맞춘 호출별 서버 계산 값이어야 한다.
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
시험 예약은 `{request_id, status:"verifying", error_code:null}`를 반환한다.
`GET /admin/ai/probes/{request_id}`는 비밀·출력 본문 없는
`{request_id, status, error_code}`만 반환한다. 진행 중 상태의 명시적 조회로 재접속을
지원한다. 같은 request_id 재전송은 기존 시험을 반환하며 새 호출을 만들지
않는다. 새로운 시험 확인 화면을 여는 명시적 동작만 새 UUID를 만든다.
예약된 ID는 owner별 유일하며 model_config_id/role/expected_version의
fingerprint와 결합한다. 같은 ID/입력은 진행/성공/실패/오래된 결과 모두
202로 재생하며 다른 입력은 409다. 예약 전 401/403/404/409/422/503 거부는
ID를 결합하지 않으므로 올바른 입력으로 다시 예약할 수 있다. A6의 슬롯
429도 같은 예약 전 의미를 따라야 한다. UI는 예약 실패 후 동일 ID를 유지한다.
상태/취소는 해당 owner인 관리자만 접근한다. 다른 관리자의 상태는 404이며
상태 응답은 `Cache-Control: no-store`다.
`model_probe.owner_id`는 NOT NULL/ON DELETE RESTRICT로 유지한다.
시험 기록이 있는 사용자 삭제는 안내와 함께 거부해 owner/요청 ID의 유일성을
보존한다. 원장의 nullable owner_id와는 별개다.
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
미확인 모델/옵션은 추측하지 않고 차단한다. Mini 공식 페이지는 더 새로운
모델을 권장하며 이 안내는 접근 보장이 아니다. 기존 모델을 다른 ID로
자동 대체하지 않는다.

### A5 학생 시험과 원장 계약

학생 역할 계약 `s1-v1`은 고정된 합성 물의 상태 질문과 한국어 한 문장
지시로 Responses 일반 텍스트와 스트리밍을 순차 호출한다. 수업 입력은
사용하지 않는다. 저장된 옵션을 검증한 뒤 출력 예산만 위 상한으로 낮추며
`store=false`, SDK/app 재시도 0회다. 첫 호출 실패 시 스트림을 시작하지 않는다.
학생/멘토의 초기 역할 버전은 유지하며 이 입력/검증 계약 변경 시 올린다.

`TextRequest`는 provider/model_id/role/system_instruction/messages/
validated_options/request_id를 가지며 비밀은 별도 인수로 요청 동안만
복호화한다. 공통 이벤트는 text_delta/usage와 completed/refused/interrupted/
error 중 하나의 종료다. 추론 delta는 본문/첫 표시 시간에 포함하지 않는다.
EOF, 미지 상태, 빈 본문, output_limit, 거절은 성공이 아니다. 상태 API/원장은
본문·프롬프트·전체 SDK 응답을 반환하거나 저장하지 않는다.

호출마다 시작 시 현재 시간 제한을 읽는다. 기본 연결 5초, 스트림 첫 표시
60초, 각 호출 전체 180초이며 일반 텍스트는 첫 표시 타이머가 없다. 변경은
이후 호출에만 적용한다. 명시적 취소/시간 초과는 SDK HTTP를 닫고 안전한
종료를 기록한다. 서버 시작 시 verifying 시험과 running 새 원장은 interrupted로
정리하며 자동 재생성하지 않는다. 역할 최종화는 키/연결/모델/정의/계약
버전을 확인하고 새 역할 증거를 덮어쓰지 않는다. 공통 슬롯과 연결 철회 정책은 아래 A6 계약을 따른다.

Migration 027은 기존 api_usage_log의 ID/세션/토큰/비용을 그대로 보존한다.
새 행은 invocation_id/request_id, nullable run_id/session_id/owner_id,
attempt_no/provider/model/role/operation/credential_revision, status/error_code,
started_at/first_output_at/finished_at/retry_wait_ms, input_tokens/output_tokens/
cache_read_tokens/cache_write_tokens/reasoning_tokens/total_tokens,
raw_usage_json/estimated_cost_usd/pricing_as_of/pricing_source/usage_complete를
사용한다. probe의 하위 호출은 학생 text/stream, 멘토 judgment/coaching,
분석 classification/synthesis이며 각자 invocation과
attempt_no=1을 갖는다. 가짜 세션/턴/run은 만들지 않는다. 외부 호출 전 running
행을 별도 트랜잭션으로 커밋하고 실패하면 호출하지 않는다. 최종화는 running
조건부 갱신으로 한 번만 수행한다. 목록은 operation=model_list, model/role=null로
생성 시도와 구분하며 A4 화면의 생성 비용 합계에서 제외한다.
실제 재시도 대기가 없으면 retry_wait_ms는 NULL이다.

NULL은 알 수 없음, 0은 관측한 0이다. 누적 usage는 합산하지 않고 최신 관측
필드를 병합한다. 캐시는 입력, reasoning은 출력의 부분집합이며 중복 가산하지
않는다. OpenAI가 보고하지 않은 cache_write는 NULL이다. raw_usage_json에는
검증된 토큰 숫자만 저장한다. 새 호출의 비용/단가 출처/기준일은 A10 공식
단가 연결까지 NULL이며 다른 모델 가격으로 대체하지 않는다. 과거 행은
invocation_id=NULL인 채 기존 관측값을 유지한다. A4 화면은 양쪽을 표시한다.

기존 수업 학생/멘토/분석 경로는 A12–A14까지 기존 설정을 사용한다.
FastAPI/Google용 HTTPX와 OpenAI SDK용 HTTPX2 구분을 유지한다.
이 단계는 운영 전환 승인이 아니다.


### A6 호출 승인·취소·시간 제한 계약

목록과 생성은 `call_admission.admit_call` → `call_execution.execute_call`을
공유한다. 시험의 첫 슬롯은 예약 트랜잭션 안에서 `approve_call`로 승인하고
즉시 태스크에 넘긴다. 이후 하위 호출은 슬롯을 다시 승인한다. 슬롯은 외부
시도 단위이며 DB/SDK 정리 뒤 반환한다. 앱 인스턴스 하나·비동기 워커 하나가
전제다. 프로세스 내부 `active_probes`/슬롯/활성 호출 레지스트리를 여러 워커가
공유하지 않는다. 큐·Redis·다중 워커 지원은 없다.

기본 전체 8/제공자별 4/관리자 작업 전체 3이며 관리자 목록도 작업 한도에
포함한다. 관리자 호출은 전체와 제공자 한도에서 일반 호출 한 자리를 남긴다.
전체/제공자 한도는 ≥2, 관리자 1–3 및 전체보다 작아야 한다. 관리자당 verifying
시험 묶음은 한 건이며 같은 모델·역할의 중복 예약도 제한한다. 한도를 낮춰도
기존 호출은 유지하고 현재 수가 줄 때까지 새 승인을 거부한다. 슬롯 부족 시
대기 없이 **429 `detail.code=call_limit_reached`, `Retry-After: 1`**이다.
사전 거부는 시험/비용 원장을 만들지 않으며 같은 request_id로 재시도할 수 있다.
두 번째 하위 호출이 거부되면 기존 시험은 failed/call_limit_reached로 끝난다.

409 코드는 **request_conflict**(같은 관리자/request_id에 다른 입력),
**version_conflict**(expected_version 불일치), **probe_in_progress**(같은 모델·역할의
진행 시험)다. 동일 입력/request_id 재전송은 슬롯 상태와 무관하게 기존 202
결과를 반환한다. 상태 조회와 취소는 owner 기준이며 다른 관리자의 시험은 404다.
완료 후 취소는 **idempotent no-op**으로 결과/원장을 바꾸지 않고
`{"status":"cancel_requested"}`를 반환한다. 모든 쓰기는 CSRF/관리자 권한을
검사하고 연결 변경은 매번 현재 비밀번호를 확인한다.

승인·활성 등록·연결 변경은 같은 짧은 잠금을 사용하며 SDK를 기다리는 동안
DB 쓰기 잠금을 보유하지 않는다. 외부 호출 직전에 활성/시험·모델 검증 버전을
재확인한다. 교체 전에 승인된 일반 호출은 확보한 이전 키로 완료할 수 있다.
이전 시험의 결과는 새 revision을 검증하지 못한다. 비활성화·삭제는 커밋 뒤
같은 승인 잠금 안에서 모든 revision(슬롯 반환 중 backoff 포함)에 중단을
요청하고 새 호출을 차단한다. 교체·비활성화·삭제·재활성화의 역할 증거는
현재 버전과 대조하여 stale로 표시하며 재활성화만으로 성공을 복원하지 않는다.
연결 영향 목록은 등록 모델·작성 기본값·현재 활성 호출 수를 표시한다.
취소 안내: ‘중단을 요청했습니다. 제공자 처리 및 이미 발생한 비용은 취소되지
않을 수 있습니다.’ 로컬 HTTP/클라이언트/슬롯 정리는 upstream 과금 취소 보장이 아니다.

`CallDeadline`이 앱의 전체/첫 표시 본문 타이머를 소유한다. 전체 시간은 승인
시각부터 원장 준비·모든 시도·backoff를 포함하는 절대 deadline이다. 각 시험
하위 호출은 새 승인 시각/역할별 설정을 읽는다. connect는 SDK transport timeout,
첫 표시 본문은 student/mentor 스트림에만 적용하며 추론·빈 delta는 제외한다.
non-stream 텍스트/분석/목록에는 첫 본문 타이머가 없다. 이미 승인된 호출의
설정 스냅샷은 설정 수정으로 바뀌지 않는다. SDK max_retries=0이며 앱 재시도는
학생/멘토/개입 판단/인사/시험/목록/향후 비교 0회, **비관리자 analysis 역할의
classification/synthesis만 최대 1회**다. 일시 연결 오류/408/409/일시429/5xx만
대상이고 본문 수신 뒤 실패·검증 실패·quota·권한·전체 deadline·사용자 취소는
재시도하지 않는다. Retry-After는 초/HTTP 날짜를 지원하며 음수/잘못된 값/누락은
1초다. 남은 시간에 대기가 들어가지 않으면 재시도하지 않는다. backoff 때 슬롯을
반환하고 현재 한도/연결/검증을 다시 승인하며 슬롯 재획득 거부는 대기하지 않는다.

`execute_call`의 이벤트를 `aclosing`으로 소비한다. 현재 SDK dispatch는 OpenAI만 지원하며 다른 제공자의 키를 OpenAI로 전송하지
않고 원장/SDK 시작 전에 차단한다(A8/A9가 별도 어댑터를 연결한다). 일반 호출은 정확한
model_config_id/config_version/역할 성공으로 승인하고 요청 모델·제공자·역할을
승인 정보와 대조한다. 종료 이벤트 뒤에는 원장 최종화가 끝나 있다. 동일 logical
invocation의 재시도 행은 invocation_id를 공유하고 attempt_no=1,2를 가진다.
첫 시도 retry_wait_ms는 NULL이며 실제 재시도 대기가 있으면 두 번째 행에
대기 값을 기록한다(0초 Retry-After로 실제 재시도하면 0). 모델 목록의 내부
완료 이벤트만 정규화된 models를 가지며 공개 시험 DTO는 변경하지 않는다.
시작/최종화 DB 실패에는 비밀 없는 configuration_unavailable 및 안전한 로그만
남기고 새 호출을 만들지 않는다. 최종화 DB 장애로 남은 running은 다음 부팅에서
interrupted/usage unknown으로 정리한다. 취소 직전 시작 트랜잭션도 짧게 정리한
뒤 HTTP/슬롯을 닫는다. 재시작은 verifying/running을 정리할 뿐 재호출하지 않는다.
기존 수업 경로의 실제 전환은 A12–A14이며 여기서는 모의 일반 호출로 여유를 검증한다.

## A7 OpenAI 멘토·사후 분석 검증 계약

기존 예약·상태 조회·취소 API에 role=mentor/analysis를 허용한다. UUID 재전송,
관리자당 한 묶음, 최대 두 호출, 첫 실패 중단, 자동 재시도 0회는 학생과 같다.
화면은 역할별 상한과 계약, 비용 가능성, 교육적 품질 보증이 아님을 먼저 표시한다.
모델 활성화에는 한 역할 이상의 유효 성공이 필요하지만 실제 역할 사용에는
해당 역할의 성공이 필요하다. 재시험은 해당 역할만 verifying으로 교체하며 실패가
이전 성공을 복원하지 않는다. 지원 범위 안 기본 옵션 변경은 성공을 유지하고
자격 증명·연결·기능 정의·역할 계약 변경은 stale로 표시한다.

`StructuredRequest`는 기존 `TextRequest`에 Pydantic 모델인 `output_schema`와
서버용 `validation_context`를 추가한다. `execute_call(..., kind="structured")`는
OpenAI `generate_structured`를 호출하고 서버 검증 이후에 원장을 최종화한다.
성공 `CallEvent.structured`에는 검증된 객체만, `text`에는 빈 문자열이 들어간다.
실패에는 객체나 부분 JSON을 반환하지 않는다. 공개 probe/state 응답과 원장은
출력 본문을 저장·반환하지 않는다. 수업 호출 전환은 A13/A14 범위다.

`structured_output.strict_schema`는 Pydantic JSON Schema의 닫힌 object,
primitive, array, $defs/$ref, enum/const, 중첩 anyOf를 strict text.format으로
변환한다. 모든 property를 required로, 객체를 additionalProperties=false로
만들고 default를 제거한다. Optional은 nullable 스키마를 사용한다. 열린
딕셔너리·extra=allow·tuple·임의 Any·미지원 스키마 키워드·루트 union/array는
SDK 생성 전에 configuration_unavailable로 거부하며 JSON 프롬프트로 우회하지 않는다.
지원 구조의 근거는 [OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)
(2026-10-09 확인)이다. 서버 Pydantic·의미 검증은 전송의 strict 보장을 신뢰해 생략하지 않는다.

`role_output_contracts`의 s1-v1 모델은 현재 필드 계약을 보존한다.

- InterventionJudgment: is_repetitive/is_inappropriate는 boolean, reason은 문자열.
  개입 판단이 true이면 비어 있지 않은 근거가 필요하다.
- QuestionClassification: label, confidence(0~1), DetailedReasoning 기반
  summary/improved_sentence. context.labels는 {label: level}; 존재하지 않는
  label을 거부한다. low에는 개선 질문, 다른 level에는 null이 필요하다.
- SessionSynthesis: brief_feedback/strengths/improvements/dialogue_coaching.
  context.messages는 {id, role, content} 배열. ID·역할·원문 인용을 확인하고
  70자 피드백·60자 대안 질문, 유효 marker를 검사한다. 비어 있는 핵심/상세
  결과는 실패한다. 기존 분석 fixture의 합성 ID=100(teacher, Why?), 101(student)를
  사용한다. 기존 lesson의 보정·degraded·보존 정책은 변경하지 않는다.

구문 오류는 invalid_json, 잘못된 label/ID/역할/인용은 invalid_reference,
타입·범위·계약 오류는 invalid_output, 필요한 빈 결과는 empty_response다.
refused/output_limit은 제공자 종료 상태에서 구분하며 usage는 실패해도 보존한다.
상태의 역할 계약 버전은 ROLE_CONTRACT_VERSIONS에서 읽는다. 새 교육 동작이나
출력 계약을 도입하는 후속 티켓은 버전을 올려 기존 증거를 stale로 만들어야 한다.
