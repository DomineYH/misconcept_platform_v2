# AI 연결·모델 화면 계약 (#41 → #42–#55)

`GET /admin/ai`는 기존 관리자 인증으로 템플릿만 반환한다. 화면 진입,
새로고침, 재접속은 제공자를 호출하거나 시험을 시작하지 않는다.
`static/js/ai-connections.js`가 아래 API를 소비한다. A1에는 이 데이터/쓰기
API, DB 모델, SDK 연결이 없다. 없는 API는 안전한 설정 불가 안내를 표시한다.
합성 상태는 `tests/browser_ai_*.mjs`의 Playwright 응답에만 존재한다.

## 읽기: `GET /admin/ai/state`

비밀 없는 JSON 객체:

- `master_key_available`: 마스터 키가 저장/호출 가능한 상태인지 boolean.
- `providers`: openai/anthropic/google 각 연결의 배열.
  `provider`, `connection_version`, `credential_revision`, `key_registered`,
  `masked_hint` (끝 4자 이하), `enabled`,
  `status` (unconfigured/ready/decryption_failed), `verified_at` (nullable),
  `error_code` (nullable, 허용된 안전 코드), `impact` (비밀 없는 설명 문자열 배열).
  `catalog`: `stale`, `fetched_at` (nullable), `models` (`model_id` 객체 배열).
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
