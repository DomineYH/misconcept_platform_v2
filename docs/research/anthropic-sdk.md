# 조사: Anthropic Messages API · Python SDK 사실 (어댑터 계약용)

- 이슈: #11 (부모 #23, 차단 대상 #17). PRD 근거 W3·W4·W8, 관련 절 4.3–4.4, 7.4, 9.1–9.4.
- 기준 SDK: `anthropic` **1.12.1** (PyPI 2026-10-08 업로드, tag `v1.12.1`). Python 3.10+, HTTP 계층은 `httpx2`.
- 확인일: **2026-10-08**. 출처는 공식 문서(platform.claude.com)와 SDK 저장소 소스뿐. 유료 API 호출은 하지 않음.
- 표기: **[문서]** = 공식 문서 문장, **[소스]** = SDK 코드에서 직접 확인, **[추론]** = 위 두 근거에서 도출한 설계 함의, **UNCONFIRMED** = 1차 출처로 확인 못 함.

출처 약어
- `ML` https://platform.claude.com/docs/en/api/models/list
- `MO` https://platform.claude.com/docs/en/about-claude/models/overview (Using the Models API)
- `ST` https://platform.claude.com/docs/en/build-with-claude/streaming
- `SR` https://platform.claude.com/docs/en/build-with-claude/handling-stop-reasons
- `RF` https://platform.claude.com/docs/en/build-with-claude/refusals-and-fallback
- `SO` https://platform.claude.com/docs/en/build-with-claude/structured-outputs
- `TH` https://platform.claude.com/docs/en/build-with-claude/thinking
- `TS` https://platform.claude.com/docs/en/build-with-claude/thinking-steering-and-cost (구 adaptive-thinking URL이 여기로 연결)
- `PC` https://platform.claude.com/docs/en/build-with-claude/prompt-caching
- `MA` https://platform.claude.com/docs/en/api/messages (요청 파라미터 레퍼런스)
- `ER` https://platform.claude.com/docs/en/api/errors
- `PY` https://platform.claude.com/docs/en/cli-sdks-libraries/sdks/python
- `GH` https://github.com/anthropics/anthropic-sdk-python/blob/v1.12.1/ (아래 경로는 이 기준)
- `CL` https://github.com/anthropics/anthropic-sdk-python/blob/v1.12.1/CHANGELOG.md

---

## 1. 모델 목록 API (W3, PRD 4.3)

