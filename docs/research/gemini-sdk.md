# 조사: Gemini `generateContent` + `google-genai` Python SDK — 어댑터 계약용 사실

- 이슈: #12 (map #23, blocking #17) · PRD W5/W6/W9, §4.3–4.4, §7.4, §9.1–9.4
- **기준 SDK: `google-genai==2.29.0`** (PyPI 2026-10-07 업로드, 최신). 확인일 **2026-10-08**.
- 방법: PyPI wheel 소스를 직접 읽고, 네트워크 없는 로컬 객체 생성으로 기본값 확인(API 호출 0회). 문서는 ai.google.dev 원문(각 페이지 "Last updated" 표기).
- 소스 링크의 라인 번호는 2.29.0 wheel 기준 → `https://github.com/googleapis/python-genai/blob/v2.29.0/google/genai/<file>`.
- 표기: **[문서]** ai.google.dev, **[소스]** SDK 코드, **[실측]** 로컬 객체 검사, **UNCONFIRMED** = 1차 출처로 확인 못 함.

## 0. 먼저 알아야 할 것 (어댑터 결정에 영향)

1. **generateContent는 "legacy"이지만 "fully supported".** Interactions API가 2026-06 GA, 신규 개발 권장. 가이드 페이지 기본 탭이 Interactions로 바뀜. generateContent용 가이드는 `/gemini-api/docs/generate-content/...` 경로에 따로 있음. PRD §9.1의 generateContent 선택은 유효하지만, 문서 링크는 이 경로를 써야 함.
   - https://ai.google.dev/gemini-api/docs/interactions-overview ("As of June 2026, it is Generally Available … While it is now considered legacy, the original generateContent API remains fully supported.", 2026-10-01)
   - https://ai.google.dev/gemini-api/docs/migrate-to-interactions (2026-09-23)
2. **SDK 자동 재시도는 2.29.0에서 기본 OFF다. 문서와 다르다.** troubleshooting 문서는 "Python SDK automatically retries transient errors up to four times"라고 하지만, 소스에서는 `retry_options=None`이면 `stop_after_attempt(1)`이고, 실측에서도 `max_attempt_number == 1`. **어댑터는 `HttpRetryOptions(attempts=1)`을 명시해 두 경우를 모두 막는다.** (§4)
3. **비동기 경로에서 aiohttp가 설치돼 있으면 `retry_options`와 무관한 숨은 1회 재시도가 있다.** 현재 `uv.lock`에 aiohttp 없음 → httpx 경로. aiohttp를 넣지 말거나 `httpx_async_client`를 주입한다. (§4)
4. **구조화 출력: 문서 예제의 `response_format`은 SDK 2.29.0 `GenerateContentConfig`에서 `ValidationError`가 난다.** REST 참조는 `responseSchema`/`_responseJsonSchema`를 deprecated로 표시하고 `responseFormat`을 권장한다. 하지만 SDK `GenerateContentConfig`(`extra='forbid'`)에는 그 필드가 없다. 2.29.0에서 동작하는 경로는 `response_mime_type="application/json"` + `response_json_schema`다. (§3)
5. **기본 타임아웃 없음.** `HttpOptions.timeout=None`이면 httpx 클라이언트도 `Timeout(timeout=None)`이다(실측). 연결·첫 본문·전체 타임아웃은 앱이 소유해야 한다. (§4)

## 1. 모델 목록 (`models.list`) — W5

| 항목 | 사실 | 출처 |
|---|---|---|
| 페이지 크기 | `pageSize` 미지정 시 50, 최대 1000(더 크게 줘도 1000) | [문서] https://ai.google.dev/api/models (2026-09-23) |
| 다음 페이지 | `nextPageToken`이 없으면 마지막 페이지. 토큰을 쓸 때 다른 파라미터는 처음 호출과 같아야 함 | 같음 |
| SDK 페이지네이션 | `client.models.list(config={'page_size': N})` → `Pager[Model]`. 반복하면 `next_page()`를 자동 호출해 끝까지 감. async는 `await client.aio.models.list()` → `AsyncPager` | [소스] `models.py` L6778–6829, L8818; `pagers.py` L171–204 |
| `query_base` | 미지정 시 `True`(base 모델). `False`면 tuned 모델 | [소스] `models.py` L6808–6809 |
| 메타데이터 매핑 | REST `supportedGenerationMethods` → SDK `Model.supported_actions` | [소스] `models.py` L3171–3175 |
| 메서드 문자열 | camelCase 문자열(예: `generateContent`) | [문서] api/models |
| 토큰 한도 | `input_token_limit`, `output_token_limit` | [문서] api/models · [소스] `types.py` `class Model` |
| 샘플링 메타 | `temperature`(백엔드 기본값, 범위 `[0.0, maxTemperature]`), `max_temperature`, `top_p`, `top_k`(비어 있으면 그 모델에서 `topK` **허용 안 됨**) | [문서] api/models |
| thinking 지원 | `Model.thinking: bool` — "Whether the model supports thinking." | [문서] api/models · [소스] `models.py` L3190 |
| `name` 형식 | `models/<id>`. `generate_content(model=...)`는 `'gemini-…'`와 `'models/gemini-…'`를 모두 받음 | [소스] `models.py` docstring L8479 이하 |

