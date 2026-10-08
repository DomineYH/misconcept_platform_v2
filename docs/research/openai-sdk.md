# 조사: OpenAI Responses API · `openai` Python SDK 사실 (issue #10)

- 대상 버전: `openai` **3.26.0** (PyPI 업로드 2026-10-06, 커밋 `4e152cd`). 확인일: **2026-10-08**.
- 출처: 공식 문서(developers.openai.com), SDK 저장소 소스·README·CHANGELOG만. 커뮤니티 글은 근거로 쓰지 않음.
- 확인하지 못한 것은 **UNCONFIRMED**로 표시.
- 소스 링크 접두어: `SDK` = `https://github.com/openai/openai-python/blob/v3.26.0/`

## 0. 먼저 알아야 할 것: 현재 저장소와의 버전 차이

| 항목 | 현재 저장소 | 최신 SDK |
|---|---|---|
| `pyproject.toml` | `openai>=1.68.0` | — |
| `uv.lock` 고정 | `2.7.1` | `3.26.0` |
| HTTP 클라이언트 | `httpx` | **`httpx2`** (3.0.0부터 기본, `httpx`는 더 이상 설치되지 않음) |

- 3.0.0(2026-08-12)의 BREAKING 변경: HTTPX2가 기본 HTTP 클라이언트가 되고 `httpx`는 자동 설치되지 않는다. 커스텀 `http_client`/transport/`httpx.Timeout`을 쓰는 코드는 `httpx2` 쪽으로 옮겨야 한다. — `SDK/CHANGELOG.md` (3.0.0), `SDK/httpx2.md`
- 기본 클라이언트만 쓰면 호출·스트리밍·재시도·숫자 타임아웃은 그대로 동작한다. 다만 **TLS 신뢰 저장소가 `certifi`에서 OS 저장소로 바뀐다**. CA가 없는 최소 컨테이너에서는 `SSL_CERT_FILE`/`SSL_CERT_DIR`이 필요하다. — `SDK/httpx2.md`
- 2.0.0(2025-09-30)의 BREAKING 변경은 함수 도구 출력 타입 하나뿐이다. 텍스트·구조화 출력 경로와는 관계없다. — `SDK/CHANGELOG.md` (2.0.0)
- 저장소는 `httpx>=0.25.0`을 직접 의존성으로 선언하고 있다. 테스트에서 `httpx`로 SDK를 mock한다면 3.x로 올릴 때 영향이 있을 수 있다(미검토).
- 의미: 어댑터 구현(#17)에서 `openai`를 **`>=3.26,<4`로 고정**할지, 2.x에 남을지 정해야 한다. 아래 사실은 3.26.0 기준이다.

## 1. 모델 목록 (`list_models`)

- `client.models.list()` → `GET /v1/models` → `SyncPage[Model]`. — `SDK/src/openai/resources/models.py`
- **실제 페이지네이션은 없다.** `SyncPage`의 docstring: "no pagination actually occurs yet, this is for forwards-compatibility". `next_page_info()`는 항상 `None`이다. — `SDK/src/openai/pagination.py`
- API 레퍼런스에도 쿼리 파라미터(`after`/`limit`)가 문서화되어 있지 않다. 응답은 `{object: "list", data: [Model]}`이다. — https://developers.openai.com/api/reference/resources/models/methods/list
- 그래도 `for m in client.models.list()`처럼 반복하면 나중에 페이지네이션이 생겨도 코드가 깨지지 않는다(SDK auto-paginating iterator). — `SDK/README.md#pagination`
- `Model` 필드는 다음이 전부다: `id`, `created`(unix 초), `object="model"`, `owned_by`, `shutdown_date: str | None`. — `SDK/src/openai/types/model.py`
- **기능 정보(스트리밍·구조화 출력·reasoning·temperature 지원)는 API 응답에 없다.** — 위 레퍼런스: "no fields describing features or capabilities"
- 기능 정보는 **문서의 모델 페이지**에만 있다. 예: `gpt-5.5` 페이지에는 Endpoints(Responses/Chat Completions), Features(`streaming`, `structured_outputs`, `function_calling` 등), "Reasoning.effort supports: none, low, medium (default), high and xhigh", context 1,050,000, max output 128,000이 나온다. — https://developers.openai.com/api/docs/models/gpt-5.5
- 이 모델 페이지를 기계가 읽을 수 있는 형태로 받는 API는 **UNCONFIRMED**(찾지 못함).
- 목록은 해당 키가 접근할 수 있는 모델만 반환한다고 짐작되지만, 공식 문구는 "Lists the currently available models"뿐이다. 키·프로젝트별 필터링 여부는 **UNCONFIRMED**.
- 어댑터 결론: `list_models()`는 `id`, `created`, `owned_by`, `shutdown_date`만 정규화한다. 기능은 PRD 4.3대로 "작은 기능 정의 + 실제 시험"으로 채운다. 목록에 있다고 기능까지 추론하지 않는다.

## 2. 스트리밍 이벤트 (`stream_text`)

호출: `client.responses.create(..., stream=True)` → `Stream[ResponseStreamEvent]`. 또는 헬퍼 `client.responses.stream(...)`(context manager, `get_final_response()` 제공). — `SDK/README.md#streaming-responses`, `SDK/src/openai/lib/streaming/responses/_responses.py`

모든 이벤트에는 `sequence_number: int`가 있다. — `SDK/src/openai/types/responses/response_error_event.py` 외 각 이벤트 타입

| 어댑터 의미 | SDK 이벤트 `type` | 비고 |
|---|---|---|
| 시작 | `response.created`, `response.in_progress`, `response.queued` | 한 번만 오는 lifecycle 이벤트 |
| 본문 delta | `response.output_text.delta` / `.done` | 사용자 본문은 이것만 |
| 거절 | `response.refusal.delta` / `.done` | 본문 delta와 **별도 이벤트** |
| 추론 요약 | `response.reasoning_summary_text.delta/.done`, `response.reasoning_summary_part.added/.done` | `reasoning.summary`를 켰을 때만 |
| 추론 원문 | `response.reasoning_text.delta/.done` | 타입은 존재. 아래 참고 |
| 정상 완료 | `response.completed` | `response.status == "completed"` |
| **길이 초과 등** | `response.incomplete` | `response.incomplete_details.reason` 확인 |
| 실패 | `response.failed` | `response.error` (code/message) |
| 스트림 오류 | `error` | `code`, `message`, `param`, `sequence_number` |
| 구조 이벤트 | `response.output_item.added/.done`, `response.content_part.added/.done` | 무시해도 됨 |

- 타입 출처: `SDK/src/openai/types/responses/response_*_event.py`(이벤트 `type` Literal 전수 확인)
- `IncompleteDetails.reason` 값: `"max_output_tokens" | "max_messages" | "content_filter" | "steered"`. `steered`는 WebSocket `response.steer` 전용이다. — `SDK/src/openai/types/responses/response.py`
- `ResponseStatus` 값: `completed | failed | in_progress | cancelled | queued | incomplete`. — `SDK/src/openai/types/responses/response_status.py`
- 공식 가이드는 거절이 `response.refusal.delta`로 오고 `response.output_text.delta`와 분리된다고 설명한다. — https://developers.openai.com/api/docs/guides/structured-outputs (Refusals/streaming 절)
- 같은 가이드는 오류 이벤트를 "`response.error`"라고 적었지만, SDK의 `ResponseErrorEvent.type`은 `"error"`다. 어댑터는 **SDK Literal인 `"error"`**를 기준으로 한다.
- 스트리밍 가이드가 꼽는 주요 이벤트는 `response.created`, `response.output_text.delta`, `response.completed`, `error` 네 가지다. 가이드는 `response.incomplete`를 다루지 않는다. — https://developers.openai.com/api/docs/guides/streaming-responses
- 추론 원문: reasoning 가이드는 "API는 raw reasoning text를 반환하지 않는다"고 한다. 그런데 SDK에는 `response.reasoning_text.delta` 타입이 있다. 이 이벤트가 어떤 모델·조건에서 오는지는 **UNCONFIRMED**. — https://developers.openai.com/api/docs/guides/reasoning
- PRD 9.2(추론은 사용자 본문·TTFT 제외)를 지키려면 어댑터는 `response.output_text.delta`만 `text_delta`로 내보낸다. TTFT도 이 이벤트로만 잰다.
- 정상 매핑:
  - `response.completed` → `completed` + `usage`
  - `response.incomplete` → 사유별 처리(`max_output_tokens` → 길이 초과, `content_filter` → 정책 차단)
  - `response.failed` / `error` → `error`
  - refusal 이벤트 → `refused`
  - 종료 이벤트 없이 끊김 → `interrupted`

## 3. 구조화 출력 (`generate_structured`)

- 요청 형식: `text={"format": {"type": "json_schema", "name": ..., "schema": {...}, "strict": True, "description": ...}}`. `name`은 `[a-zA-Z0-9_-]`, 최대 64자. — `SDK/src/openai/types/responses/response_format_text_json_schema_config.py`
- SDK 헬퍼 `client.responses.parse(text_format=PydanticModel)`는 내부에서 `{"type": "json_schema", "strict": True, ...}`로 바꿔 보낸다. 결과는 `output_parsed`로 돌려준다. — `SDK/src/openai/lib/_parsing/_responses.py` (`type_to_text_format_param`)
- 지원 모델: GPT-4o 이후의 최신 모델. `gpt-4o-mini`, `gpt-4o-2024-08-06` 이후. 더 오래된 모델은 JSON mode만 지원. — https://developers.openai.com/api/docs/guides/structured-outputs
- **스키마 제약** (`strict: true`, 출처는 같은 가이드의 "Supported schemas" 절)
  - 지원 타입: string, number, boolean, integer, object, array, enum, anyOf
  - 지원 키워드:
    - string: `pattern`, `format`(date-time, time, date, duration, email, hostname, ipv4, ipv6, uuid만)
    - number: `multipleOf`, `minimum`, `maximum`, `exclusiveMinimum`, `exclusiveMaximum`
    - array: `minItems`, `maxItems`
  - **루트는 object여야 하고 `anyOf`를 쓸 수 없다.**
  - **모든 필드가 `required`여야 한다.** 선택 필드는 `["string", "null"]` 같은 null 유니온으로 표현한다.
  - **모든 object에 `additionalProperties: false`가 필요하다.** 출력 키 순서는 스키마 순서를 따른다.
  - 미지원: `allOf`, `not`, `dependentRequired`, `dependentSchemas`, `if`, `then`, `else`. 미지원 스키마를 `strict: true`로 보내면 오류가 난다.
  - `$defs` + `$ref`와 재귀(`"$ref": "#"`)를 지원한다.
  - 한도:
    - object 속성 총 5000개, 중첩 10단계
    - 속성명·정의명·enum·const 문자열 총길이 120,000자
    - enum 값 총 1000개
    - 값이 250개를 넘는 단일 string enum은 총길이 15,000자
  - fine-tuned 모델은 추가 제약이 있다(`minLength`/`maxLength`/`pattern`/`format`/`minimum`/`maximum`/`minItems` 등 미지원).
- **Pydantic 기본 스키마는 그대로는 strict 규칙에 맞지 않을 수 있다**(Optional 필드의 default, `additionalProperties`). `responses.parse` 헬퍼가 변환해 준다(위 `type_to_text_format_param` 경로). 우리 7.4 스키마가 이 변환을 통과하는지는 구현 때 단위 테스트로 확인해야 한다(**UNCONFIRMED**: 이번 조사에서 직접 실행하지 않음).
- **거절 표현**: 출력 message의 content 배열에 `{"type": "refusal", "refusal": "..."}` 항목이 생긴다. 이 항목은 스키마를 따르지 않는다. — 같은 가이드, `SDK/src/openai/types/responses/response_output_refusal.py`
- **길이 초과**: `status == "incomplete"` 이고 `incomplete_details.reason == "max_output_tokens"`이면 JSON이 잘린 것이다. 이런 결과는 파싱하지 않는다. 가이드 예제는 `content_filter`도 오류로 처리한다. — 같은 가이드
- 새 스키마의 첫 요청은 스키마 처리 때문에 지연이 추가된다. 같은 스키마의 이후 요청에는 추가 지연이 없다. — 같은 가이드
- 어댑터가 구분할 결과: `refused`(refusal 항목) / 길이 초과(`incomplete`+`max_output_tokens`) / 정책 차단(`incomplete`+`content_filter`) / 빈 응답 / 스키마 검증 실패. 마지막 둘은 서버 Pydantic 최종 검증(PRD 7.4)이 판정한다.

## 4. 취소 · 재시도 · 타임아웃

**재시도** (`SDK/src/openai/_constants.py`, `SDK/src/openai/_base_client.py`, `SDK/README.md#retries`)
- 기본값은 `DEFAULT_MAX_RETRIES = 2`, 백오프 0.5s → 최대 8s.
- `OpenAI(max_retries=0)`이면 재시도를 끈다. 요청 단위로는 `client.with_options(max_retries=0)`. 값은 0 이상의 정수여야 하고, 아니면 전송 전에 오류가 난다.
- 재시도 대상: 연결 오류, 408, **409**, 429, 5xx. 서버 헤더 `x-should-retry: true/false`가 오면 그 지시를 우선한다.
- `Retry-After`(및 `retry-after-ms`)를 존중한다. 다만 값이 120초(`MAX_RETRY_AFTER_DELAY`)를 넘으면 재시도하지 않는다.
- 앱 계층이 재시도를 소유한다면(PRD 9.4) SDK는 `max_retries=0`으로 두고, 위 분류(특히 409 포함 여부)와 `Retry-After` 파싱을 앱에서 다시 구현한다.
- 재시도할 때 SDK는 non-GET 요청에 `Idempotency-Key`를 자동 생성한다(`stainless-python-retry-<uuid>`). 다만 `_idempotency_header`는 `None`으로만 설정되고 어디서도 덮어쓰지 않는다. 따라서 이 키는 **HTTP 헤더로 전송되지 않는다**(소스 확인, `_base_client.py` L472·L540). 서버 측 멱등 보장은 **UNCONFIRMED**(문서에서 찾지 못함).
- **스트림 소비 중 오류는 재시도하지 않는다**: "Stream consumption is not automatically retried, because replaying a request could duplicate output". 스트림 중 read timeout은 `APITimeoutError`, 다른 전송 실패는 `APIConnectionError`로 온다. — `SDK/README.md#handling-errors`

**타임아웃**
- SDK 기본값: `httpx2.Timeout(timeout=600, connect=5.0)`. connect 5초, read/write/pool은 각 600초. — `SDK/src/openai/_constants.py`
- 클라이언트 단위로 `OpenAI(timeout=httpx2.Timeout(60.0, read=5.0, write=10.0, connect=2.0))`처럼 정하거나, 요청 단위로 `with_options(timeout=...)`나 `timeout=` 인자를 쓴다. 타임아웃이 나면 `APITimeoutError`. — `SDK/README.md#timeouts`
- httpx2의 timeout은 **작업 단위**다. connect는 소켓 연결, read는 "청크 하나를 기다리는 최대 시간"이다. **전체 요청 마감(total deadline) 옵션은 없다.** — https://pydantic.dev/docs/httpx2/advanced/timeouts/
- 따라서 PRD 9.4의 세 타임아웃은 이렇게 나눈다.
  - 연결: httpx2 `connect`
  - 첫 본문: 앱이 `response.output_text.delta` 첫 도착까지 `asyncio.timeout`으로 감싼다
  - 전체: 앱의 `asyncio.timeout`
  - SDK `read`는 청크 사이 유휴 한도로만 쓴다

**취소**
- `Stream.close()` / `AsyncStream.close()`는 "Close the response and release the connection"이다. `with` 블록을 벗어나면 자동으로 닫힌다. — `SDK/src/openai/_streaming.py`
- 커스텀 transport나 hook에서 발생한 task 취소 신호는 바뀌지 않고 그대로 전파된다. — `SDK/README.md#retries`
- `client.responses.cancel(id)`는 **`background=True`로 만든 응답에만** 쓸 수 있다. — `SDK/src/openai/resources/responses/responses.py` (`cancel` docstring)
- **연결을 끊으면 서버 생성과 과금도 멈추는지는 UNCONFIRMED.** 공식 문서에서 찾지 못했다. PRD 9.3이 말하는 "정확히 한 번 과금 비보장"과 같은 맥락으로 취급한다.

## 5. 사용량 필드

`ResponseUsage`의 필드 — `SDK/src/openai/types/responses/response_usage.py`
- `input_tokens`
- `input_tokens_details.cached_tokens`, `input_tokens_details.cache_write_tokens`
- `output_tokens`
- `output_tokens_details.reasoning_tokens`
- `total_tokens`

포함 관계
- **cached ⊂ input, cache_write ⊂ input**: 공식 비용 예시 코드가 일반 입력을 `inputTokens - cachedTokens - cacheWriteTokens`로 계산한다. "Cache-write pricing is not an additive fee"라는 문구도 있다. 다만 "subset"이라는 명시적 단어는 없다. — https://developers.openai.com/api/docs/guides/prompt-caching
- **reasoning ⊂ output**: 추론 토큰은 "billed as output tokens"이고 `output_tokens_details.reasoning_tokens`로 보고된다. 예시에서는 `output_tokens` 1,186 중 1,024가 reasoning이다. — https://developers.openai.com/api/docs/guides/reasoning
- `max_output_tokens`는 보이는 출력과 추론 토큰을 **합친** 상한이다. 그래서 추론만 하다가 보이는 출력 없이 `incomplete`가 날 수 있고, 그때도 입력과 추론 토큰은 과금된다. 시작값으로 25,000 토큰 이상을 확보하라고 권장한다. — 위 reasoning 가이드, `SDK/.../response_create_params.py` (`max_output_tokens`)
- `total_tokens == input_tokens + output_tokens`라는 명시적 정의는 **UNCONFIRMED**. docstring은 "the total number of tokens used"뿐이다.
- 캐시 최소 길이: GPT-5.6 이후는 1,024 토큰. 그 이전 모델은 조건마다 다르다. `cached_tokens` 보고 방식도 모델 세대마다 다르다(이전 모델은 128 단위로 내림). — prompt-caching 가이드
- 스트리밍에서는 `usage`가 `response.completed`(또는 `incomplete`/`failed`)의 `response.usage`에 실린다. `incomplete`/`failed`에서도 항상 채워지는지는 **UNCONFIRMED**.

## 6. `temperature` / `reasoning` 허용 규칙

- 요청 필드 `reasoning`의 구성 — `SDK/src/openai/types/shared/reasoning.py`, `reasoning_effort.py`
  - `effort`: `none | minimal | low | medium | high | xhigh | max`
  - `summary`: `auto | concise | detailed`
  - `context`: `auto | current_turn | all_turns`
  - `mode`: `standard | pro | str`
  - `generate_summary`: deprecated
- **SDK는 모델별 허용 여부를 검증하지 않는다.** 타입은 모든 값을 받는다. docstring 문구: "Not all reasoning models support every value".
- `temperature`는 0–2이고, `temperature`와 `top_p`를 함께 바꾸지 말라고 권장한다. 모델별 제약은 docstring에 없다. — `SDK/.../response_create_params.py`
- 모델별 규칙은 문서의 모델 페이지와 가이드에만 있다. 공식 문서로 확인한 예:
  - `gpt-5.5`: effort `none, low, medium(default), high, xhigh`. — models/gpt-5.5
  - GPT-5.2/5.1: `temperature`, `top_p`, `logprobs`는 **effort `none`일 때만 지원**한다. 다른 effort와 함께 보내면 "will raise an error". GPT-5의 기본 effort는 medium이고, GPT-5.1/5.2는 none이다. — https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5.2
  - GPT-6 계열: "When reasoning effort is not `none`, remove `temperature`, `top_p`, and `top_logprobs`."
  - GPT-6.1 Sol: effort `low, medium(default), high, xhigh, max`이고 `none`/`minimal`은 미지원.
  - GPT-6 Astra는 `none` 미지원(보내면 HTTP 400). GPT-6 Sol과 Luna는 `none`을 지원한다.
  - 출처: https://developers.openai.com/api/docs/guides/latest-model, reasoning 가이드
- 일반 규칙으로 정리하면: **effort가 `none`이 아닌 reasoning 모델에서는 sampling 파라미터를 보내면 안 된다(오류).** 지원 effort 집합과 기본값은 모델마다 다르고, 미지원 값은 400을 낸다.
- 모델별 허용 매트릭스를 API로 조회할 방법은 없다(§1). 아래는 **UNCONFIRMED**:
  - effort `none`인 GPT-6 Sol/Luna에서 `temperature`가 허용되는지(문서에 명시 없음)
  - 비추론 모델(예: `*-chat-*`)의 규칙
- 어댑터 결론(PRD 4.4): `validate_model_and_options()`는 모델별 "작은 기능 정의"(허용 effort 집합, sampling 허용 조건)로 **사전에 거부**한다. SDK나 API가 대신 걸러 주리라 기대하지 않는다. 정의에 없는 모델·옵션 조합은 미검증으로 거부하거나, 시험 호출로 400 여부를 확인해 기록한다.

## 7. 어댑터 계약에 반영할 요약

1. `openai` 3.x는 httpx2 기반이다(BREAKING). 버전 고정과 TLS 저장소를 결정해야 한다.
2. `models.list()`는 페이지 없이 `id/created/owned_by/shutdown_date`만 준다. 기능 정보는 없다.
3. 본문은 `response.output_text.delta`, 거절은 `response.refusal.delta`, 종료는 `completed`/`incomplete`(사유 필드)/`failed`/`error`이고 서로 배타적이다.
4. 구조화 출력은 `text.format` json_schema + `strict`. 루트 object, 전부 required, `additionalProperties: false`이고 거절은 refusal 항목으로 온다.
5. `max_retries=0`이면 SDK 재시도가 꺼진다(기본 2회, 408/409/429/5xx). 스트림 소비 중에는 원래 재시도하지 않는다.
6. 타임아웃은 connect와 청크 유휴 단위뿐이다. 첫 본문과 전체 마감은 앱에서 구현한다.
7. cached ⊂ input, reasoning ⊂ output. `max_output_tokens`에는 추론 토큰이 포함된다.
8. effort ≠ `none`이면 `temperature`/`top_p` 금지. 허용 effort는 모델별이고 SDK는 검증하지 않는다.