**엔드포인트·페이지네이션**
- `GET /v1/models`. 최신 출시 모델이 먼저 나온다. [문서 `ML`]
- 커서 방식: `after_id`, `before_id`, `limit`(기본 20, 1–1000). 응답은 `data[]`, `first_id`, `last_id`, `has_more`. 다음 페이지는 `after_id=last_id`. [문서 `ML`]
- SDK: `client.models.list(...)`는 `SyncPage[ModelInfo]`를, 비동기는 `AsyncPaginator[ModelInfo, AsyncPage[ModelInfo]]`를 반환한다. `for`/`async for`로 전체 페이지를 자동 순회하고, `.has_next_page()`, `.next_page_info()`, `.get_next_page()`로 직접 넘길 수도 있다. [문서 `PY` Auto-pagination, 소스 `GH src/anthropic/resources/models.py`]
- 단건 조회: `GET /v1/models/{model_id}` → `client.models.retrieve(id)`. [문서 https://platform.claude.com/docs/en/api/models/retrieve]
- `anthropic-workspace-id` 헤더(선택)는 여러 Workspace에 걸친 자격증명일 때만 필요하다. [문서 `ML`]
- `lifecycle` 필터: 값은 `active`/`deprecated`/`retired`, 최대 3개. **생략하면 active와 deprecated가 나오고 retired는 명시해야 나온다.** [소스 `GH src/anthropic/resources/models.py` docstring, `CL` 1.12.0 "add lifecycle stage fields and filter to /v1/models"] 공식 API 레퍼런스 페이지(`ML`)에는 2026-10-08 기준 아직 반영되지 않았다.

**`ModelInfo` 메타데이터** [문서 `ML`, 소스 `GH src/anthropic/types/model_info.py`]
- `id`, `type="model"`, `display_name`, `created_at`(출시 시각, 모르면 epoch 값일 수 있음)
- `max_input_tokens`(컨텍스트 창), `max_tokens`(`max_tokens` 파라미터 상한). 둘 다 nullable. `context_window`라는 필드는 없다.
- `line`: `haiku|sonnet|opus|fable|mythos|null`. "do not infer a line from the `id`".
- `lifecycle`(필수), `deprecated_at`, `retires_at`: SDK 1.12.0 이후 타입. 문서 페이지에는 아직 없다(위 참고).
- `capabilities`(nullable; 있으면 알려진 모든 키가 항상 들어 있음): `batch`, `citations`, `code_execution`, `context_management{...}`, `effort{low,medium,high,xhigh?,max,supported}`, `image_input`, `pdf_input`, `server_tools{web_search,code_execution}`, `structured_outputs`, `thinking{supported, types{adaptive, disabled, enabled}}`.
- `thinking.types.disabled.supported`는 "False exactly when a request that sends it gets a 400 from this model". [문서 `ML`, `MO`]

**메타데이터만으로는 알 수 없는 것** [추론, 근거 `ML`·`TH`·`MA`] → PRD 4.3의 "작은 기능 정의"가 필요하다
- `temperature`/`top_p`/`top_k` 지원 여부를 나타내는 capability 필드가 없다.
- 모델별 effort **기본값**이 없다(Opus 5.5와 Haiku 5.5는 `medium`, 대부분은 `high`. `TS`).
- `"disabled"`가 effort `high` 이하에서만 허용되는 조건(Opus 5, Haiku 5.5)을 표현하지 못한다. Sonnet 5.5의 `thinking.type="between_tools"`는 `types`에 키 자체가 없다.
- 스트리밍 지원 여부를 나타내는 필드가 없다(모든 Messages 모델이 지원한다고 가정할 수 있다는 문서 문장도 찾지 못함 → UNCONFIRMED).

## 2. 스트리밍 이벤트 (W4, PRD 9.2)

**이벤트 순서** [문서 `ST` Event types]
1. `message_start`: `content`가 빈 `Message`. `stop_reason`은 `null`.
2. 블록마다 `content_block_start` → `content_block_delta`(1회 이상) → `content_block_stop`. `index`는 최종 `content` 배열의 위치.
3. `message_delta`(1회 이상): 최상위 변경. **여기서만 `stop_reason`이 온다.** [문서 `SR` Streaming considerations]
4. `message_stop`(마지막).
- `ping`은 아무 때나 몇 번이든 올 수 있다. 알 수 없는 이벤트 타입은 "handle gracefully"해야 한다(버전 정책상 추가될 수 있음). [문서 `ST`]
- `message_delta.usage`의 토큰 수는 **누적값**이다. [문서 `ST` Warning, 소스 `GH src/anthropic/types/message_delta_usage.py` "cumulative"]

**delta 타입** [문서 `ST`, `TH`]
- `text_delta`(`.text`), `input_json_delta`(`.partial_json`, 도구용), `thinking_delta`(`.thinking`), `signature_delta`(thinking 블록의 `content_block_stop` 직전).
- 현행 모델의 thinking `display` 기본값은 `"omitted"`이다: thinking 블록이 열리고, 빈 `thinking_delta` 하나와 `signature_delta` 하나가 온 뒤 닫힌다. 텍스트를 보려면 `display:"summarized"`를 명시한다. 원문 사고 과정은 어떤 설정에서도 반환되지 않는다. [문서 `TH` Controlling thinking display]
- adaptive 모드에서는 모델이 사고를 건너뛸 수 있으므로 **thinking 블록이 없는 턴**도 있다. [문서 `TS`, `TH`]
- [추론] PRD 9.2의 "원문 추론 이벤트를 TTFT에 넣지 않는다"는 첫 `text_delta` 시점을 TTFT로 잡으면 된다. `omitted`이면 서버가 thinking 토큰 스트리밍을 생략해 첫 텍스트가 더 빨리 온다. [문서 `TH`]

**`stop_reason` 값** [문서 `SR`, 소스 `GH src/anthropic/types/stop_reason.py`]
- `end_turn` · `max_tokens` · `stop_sequence` · `tool_use` · `pause_turn` · `refusal` · `model_context_window_exceeded` (SDK `StopReason` Literal과 문서 목록이 정확히 일치함)
- `model_context_window_exceeded`: Sonnet 4.5 이후 모델은 헤더 없이 반환하지만 SDK에서는 **beta 타입에만** 들어 있다고 문서에 적혀 있다. 그러나 1.12.1의 비-beta `StopReason`에도 이미 들어 있다(문서가 뒤처진 것으로 보임). [문서 `SR`, 소스 위 경로]

**거절(refusal)** [문서 `RF`, `SR`]
- 거절은 오류가 아니라 **HTTP 200 + `stop_reason:"refusal"`** 이다. `stop_details{type:"refusal", category, explanation}`가 함께 온다. 다른 stop_reason에서는 `stop_details=null`.
- `category`는 `cyber|bio|frontier_llm|reasoning_extraction|general_harms|null`(열린 집합). `explanation` 문구는 안정적이지 않으므로 파싱하지 말고 표시만 한다. 분기는 `stop_reason`으로 한다.
- **스트림 중간 거절**: 부분 출력 뒤에 올 수 있으며, 부분 출력은 "incomplete, discard"로 다룬다.
- 과금: 출력 전 거절은 `bio`·`frontier_llm`·`reasoning_extraction`만 과금하고(2026-09 기준, 바뀔 수 있음) 나머지와 `null`은 과금하지 않는다. 중간 거절은 입력과 이미 스트리밍된 출력을 과금한다.
- 같은 모델로 재전송하면 대개 다시 거절된다. 대안은 다른 모델 재시도, 또는 서버측 `fallbacks`(beta: `server-side-fallback-2026-07-01`, Claude API 전용)다.
- 대상 모델: Fable 5/5.1, Opus 5/5.5, Sonnet 5.5, Haiku 5.5에 분류기가 있다.

**스트림 중 오류** [문서 `ST` Error events, `ER`]
- 200 응답 뒤에 `event: error`(예: `overloaded_error`, 비스트림이라면 529에 해당)가 올 수 있다. 이 경우 일반적인 HTTP 오류 처리 경로를 따르지 않는다.
- [소스 `GH src/anthropic/_streaming.py`, `src/anthropic/_client.py::_make_status_error`] SDK는 이를 `_make_status_error(..., response=<200 응답>)`로 올린다. 상태코드가 200이므로 **`RateLimitError`나 `OverloadedError` 같은 서브클래스가 아닌 기본 `APIStatusError`(status_code=200)** 가 발생한다. 오류 종류는 `e.body["error"]["type"]`로 판별해야 한다. [추론]
- 끊긴 스트림 복구: 4.6 이후 모델은 assistant prefill 대신 "부분 응답 + 계속하라는 user 메시지"로 재요청한다. thinking과 tool_use 블록은 부분 복구가 안 된다. [문서 `ST` Error recovery] (PRD 9.4는 본문 송출 후 자동 재생성을 금지하므로 참고만.)

## 3. 구조화 출력 (W8, PRD 7.4)

- **공식 방식(GA, beta 헤더 없음)**: `output_config={"format":{"type":"json_schema","schema":{...}}}`. 결과는 text 블록에 담긴 JSON이다. 제약 디코딩(constrained decoding)을 쓴다. [문서 `SO`]
- 다른 축으로 `strict: true` 도구(입력 스키마를 보장)가 있고, 둘을 같은 요청에서 함께 쓸 수 있다. tool 기반 우회는 필요 없다. [문서 `SO`]
- 구 `output_format` 파라미터는 deprecated이고 `structured-outputs-2025-11-13` 헤더가 없으면 400이다. SDK 1.x의 `beta.messages.create(output_format=...)`는 `TypeError`. [문서 `SO` Migrating from the beta]
- SDK 헬퍼 `client.messages.parse(output_format=PydanticModel)`는 스키마를 변환해 `output_config.format`으로 보내고, 응답을 검증해 `parsed_output`을 돌려준다. [문서 `SO` Usage] [추론] PRD는 서버에서 동일 Pydantic으로 최종 검증하므로, 어댑터는 raw `output_config.format` + 자체 검증 방식이 제공자 간 일관성에 유리하다.
- 지원 모델(`SO` frontmatter): Opus 4.5–5.5, Sonnet 4.5–5.5, Haiku 4.5/5.5, Fable·Mythos 등. 런타임에는 `capabilities.structured_outputs`. [문서 `SO`, `ML`]
- **스키마 제약** [문서 `SO` JSON Schema limitations]
  - 객체는 `additionalProperties:false`가 필수다. 재귀 스키마, 외부 `$ref`, 숫자 제약(`minimum`/`maximum`/`multipleOf`), 문자열 길이 제약, `minItems`(0·1 외)는 미지원이며 400을 낸다.
  - `enum`은 원시값만, `anyOf`/`allOf`는 제한적, 정규식은 lookaround·역참조·`\b` 미지원.
  - 복잡도 한도: strict 도구 20개, optional 파라미터 총 24개, union 타입 파라미터 16개. 초과 시 "Schema is too complex for compilation"(400). 컴파일 타임아웃은 180초.
  - SDK 헬퍼는 미지원 제약을 제거하고 description에 옮겨 적은 뒤 응답을 원 스키마로 재검증한다. [문서 `SO` How SDK transformation works]
- **출력이 스키마와 어긋날 수 있는 경우** [문서 `SO` Invalid outputs] → PRD 7.4의 "거절·길이 초과·빈 응답·스키마 오류" 구분에 그대로 대응
  - `refusal`: 200이고 과금되며, 스키마와 불일치할 수 있다.
  - `max_tokens`: 잘린 JSON이 올 수 있다.
  - `enum`/`const`의 **대소문자는 보장되지 않는다**(정상 종료로 옴). 대소문자를 무시하고 비교해야 한다.
  - 프로퍼티 순서: required가 먼저 오고 optional이 뒤에 온다.
- **경고 (PRD 7.4 스키마 설계에 영향)**: 모델의 thinking이나 단계별 reasoning을 요구하는 프로퍼티(`reasoning`, `thinking`, `trace` 같은 필드)는 `reasoning_extraction` 거절을 부를 수 있다. 대신 "짧은 설명·근거"를 요구한다. [문서 `SO`, `RF` Keep reasoning in thinking blocks] → `misconception_findings`의 근거 필드 이름과 지시문에 주의.
- 첫 사용 시 문법 컴파일 지연이 있고 컴파일 결과는 24시간 캐시된다. `output_config.format`을 바꾸면 프롬프트 캐시가 무효화된다. 추가 시스템 프롬프트가 자동 주입되어 입력 토큰이 늘어난다. [문서 `SO`]
- 스트리밍과 함께 쓸 수 있다("Stream structured outputs like normal responses"). citations와 prefill은 함께 쓸 수 없다(400). 문법은 최종 출력에만 적용되고 thinking에는 적용되지 않는다. [문서 `SO` Feature compatibility]

## 4. 취소 · 재시도 · 타임아웃 (PRD 9.3–9.4)

**SDK 자동 재시도** [문서 `PY` Retries, `ER`; 소스 `GH src/anthropic/_constants.py`, `_base_client.py::_should_retry`]
- 기본 `max_retries=2`, 지수 백오프(초기 0.5s, 최대 8s). 대상은 연결 오류, 408, 409, 429, 5xx(529 포함)이고 **타임아웃도 재시도**한다.
- 서버 헤더 `x-should-retry: true|false`가 상태코드 규칙보다 우선한다. `retry-after-ms` → `retry-after`(초) → `retry-after`(날짜) 순으로 대기 시간에 반영한다.
- `Anthropic(max_retries=0)` 또는 `client.with_options(max_retries=0)`이면 끈다. PRD 9.4의 "단일 계층 소유"는 이 설정으로 충족된다.
- 429 중 tier 월 지출 한도로 인한 것은 `retry-after` 헤더가 없고, 접근이 재개될 때까지 계속 실패한다 → 재시도 대상에서 빼야 한다. [문서 `ER`]
- [소스/추론] 재시도는 요청·응답 헤더 단계에서만 일어난다. 스트림을 순회하다 생긴 오류(위 2절 `event: error`, 읽기 타임아웃)는 SDK가 재시도하지 않는다.

**예외 계층** [소스 `GH src/anthropic/_exceptions.py`, 문서 `PY` Handling errors]
- `APIError` ⊃ `APIStatusError` ⊃ {`BadRequestError` 400, `AuthenticationError` 401, `PermissionDeniedError` 403, `NotFoundError` 404, `ConflictError` 409, `RequestTooLargeError` 413, `UnprocessableEntityError` 422, `RateLimitError` 429, `OverloadedError` 529, `InternalServerError` ≥500, ...}
- `APIConnectionError` ⊃ **`APITimeoutError`**. 타임아웃을 연결 오류와 구분하려면 `APITimeoutError`를 먼저 잡아야 한다.
- 응답 객체의 `_request_id`(공개 속성)는 로그 용도다.

**타임아웃** [문서 `PY` Timeouts / Long requests, 소스 `_constants.py`]
- 기본값은 `httpx2.Timeout(timeout=600, connect=5.0)`. `timeout=float` 또는 `httpx2.Timeout(total, read=, write=, connect=)`으로 바꾸고, 요청 단위로는 `with_options(timeout=...)`.
- 시간이 초과하면 `APITimeoutError`가 나고 **기본 2회 재시도**된다. 따라서 벽시계 시간은 최대 timeout×(retries+1)까지 늘 수 있다.
- 비스트림 요청이 10분을 넘을 것으로 예상되면 SDK가 `ValueError`를 낸다(`stream=True` 또는 timeout을 명시하면 해제된다). 큰 `max_tokens`에는 스트리밍을 권장한다. TCP keep-alive는 SDK가 설정한다.
- [추론] httpx 계열 Timeout은 connect/read/write/pool **단계별** 한도라서 "첫 본문까지"나 "요청 전체" 한도는 없다. 게다가 `ping` 이벤트가 읽기 타이머를 리셋하므로 read timeout으로는 첫 `text_delta` 지연을 잡을 수 없다. PRD 9.4의 "연결·첫 본문·전체 구분"은 **connect = SDK 설정, 첫 본문·전체 = 앱의 `asyncio.timeout`** 으로 구현해야 한다. httpx2가 httpx와 같은 시맨틱인지는 문서로 확인하지 못했다 → UNCONFIRMED.

**스트림 취소** [소스 `GH src/anthropic/_streaming.py`, `src/anthropic/lib/streaming/_messages.py`; 문서 `PY` Streaming helpers]
- 두 경로: `client.messages.stream(...)`(컨텍스트 매니저, `text_stream`, `get_final_message()`, `current_message_snapshot`, 누적 제공)과 `client.messages.create(..., stream=True)`(raw 이벤트만, 메모리 적음).
- `Stream.close()` / `AsyncStream.close()`(`await`)는 HTTP 응답을 닫아 연결을 해제한다. `with`/`async with`를 빠져나오거나 순회 중 예외가 나도 `finally`에서 응답이 닫힌다.
- [추론] 클라이언트 disconnect 시 FastAPI 핸들러가 `async with` 블록을 벗어나게만 하면 업스트림 연결이 정리된다.
- **연결을 끊으면 서버측 생성과 과금이 즉시 멈추는지는 공식 문서에서 찾지 못했다 → UNCONFIRMED.** 문서가 확인해 주는 것은 "중간 거절 시 이미 스트리밍된 출력이 과금된다"는 점뿐이다. PRD 9.3처럼 "정확히 한 번 과금"을 보장하지 않는다는 전제를 유지해야 한다.

## 5. 사용량 필드 (PRD 8.4, 9.1 `usage`)

- `usage.input_tokens`: **마지막 캐시 브레이크포인트 이후** 토큰만 센다(캐시를 읽지도 쓰지도 않은 부분). [문서 `PC` Tracking cache performance]
- `cache_creation_input_tokens`(이번에 캐시에 쓴 양)와 `cache_read_input_tokens`(캐시에서 읽은 양)는 `input_tokens`와 **겹치지 않는다**. [문서 `PC`]
  - 총 입력 = `cache_read_input_tokens + cache_creation_input_tokens + input_tokens` [문서 `PC`]
  - `cache_creation{ephemeral_5m_input_tokens, ephemeral_1h_input_tokens}`의 합이 `cache_creation_input_tokens`와 같다. [문서 `PC`]
  - 두 캐시 필드가 모두 0이면 캐시되지 않은 것이다(최소 길이 미달은 오류 없이 조용히 무시됨). [문서 `PC`]
  - SDK에서 캐시 필드는 `Optional[int]`이므로 `None`은 0으로 다룬다. [소스 `GH src/anthropic/types/usage.py`]
- `output_tokens`는 **thinking 토큰을 포함한** 과금 기준 총량이다. `display`가 omitted든 summarized든 과금은 같다(요약 생성은 무료). `output_tokens_details.thinking_tokens` ≤ `output_tokens`는 관측용 분해값이다. [문서 `TS`, 소스 `usage.py` docstring]
- 스트리밍에서는 `message_start.message.usage`가 초기값(입력 측)이고 `message_delta.usage`가 **누적** 최종값이다. `output_tokens_details`는 마지막 `message_delta`에만 온다. [문서 `ST`, `TS`]
- 그 밖의 필드: `server_tool_use`, `service_tier`, `inference_geo`. [소스 `usage.py`]
- 거절 응답에도 `usage`는 채워진다(출력 전 거절 예: `output_tokens: 0`). [문서 `RF`]

## 6. `temperature` / `thinking` / effort 허용 규칙 (PRD 4.4)

**샘플링 파라미터** [문서 `TH` Limits › Sampling parameters, `MA` temperature]
- **Fable 5/5.1, Mythos 5/5.1/Preview, Opus 4.7/4.8/5/5.5, Sonnet 5/5.5, Haiku 5.5에서는 기본값이 아닌 `temperature`·`top_p`·`top_k`가 thinking 사용 여부와 상관없이 항상 400이다.** `MA`는 `temperature`를 deprecated로 표시하고 "1.0만 하위호환으로 허용"한다고 적는다.
- 구형 모델(Opus 4.6, Sonnet 4.6, Haiku 4.5 등)은 thinking이 켜져 있을 때만 제한된다: `temperature`·`top_k`는 thinking과 함께 쓸 수 없고, `top_p`는 0.95–1만 허용된다.
- `temperature` 범위는 0.0–1.0, 기본값 1.0. [문서 `MA`]
- 주의: `MA`의 문구 "Models released after Claude Opus 4.6"와 `TH` 목록은 Sonnet 4.6 포함 여부가 엇갈린다. `TH` 목록에는 Sonnet 4.6이 없다 → Sonnet 4.6의 thinking-off temperature 허용은 `TH` 기준이며, 실측 전까지 UNCONFIRMED.

**`thinking` 값별 허용표** [문서 `TH` Configuring thinking. "≤high"는 effort low/medium/high에서만 허용되고 xhigh/max는 400이라는 뜻]

| 모델 | 생략 | adaptive | enabled+budget | between_tools | disabled |
|---|---|---|---|---|---|
| Opus 5.5 | adaptive | O | 400 | 400 | 400 |
| Sonnet 5.5 | adaptive | O | 400 | ≤high | 400 |
| Haiku 5.5 | adaptive | O | 400 | 400 | ≤high |
| Opus 5 | adaptive | O | 400 | 400 | ≤high |
| Sonnet 5 | adaptive | O | 400 | 400 | O |
| Fable 5/5.1, Mythos 5/5.1 | adaptive | O | 400 | 400 | 400 |
| Opus 4.7/4.8 | off | O | 400 | 400 | O |
| Opus 4.6 / Sonnet 4.6 | off | O | O (deprecated) | 400 | O |
| Opus 4.5 / Sonnet 4.5 / Haiku 4.5 | off | 400 | O | 400 | O |

- `display`는 `type:"disabled"`와 함께 보내면 invalid다. `between_tools`에는 다른 필드를 붙일 수 없다. [문서 `TH`]
- thinking 토큰은 `max_tokens`에 포함된다. `budget_tokens`(구형)는 `max_tokens`보다 작아야 하며 최소 1024. [문서 `TH`, https://platform.claude.com/docs/en/build-with-claude/extended-thinking]
- thinking이 켜져 있으면 prefill을 쓸 수 없다. 강제 `tool_choice`(`any`/`tool`)는 Opus 5.5, Sonnet 5.5, Fable 5.1, Mythos 5.1에서 **항상 400**이다. [문서 `TH` Response prefill and forced tool use] → 구조화 출력은 tool 강제 대신 `output_config.format`을 쓴다.
- **effort**: `output_config.effort`(`low|medium|high|xhigh|max`, 최상위 파라미터가 아님). 기본값은 대부분 `high`이고 Opus 5.5와 Haiku 5.5는 `medium`. 모델별 허용 레벨은 `capabilities.effort.*`. [문서 `TS` Effort levels, `ML`]
- [추론] 어댑터의 `validate_model_and_options()`에 필요한 것: (a) 샘플링 파라미터 허용을 담은 정적 표(Models API에 없음), (b) thinking 타입과 effort의 조합 규칙(위 표), (c) Models API의 `capabilities.thinking.types` / `effort` / `structured_outputs`와 교차검증. 표에 없는 모델 ID는 **미검증**으로 두고 옵션을 거부한다(PRD 4.3–4.4와 일치).

## 7. 어댑터 계약 요약 (PRD 9.1 매핑)

| 공통 이벤트/결과 | Anthropic 근거 |
|---|---|
| `text_delta` | `content_block_delta` + `delta.type=="text_delta"` (thinking·signature·input_json delta는 사용자 출력에서 제외) |
| `completed` | `message_stop` 수신 + `stop_reason ∈ {end_turn, stop_sequence}` |
| 길이 초과 | `stop_reason ∈ {max_tokens, model_context_window_exceeded}` → 잘린 결과로 표시하고 성공으로 위장하지 않음 |
| `refused` | `stop_reason=="refusal"`(200). 부분 출력은 폐기, `stop_details.category`를 기록 |
| `usage` | 최종 `message_delta.usage`(누적) + `message_start` 입력 측. 총 입력은 세 필드의 합 |
| `interrupted` | `message_stop` 없이 스트림 종료/연결 오류/앱 타임아웃 |
| `error` | 요청 단계 `APIStatusError` 서브클래스·`APIConnectionError`·`APITimeoutError`. 스트림 중에는 `APIStatusError(status_code=200)` + `body.error.type` |
| 기타 | `tool_use`·`pause_turn`은 도구 미사용 범위(PRD 9.1)에서 예상 밖 → `error`로 처리 |

## UNCONFIRMED 목록
1. 스트림 연결을 끊었을 때 서버측 생성과 과금이 즉시 멈추는지 여부.
2. `httpx2.Timeout`이 httpx와 같은 단계별 시맨틱(read = 청크 간 유휴)인지 여부(httpx2 공식 문서 미확인).
3. Models API 목록에 있는 모든 모델이 스트리밍을 지원한다는 명시적 문장.
4. Sonnet 4.6에서 thinking이 꺼진 상태로 `temperature≠1`이 허용되는지(`MA`와 `TH`의 문구가 엇갈림).
5. `lifecycle`/`deprecated_at`/`retires_at`/`lifecycle` 필터: SDK 1.12.x 소스와 changelog로만 확인했고 공식 API 레퍼런스 페이지에는 아직 없다.