어댑터 함의(PRD §4.3): 목록에 있다고 역할에 쓸 수 있는 것은 아니다. `supported_actions`에 `generateContent`가 있는지와 `thinking`/`max_temperature`/`top_k` 유무는 **옵션 검증 근거로 쓸 수 있는 1차 메타데이터**다.
- **UNCONFIRMED**: `supported_actions`에 `streamGenerateContent`가 따로 나오는지(API 키가 없어 실제 목록을 못 받음). 스트리밍 지원은 목록만으로 단정하지 말고 검증 시험으로 확정한다.
- **UNCONFIRMED**: 구조화 출력 지원을 나타내는 목록 메타 필드. 문서에 없고, 지원 모델 표만 있다(§3).

## 2. 스트리밍 청크, finishReason, 거절 표현 — W6

**전송**
- SDK는 `{model}:streamGenerateContent?alt=sse`를 호출하고, 각 SSE `data:`를 `GenerateContentResponse` 하나로 파싱한다. [소스] `models.py` L4736–4747, `_api_client.py` L370–420 · [문서] https://ai.google.dev/api/generate-content ("stream of `GenerateContentResponse` instances")
- 스트림 중간에 `{"error": …}` 청크가 오면 SDK가 `errors.APIError`(4xx → `ClientError`, 5xx → `ServerError`)를 **이터레이션 중에** raise한다. [소스] `_api_client.py` L1773–1790(sync), L1823–1840(async); `errors.py` L201–205, L294–302

**청크 필드** — [소스] `types.py` `class GenerateContentResponse` L8657–
- `candidates[0].content.parts[]`: 텍스트 델타. `part.thought == True`이면 생각 요약이다(`include_thoughts=True`일 때만 옴).
- `chunk.text`는 첫 후보의 텍스트 part를 이어 붙인 것이다. **thought part는 제외**하고, 텍스트 part가 없으면 `None`을 돌려준다(`''`와 구분됨). [소스] L8698–8768
- `candidates[0].finish_reason`: "If empty, the model has not stopped generating" → 마지막 청크에서만 채워진다고 보면 된다. `finish_message`는 finishReason이 있을 때만 있다. [소스] `class Candidate`, [문서] api/generate-content
- `prompt_feedback`: "Sent only in the first stream chunk. Only happens when no candidates were generated due to content violations." [소스] L8680
- `parsed`: "Not available for streaming." [소스] L8693
- **UNCONFIRMED**: `usage_metadata`가 어떤 청크에 실리는지(마지막 청크만인지, 누적값이 매번 오는지). 문서에 명시되지 않았다. 어댑터는 **마지막으로 받은 non-null `usage_metadata`**를 최종값으로 쓴다.

**FinishReason** (REST 참조 2026-09-23 vs SDK 2.29.0 enum)
- 공통: `STOP`, `MAX_TOKENS`, `SAFETY`, `RECITATION`, `LANGUAGE`, `OTHER`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `SPII`, `MALFORMED_FUNCTION_CALL`, `IMAGE_SAFETY`, `IMAGE_PROHIBITED_CONTENT`, `IMAGE_OTHER`, `NO_IMAGE`, `IMAGE_RECITATION`, `UNEXPECTED_TOOL_CALL`, `TOO_MANY_TOOL_CALLS`
- REST에만 있음: `MISSING_THOUGHT_SIGNATURE`, `MALFORMED_RESPONSE`, `ESCALATION`, `PUP_LIMITED_DISABLED`. SDK에만 있음: `CONTINUATION`
- SDK는 모르는 enum 값을 받으면 `UserWarning`을 내고 같은 이름의 가짜 멤버를 만든다(예외 없음, 실측 확인). → **enum 멤버 비교 대신 `.value` 문자열로 매핑**한다. [소스] `_common.py` L669–685
- 문서 주석: `SAFETY` — "When streaming, content is empty if content filters blocks the output." [소스] `types.py` L522–523

**BlockReason** (프롬프트 차단, `prompt_feedback.block_reason`): `SAFETY`, `OTHER`, `BLOCKLIST`, `PROHIBITED_CONTENT`, `IMAGE_SAFETY` (+SDK에만 `MODEL_ARMOR`, `JAILBREAK`: "not supported in Gemini API"). [문서] api/generate-content, [소스] `types.py` L606–623

**거절 표현** — Gemini에는 OpenAI식 전용 `refusal` 필드가 없다(위 스키마 어디에도 없음). 어댑터 매핑 제안:
- `refused` ← `prompt_feedback.block_reason` 있음(후보 없음), 또는 finish_reason ∈ {`SAFETY`, `PROHIBITED_CONTENT`, `BLOCKLIST`, `SPII`, `RECITATION`, `LANGUAGE`, `PUP_LIMITED_DISABLED`, `ESCALATION`}
- 출력 길이 초과 ← `MAX_TOKENS` (thinking 중에 걸리면 본문이 빈 채로 끝날 수 있음, §6)
- 빈 응답 ← `STOP`인데 `text is None`
- 그 외(`OTHER`, `MALFORMED_RESPONSE`, 미지값) ← error
- 모델이 텍스트로 "할 수 없습니다"라고 답하는 경우는 API 신호가 없으므로 구분할 수 없다.

## 3. 구조화 출력 — W9

**SDK 2.29.0에서 동작하는 필드** — [소스] `types.py` `GenerateContentConfig` L6590–6630, [실측]
- `response_mime_type="application/json"` + `response_json_schema=<dict>`: JSON Schema. 지정하면 `response_schema`는 생략해야 하고 mime은 필수다.
- `response_schema`: OpenAPI 3.0 subset. "If `response_schema` doesn't process your schema correctly, try using `response_json_schema` instead."
- `response_format`은 `types.GenerationConfig`에만 있고 `GenerateContentConfig`에는 없다. 넘기면 `ValidationError: Extra inputs are not permitted`(실측). changelog의 "Add response_format … in GenerationConfig"도 이 범위다. https://github.com/googleapis/python-genai/blob/main/CHANGELOG.md
- REST 참조: `responseSchema`와 `_responseJsonSchema`는 "Deprecated. Use responseFormat instead.", `responseFormat`은 `ResponseFormatConfig{text,audio,image}`. [문서] https://ai.google.dev/api/generate-content
- 가이드 예제는 `config={"response_format": {"text": {"mime_type": ..., "schema": ...}}}`를 쓴다. 2.29.0 SDK와 **불일치**한다. [문서] https://ai.google.dev/gemini-api/docs/generate-content/structured-output (2026-09-02)
- → 어댑터 결정: 2.29.0 고정 동안 `response_json_schema`를 쓰고, SDK가 `response_format`을 `GenerateContentConfig`에 추가하면 어댑터 내부만 바꾼다. **UNCONFIRMED**: `responseJsonSchema` 제거 일정(deprecations 페이지에 항목 없음 — https://ai.google.dev/gemini-api/docs/deprecations, 2026-10-07).

**지원 JSON Schema 키워드** ([문서] generate-content/structured-output; SDK docstring도 같은 목록)
- type: `string`, `number`, `integer`, `boolean`, `object`, `array`, `null`(`{"type": ["string","null"]}` 형태)
- `title`, `description`, `properties`, `required`, `additionalProperties`, `enum`(string/number), `format`(date-time, date, time), `minimum`/`maximum`, `items`, `prefixItems`, `minItems`/`maxItems`
- SDK docstring 추가분: `$id`, `$defs`, `$ref`, `$anchor`, `anyOf`, `oneOf`(anyOf로 해석), 비표준 `propertyOrdering`. 순환 참조는 제한적으로 펼쳐지며 non-required 속성에서만 쓸 수 있다. `$ref`가 있는 서브스키마에는 `$`로 시작하는 키 외에 다른 키를 둘 수 없다.
- **제약**: "The model ignores unsupported properties." (조용히 무시된다 → 서버 Pydantic 재검증 필수.) "The API may reject very large or deeply nested schemas."
- 보장 범위: "syntactically valid JSON … does not guarantee the values are semantically correct." 출력 키 순서는 스키마 키 순서를 따른다.
- 지원 모델 표: Gemini 3.1 Flash-Lite, 3.1 Pro Preview, 3.5 Flash, 2.5 Pro/Flash/Flash-Lite, 2.0 Flash(-Lite)(2.0은 `propertyOrdering` 필요).
- 스트리밍: "streamed chunks will be valid partial JSON strings, which can be concatenated" → PRD §9.2대로 완료 후에만 파싱한다.
- Pydantic: `Model.model_json_schema()`를 그대로 넘기는 것이 공식 예제다. 단 Pydantic이 만드는 키 중 미지원 키(예: `default`, `pattern`)는 **무시된다**(위 규칙에서 추론. 키별 실험 결과는 UNCONFIRMED).

## 4. 재시도·타임아웃·취소 — PRD §9.4

**재시도** — [소스] `_api_client.py` L543–594, L896–902, L1535–1544, L1728–1738; `types.py` L2636–2665
- `HttpOptions.retry_options=None`(기본) → `tenacity.stop_after_attempt(1)` = **재시도 없음**. [실측] `genai.Client(api_key='x')._api_client._retry.stop.max_attempt_number == 1`
- `HttpRetryOptions()`처럼 빈 객체를 주면 기본값으로 **5회 시도**(초기 1s, 최대 60s, 지수 2, jitter 1)이고, 코드 (408, 429, 500, 502, 503, 504) + `httpx.TimeoutException`/`ConnectError`에 재시도한다. [실측] 5
- `attempts=0`은 1로 처리된다(재시도 없음). changelog "Treat `attempts=0` as `attempts=1`".
- 요청별 override: `GenerateContentConfig(http_options=HttpOptions(retry_options=...))`
- **`Retry-After`를 읽지 않는다**(소스에 `retry-after` 참조 0건). → 앱 재시도 계층이 `errors.APIError.response.headers`를 읽어야 한다.
- 재시도는 `_request`(응답 헤더 수신까지)만 감싼다 → **본문 스트리밍이 시작된 뒤에는 SDK가 재시도하지 않는다**. 스트림 중 에러 청크는 그대로 raise된다(§2).
- 문서 불일치: troubleshooting은 "include automatic retry logic … by default"라고 한다. https://ai.google.dev/gemini-api/docs/troubleshooting (2026-10-01). 소스·실측과 다르므로 **`attempts=1`을 명시**한다.
- **숨은 재시도(aiohttp 경로)**: async + aiohttp 설치 + 커스텀 transport/`httpx_async_client` 미지정이면, `ClientConnectorError`/`ClientOSError`/`ServerDisconnectedError` 등에서 `sleep(1~10s)` 후 **무조건 1회 재요청**한다(`retry_options`와 무관). [소스] `_api_client.py` L1287–1295, L1582–1615(stream), L1660–1700. aiohttp는 optional extra(`google-genai[aiohttp]`)이고 현재 lock에 없다.

**타임아웃** — [소스] `_api_client.py` L206–258, L1448–1460, L1503–1510, L1588
- `HttpOptions.timeout`의 단위는 **밀리초**다. 지정하면 `X-Server-Timeout: ceil(초)` 헤더도 보낸다.
- 기본 `None` → 무제한이다([실측] httpx `Timeout(timeout=None)`).
- httpx 경로: 초 단위 float 하나를 `build_request(timeout=…)`에 넘긴다. httpx에서 float는 connect/read/write/pool **각각에** 적용된다(전체 요청 상한 아님). 스트리밍에서는 청크 간 read 간격 제한처럼 동작한다. https://www.python-httpx.org/advanced/timeouts/
- aiohttp 경로: `ClientTimeout(total=…)` = 스트림 전체 시간 상한. changelog "Apply timeout to the total request duration in aiohttp".
- → PRD의 "연결·첫 본문·전체 구분"은 SDK로 표현할 수 없다. 앱에서 `asyncio.timeout()`으로 첫 본문/전체 데드라인을 걸고, SDK `timeout`은 연결/유휴 상한으로만 쓴다. `async_client_args={'timeout': httpx.Timeout(connect=…)}`로 연결 타임아웃만 따로 줘도 **효과가 없다**. SDK가 요청마다 `timeout=`(None 또는 float)을 명시해 넘기고, httpx는 요청 값으로 클라이언트 기본값을 덮어쓴다. [실측] httpx 0.28.1에서 `build_request(timeout=None)`이면 모든 값이 None.

**스트림 취소** — [소스] `_api_client.py` L436–477
- Python SDK 2.29.0에는 스트림 이터레이터의 공개 `close()`/`aclose()`가 없다(Java는 `responseStream.close()` 문서화).
- 가장 안쪽 `_aiter_response_stream`에 `finally: await response_stream.aclose()`가 있다. 스트리밍을 **태스크로 돌리고 `task.cancel()`** 하면, `CancelledError`가 그 안쪽 `await` 지점에서 발생해 연결이 결정적으로 닫힌다. 바깥 루프에서 `break`만 하면 안쪽 제너레이터는 GC/asyncgen finalizer 때 닫힌다(소스 구조에서 추론).
- 클라이언트 종료: `await client.aio.aclose()` / `async with Client().aio as aclient:`. [소스] README(METADATA) L225, L253
- **UNCONFIRMED**: 클라이언트가 끊으면 서버가 생성/과금을 멈추는지. 문서에 없다. PRD §9.3 "정확히 한 번 과금 보장 안 함"과 일치한다.

## 5. 사용량 `usage_metadata` — [소스] `types.py` `GenerateContentResponseUsageMetadata`, [문서] api/generate-content

| SDK 필드 | REST | 의미·포함 관계 |
|---|---|---|
| `prompt_token_count` | `promptTokenCount` | 프롬프트 전체. **캐시 토큰을 포함**("this still the total effective prompt size … includes the number of tokens in the cached content") |
| `cached_content_token_count` | `cachedContentTokenCount` | 프롬프트 중 캐시 부분(prompt의 부분집합) |
| `candidates_token_count` | `candidatesTokenCount` | 생성 후보 전체 토큰. thoughts 제외(아래 합산식과 가격 문서로 판단) |
| `thoughts_token_count` | `thoughtsTokenCount` | thinking 토큰. 요약만 받아도 전체 thought 토큰이 과금된다 |
| `tool_use_prompt_token_count` | `toolUsePromptTokenCount` | 도구 결과 입력 토큰 |
| `total_token_count` | `totalTokenCount` | SDK docstring: prompt + candidates + tool_use_prompt + thoughts. REST 문서는 "(prompt + thoughts + response candidates)" |
| `*_tokens_details` | `*TokensDetails[]` | modality별 분해 |

- 과금: "response pricing is the sum of output tokens and thinking tokens", "Thought signatures will increase the input tokens". [문서] https://ai.google.dev/gemini-api/docs/generate-content/thinking (2026-09-25)
- 어댑터 정규화 제안: `input = prompt`, `cached_input = cached_content`(input에 이미 포함되어 있으므로 더하지 말 것), `output = candidates + thoughts`, `reasoning = thoughts`. 모든 필드가 `Optional`이므로 None은 0으로 처리한다.

## 6. `temperature` / `thinkingConfig` 허용 규칙 — PRD §4.4

**temperature**
- REST 참조: "Values can range from [0.0, 2.0]", 기본값은 모델마다 다름(`Model.temperature`). [문서] api/generate-content
- models 참조: `[0.0, maxTemperature]`. [문서] api/models
- troubleshooting 표: "Temperature 0.0–1.0", "TopP 0.0–1.0", "Candidate count 1–8". **문서끼리 충돌**한다. → 상한은 **목록의 `max_temperature`** 기준으로 검증한다.
- Gemini 3: "strongly recommend keeping the temperature parameter at its default value of 1.0 … below 1.0 may lead to … looping or degraded performance". 금지가 아니라 권고다. https://ai.google.dev/gemini-api/docs/generate-content/gemini-3 (2026-09-03)
- `top_k`: 모델 메타의 `topK`가 비어 있으면 요청에 넣을 수 없다. [문서] api/models

**thinkingConfig** — `ThinkingConfig(include_thoughts, thinking_budget, thinking_level)` [소스] `types.py` `class ThinkingConfig`
- thinking 미지원 모델에 `thinkingConfig`를 넣으면 **에러**: "An error will be returned if this field is set for models that don't support thinking." → `Model.thinking`으로 사전 차단한다. [문서] api/generate-content
- `thinkingLevel` ∈ {`MINIMAL`, `LOW`, `MEDIUM`, `HIGH`}: "Recommended for Gemini 3 or later models. Use with earlier models results in an error." Gemini 2.5는 `thinkingLevel`을 지원하지 않는다. [문서] api/generate-content, generate-content/thinking
- **`thinking_level`과 `thinking_budget`을 같은 요청에 넣으면 400.** [문서] generate-content/gemini-3
- 모델별 level 지원은 표로 따로 정해져 있다. 예: 3.8/3.7 Flash `minimal` → "Not supported (error)", 3.1 Pro `minimal` 미지원, 3.1 Flash-Lite Image는 `low`/`medium` 미지원. 기본값: 3.1 Pro `high`, 3.5 Flash `medium`. 3.1 Pro는 thinking을 끌 수 없고, 3 Flash/Flash-Lite도 완전히 끄지 못한다. [문서] generate-content/thinking
  - → 목록 메타에 level 지원 정보가 **없다**. 모델별 허용 level은 PRD §4.3의 "공식 사양 기반 작은 기능 정의"로 관리하고, 검증 시험으로 확정한다.
- `thinkingBudget`(2.5 계열): 0=끔, -1=dynamic. 범위는 2.5 Pro 128–32768(끌 수 없음), 2.5 Flash 0–24576, 2.5 Flash-Lite 512–24576(기본은 thinking 안 함). Gemini 3에서는 "accepted for backwards compatibility"이지만 Pro에서 "unexpected performance"를 낼 수 있다. [문서] generate-content/thinking
- **`max_output_tokens`는 thought 토큰을 포함**한다. 추론 중에 한도에 걸리면 `MAX_TOKENS`로 "truncated or empty output"이 나오고, thinking 토큰은 과금된다. → 분석 역할의 출력 예산 계산에 thoughts를 넣어야 한다. [문서] generate-content/thinking
- `include_thoughts=True`면 thought 요약 part(`part.thought=True`)가 온다. PRD §9.2대로 사용자 본문과 TTFT에서 제외하고, `chunk.text`도 이미 제외한다(§2).

## 7. 어댑터 구성 요약 (2.29.0 기준)

```python
client = genai.Client(api_key=key, http_options=types.HttpOptions(
    retry_options=types.HttpRetryOptions(attempts=1),  # 앱이 재시도 소유 (기본값이 바뀌어도 안전)
    timeout=<ms>,  # httpx: 연결/청크 간 유휴 상한. 첫 본문/전체 데드라인은 asyncio.timeout()으로
))
# aiohttp 미설치 유지(숨은 재시도 방지). 의존성은 google-genai==2.29.0으로 고정(주 1회꼴 릴리스).
```
- 에러 분류: `errors.ClientError`(4xx: 400/403/404는 재시도 금지), `errors.ServerError`(5xx), `httpx.TimeoutException`/`ConnectError`. 429와 5xx만 앱이 `Retry-After`를 보고 제한적으로 재시도한다(troubleshooting: "Do not retry on client errors (like 400, 402, or 403)").
- 릴리스 속도: 2.20.0(2026-08-25) → 2.29.0(2026-10-07). CHANGELOG https://github.com/googleapis/python-genai/blob/main/CHANGELOG.md

## UNCONFIRMED 목록
1. 스트림에서 `usage_metadata`가 실리는 청크(마지막만인지, 누적인지)
2. `supported_actions`에 `streamGenerateContent`가 별도로 나오는지(실제 목록 응답 미확인)
3. 구조화 출력·thinking level 지원 여부를 나타내는 목록 메타 필드(없는 것으로 보이나 부재를 증명할 수 없음)
4. 클라이언트 연결이 끊긴 뒤 서버의 생성/과금 중단 여부
5. `responseJsonSchema`/`responseSchema` 제거 일정, SDK `GenerateContentConfig.response_format` 추가 시점
6. Pydantic `model_json_schema()`의 미지원 키(`default`, `pattern` 등)에 대한 실제 서버 동작(문서상 "ignores")
