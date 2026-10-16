# AI 연결·모델: 제공자 모델과 모델별 effort 최신화 및 셀렉터 선택

조사 기준일: **2026-10-13**. 공식 API 문서, 고정 SDK의 공개 타입, 현재 저장소 코드를 확인한 후 작성한 기능 요청이다. 아래 모델 표는 구현을 위한 대표 모델 조사이며, 특정 제공자 연결에서 접근 가능한 전체 목록을 대신하지 않는다.

## 요청과 기대 결과

관리자가 **AI 연결·모델**에서 제공자 연결의 최신 모델 목록을 셀렉터로 선택하고, 선택한 모델이 지원하는 effort/thinking 값만 셀렉터로 선택할 수 있도록 한다. 모델 목록 갱신과 모델별 기능 정의 갱신을 함께 다루며, 기존 모델 설정과 수업 설정 스냅샷은 보존한다.

범위는 현재 제공자 **OpenAI·Anthropic(Claude)·Google(Gemini)**와 앱의 텍스트 생성·스트리밍·구조화 출력 역할이다. 신규 제공자, 이미지·음성 전용 모델 실행, 자동 유료 시험, 자동 모델 교체는 포함하지 않는다.

## 현재 구현에서 확인한 차이

기준 코드: `c6af9a1eadf2930ede096a8dc0194a10a7d8a3c4`.

- `static/js/ai-render.js`는 제공자 목록의 모델을 개별 버튼으로 표시한다. `static/js/ai-connections.js`의 모델 등록 폼은 모델 ID를 텍스트 입력으로 받는다.
- 옵션 편집에는 이미 `capabilities.fields[].choices`를 `<select>`로 그리는 기능이 있다. `static/js/ai-render.js`의 기존 `field()`를 재사용할 수 있다.
- 기능 정의는 OpenAI의 `gpt-5-mini`, `gpt-5.2` 및 명시적인 두 스냅샷, Claude의 `claude-sonnet-4-6`, Gemini의 `gemini-2.5-flash`로 제한된다. 최신 모델이 제공자 목록에 나타나더라도 기능 정의가 없으면 옵션 편집·역할 시험·실행이 차단된다.
- 비생성 모델 목록 갱신 API, 전체 페이지 읽기, 24시간 TTL, 실패 시 마지막 성공 목록 보존, 키 revision에 따른 늦은 결과 차단은 이미 존재한다. 새 목록 수집 체계를 만들 필요는 없다.
- Gemini 기능 정의의 `thinking_levels`는 비어 있지만, `google_generation.parameters()`에는 이미 앱의 `thinking.level`을 SDK의 `thinking_config.thinking_level`로 전달하는 일반 매핑이 있다. 우선 기능 정의·검증·전송 테스트를 확장하고, 실패 근거가 있을 때만 어댑터를 수정한다.

기존 계약: [ADR-0005 모델 목록과 역할 검증 분리](https://github.com/DomineYH/misconcept_platform_v2/blob/c6af9a1eadf2930ede096a8dc0194a10a7d8a3c4/docs/adr/0005-provider-catalog-role-verification.md), 기존 관련 이슈 #40, #43, #48, #49.

## 조사 결과: 모델 목록만으로 effort를 최신화할 수 있는가

| 제공자 | 모델 목록 API | 확인된 차이와 구현에 필요한 정보 |
| --- | --- | --- |
| OpenAI | `GET /v1/models` | ID와 공개 메타데이터를 반환하지만 모델별 effort enum을 반환하지 않는다. 목록과 별도로 공식 모델 문서에 근거한 기능 정의가 필요하다. [공식 Models API](https://developers.openai.com/api/reference/resources/models/methods/list) |
| Claude | `GET /v1/models` | 커서 페이지네이션과 `capabilities.effort`, thinking 지원 정보, lifecycle 메타데이터를 제공한다. 허용 값의 교차 확인에 활용하되 기본값과 옵션 조합 제약은 공식 문서로 보완한다. 기존 목록 파서의 effort 필드에는 `xhigh`가 없으므로 신규 모델 지원 시 확인해야 한다. [공식 Models API](https://platform.claude.com/docs/en/api/models/list) |
| Gemini | `GET /v1beta/models` | 페이지네이션, 생성 메서드와 토큰 한도를 확인할 수 있다. 고정 SDK의 Model에는 `thinking` 필드도 있으나 허용 level/budget 값은 목록만으로 알 수 없다. 모델별 공식 정의가 필요하다. [공식 Models API](https://ai.google.dev/api/models) |

**공식 문서에 공개된 모델**, **현재 연결로 조회된 모델**, **앱에서 해당 역할 검증을 통과한 모델**을 구분해야 한다. 공개 모델 이름이나 목록 포함 여부만으로 현재 계정의 실행 권한·쿼터·역할 적합성을 보장하지 않는다.

## 조사 결과: 모델별 추론 옵션

OpenAI는 현재 앱이 사용하는 Responses API의 `reasoning.effort`, Claude는 Messages API의 `output_config.effort`를 사용한다. Gemini는 앱의 `thinking.level`/`thinking.budget`을 SDK의 `thinking_config.thinking_level`/`thinking_config.thinking_budget`으로 전송한다. 같은 이름의 수준이라도 제공자 간 동일한 동작으로 환산하지 않는다.

### OpenAI

| 모델 ID | 허용 `reasoning.effort` | 생략 시 기본값 | 공식 출처 |
| --- | --- | --- | --- |
| `gpt-6-astra` | `low`, `medium`, `high`, `xhigh`, `max` | `medium` | [모델](https://developers.openai.com/api/docs/models/gpt-6-astra), [현재 모델 가이드](https://developers.openai.com/api/docs/guides/latest-model) |
| `gpt-6.1-sol` | `low`, `medium`, `high`, `xhigh`, `max` | `medium` | [모델](https://developers.openai.com/api/docs/models/gpt-6.1-sol) |
| `gpt-6-sol` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | `medium` | [모델](https://developers.openai.com/api/docs/models/gpt-6-sol) |
| `gpt-6-luna` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | `medium` | [모델](https://developers.openai.com/api/docs/models/gpt-6-luna) |
| `gpt-5.6-sol` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | `medium` | [모델](https://developers.openai.com/api/docs/models/gpt-5.6-sol) |
| `gpt-5.6-terra` | `none`, `low`, `medium`, `high`, `xhigh`, `max` | `medium` | [모델](https://developers.openai.com/api/docs/models/gpt-5.6-terra) |
| `gpt-5.5` | `none`, `low`, `medium`, `high`, `xhigh` | `medium` | [모델](https://developers.openai.com/api/docs/models/gpt-5.5) |
| `gpt-5.2` (기존) | `none`, `low`, `medium`, `high`, `xhigh` | `none` | [모델](https://developers.openai.com/api/docs/models/gpt-5.2) |
| `gpt-5-mini` (기존) | `minimal`, `low`, `medium`, `high` | `medium` | [GPT-5 가이드](https://developers.openai.com/api/docs/guides/latest-model?model=gpt-5) |

`none`과 `minimal`은 서로 다른 값이며 GPT-6 Astra/6.1 Sol에는 제공하면 안 된다. SDK의 전체 enum을 모든 모델에 그대로 노출해서도 안 된다. temperature와 effort의 조합은 모델별로 검증한다. 예를 들어 현재 `gpt-5.2` 구현은 temperature를 effort=`none`일 때만 허용한다. [공식 reasoning 가이드](https://developers.openai.com/api/docs/guides/reasoning)

현재 지원 스냅샷 `gpt-5-mini-2025-08-07`은 **2026-12-11 종료 예정**이며 공식 대체 안내는 `gpt-5.6-terra`다. 이는 해당 스냅샷의 종료 일정이며, 저장된 alias나 설정을 임의로 치환하는 근거로 사용하지 않는다. 공식 Markdown의 종료 표를 직접 확인했다. [공식 종료 안내](https://developers.openai.com/api/docs/deprecations.md)

### Claude

| 모델 ID | 허용 `output_config.effort` | 기본값 | 관련 thinking 제약 |
| --- | --- | --- | --- |
| `claude-opus-5-5` | `low`, `medium`, `high`, `xhigh`, `max` | `medium` | adaptive thinking 상시 사용. `disabled`는 모든 수준에서 400 오류 |
| `claude-sonnet-5-5` | `low`, `medium`, `high`, `xhigh`, `max` | `high` | thinking 방식과의 조합을 모델별로 정의 |
| `claude-haiku-5-5` | `low`, `medium`, `high`, `xhigh`, `max` | `medium` | `disabled`는 `high` 이하에서 허용. `xhigh`/`max`에서는 400 오류 |
| `claude-sonnet-4-6` (기존) | `low`, `medium`, `high`, `max` | `high` | `xhigh` 미지원. 수동 budget 방식의 deprecated 상태를 표시 |

위 표의 허용 값·기본값·비활성화 제약은 [공식 effort 문서](https://platform.claude.com/docs/en/build-with-claude/effort)를 기준으로 확인했다. 문서는 Fable/Mythos 계열의 추가 지원도 설명한다. 해당 계열은 실제 연결의 목록·접근 권한 및 정확한 모델 ID를 확인한 뒤 기능 정의 대상으로 판단하며 대표 표를 전체 모델 목록으로 고정하지 않는다.

effort는 thinking 활성 여부와 별도의 제어다. `budget_tokens` 방식이 deprecated라는 사실을 즉시 미지원이나 확인된 코드 버그로 해석하지 않는다. 기존 Sonnet 4.6 설정을 조용히 지우거나 바꾸지 않고 모델별 규칙과 역할 시험으로 확인한다. [공식 effort와 thinking 설명](https://platform.claude.com/docs/en/build-with-claude/effort)

### Gemini

| 모델 ID | 앱 옵션 → SDK 옵션 | 허용 값 | 기본값·상태 |
| --- | --- | --- | --- |
| `gemini-3.8-flash` | `thinking.level` → `thinking_config.thinking_level` | `low`, `medium`, `high` | `medium`, 안정 모델. `minimal` 미지원 |
| `gemini-3.6-flash` | 동일 | `minimal`, `low`, `medium`, `high` | `medium`, 안정 모델 |
| `gemini-3.5-flash-lite` | 동일 | `minimal`, `low`, `medium`, `high` | `minimal`, 안정 모델 |
| `gemini-3.1-flash-lite` | 동일 | `minimal`, `low`, `medium`, `high` | `minimal`, 안정 모델 |
| `gemini-3.1-pro-preview` | 동일 | `low`, `medium`, `high` | `high`, preview를 명시. `minimal` 미지원 |
| `gemini-2.5-flash` (기존) | `thinking.budget` → `thinking_config.thinking_budget` | `-1` 자동, `0` 끄기, 정수 `1..24576` | 동적 thinking, level 미지원 |

위 모델별 값과 기본값은 **기존 `generateContent` 전용** [공식 thinking 문서](https://ai.google.dev/gemini-api/docs/generate-content/thinking), 안정/preview 구분은 [공식 모델 목록](https://ai.google.dev/gemini-api/docs/models)에서 확인했다. 일반 `/docs/thinking`의 Interactions 예제를 기존 어댑터의 요청 형식으로 사용하지 않는다.

level과 budget을 동시에 지정하지 않는다. `minimal`은 사고의 완전한 비활성화를 보장하지 않는다. Gemini 2.5 모델은 기존 사용 계정에 접근이 제한될 수 있으며 현재 공식 안내상 deprecated로 분류되지 않는다. [generateContent 제약](https://ai.google.dev/gemini-api/docs/generate-content/thinking), [2.5 접근 안내](https://ai.google.dev/gemini-api/docs/models)

Gemini 2.5 Flash의 숫자 토큰 예산은 enum으로 바꾸지 않는다. 선택 가능한 사고 방식은 셀렉터로 보여 주되, 숫자가 필요한 모델에는 기존 숫자 입력과 범위 검증을 유지한다.

## SDK 확인과 구현 방향

고정 의존성은 `openai==3.26.0`, `anthropic==1.12.1`, `google-genai==2.29.0`이다. 설치된 공개 타입을 읽어 OpenAI의 `max`, Claude의 `xhigh`/`max`, Gemini의 `thinking_level` 필드가 존재함을 확인했다. 이 요구만으로 SDK 업그레이드를 선행할 근거는 없다. 타입 존재는 모델별 지원이나 실제 SDK 전송 성공을 보장하지 않으므로 transport 테스트와 명시적 역할 시험으로 검증한다.

1. **모델 선택:** 기존 `catalog.models`와 목록 갱신 API를 재사용하여 제공자별 모델 ID 셀렉터를 제공한다. 현재 연결의 목록을 사용하며, 문서의 대표 모델을 모든 계정에서 접근 가능한 목록처럼 주입하지 않는다. 기존 모델 ID 직접 입력 경로는 기능 정의 필요 상태를 유지한다.
2. **모델별 옵션 선택:** 기존 `capabilities.fields`와 `choices` 렌더러를 재사용한다. 서버 기능 정의를 공식 출처·확인 날짜·버전과 함께 확장하고, enum과 조건부 thinking 제약을 UI와 서버에서 일치시킨다. 정확한 ID와 명시적 스냅샷을 관리하고 이름 접두사로 옵션을 추측하지 않는다.
3. **최신성:** 제공자 목록의 갱신 시각/stale과 코드 기능 정의의 확인 날짜/버전을 구분한다. 수동 갱신으로 현재 연결의 목록을 최신화하고 실패 시 기존 목록을 유지한다. 화면 진입·재접속만으로 외부 조회나 유료 시험을 시작하지 않는다.
4. **실행 연결:** 모델마다 텍스트·스트리밍·구조화 출력, 문맥/출력 한도, temperature/effort/thinking 조합을 함께 정의한다. 목록 메타데이터와 기능 정의가 충돌하면 기존 계약대로 실행과 시험을 막는다. 역할 검증의 버전 무효화도 유지한다.
5. **기존 값 보존:** 저장된 모델이나 옵션이 최신 선택지에서 사라져도 원래 값을 표시하고 사용 불가/재검증 필요 상태를 알린다. 자동 대체, 저장 값 삭제, invalid effort의 조용한 생략을 하지 않는다. 기존 시나리오·세션·비교 기록과 수업 설정 스냅샷을 보존한다.

## 영향 범위

- 화면: `static/js/ai-connections.js`, `static/js/ai-render.js`, 기존 모델 옵션을 소비하는 시나리오 작성·모델 비교 화면과 fixtures.
- 정의와 검증: `src/services/model_capabilities.py`, `anthropic_capabilities.py`, `google_capabilities.py` 및 `anthropic_catalog.py`의 메타데이터 허용 필드.
- API와 실행: `src/api/routes/admin_models.py`, `admin_catalog.py`, `src/services/model_configuration.py`, 역할 시험·공통 생성 어댑터·문맥 예산. 실제 영향이 있는 부분만 변경한다.
- 의존성·DB: 현재 조사로 새 의존성이나 DB 스키마 변경 필요는 확인되지 않았다. 기존 모델·옵션 저장 형식과 export 계약을 유지한다.
- 가격: 새로운 모델의 비용 정보가 확인되지 않으면 기존 계약대로 미산정으로 남긴다. 다른 모델의 가격이나 0으로 대체하지 않는다.

## 완료 기준

- [ ] 세 제공자의 비생성 목록 갱신 결과가 각각의 모델 ID 셀렉터에 반영되고 다른 제공자의 모델이 섞이지 않는다. 실제 계정 목록을 기준으로 최신 안정/preview 상태를 구분한다.
- [ ] 신규 모델 선택·등록 후 해당 모델의 공식 허용 effort/level만 옵션 셀렉터에 표시된다. 기능 정의 없는 모델은 그 상태를 표시하며 실행을 허용하지 않는다.
- [ ] `지정하지 않음`은 요청에서 옵션을 생략하는 의미로 유지한다. `none`, `minimal`, 숫자 `0`, 자동 budget `-1`을 서로 구분하며 모델별 기본값을 확인할 수 있다.
- [ ] 모델별 thinking 방식, effort와 temperature, level/budget 조합을 UI에서 안내하고 서버에서도 검증한다. UI를 우회한 미지원 값은 422로 거부되며 제공자 생성 호출이 발생하지 않는다.
- [ ] 허용된 옵션이 저장·재편집·역할 시험·일반 생성·모델 비교에서 같은 값으로 전달된다. 기존 Google level 매핑과 Claude `xhigh` 메타데이터 처리도 transport 테스트로 확인한다.
- [ ] 갱신 실패·빈 목록·오래된 목록·키 교체 중 늦은 응답에서도 마지막 성공 캐시와 기존 등록 모델/저장 옵션을 보존한다. 빈 성공 목록과 조회 실패 상태를 구분한다.
- [ ] 목록 확인을 역할 검증 성공으로 취급하지 않는다. 기능 정의 변경은 기존 버전 계약대로 재검증 필요 상태에 반영되며 유료 시험은 관리자 명시적 동작으로만 실행한다.
- [ ] 기존 모델/옵션이 사라진 경우 원래 선택을 표시하고 수정 또는 재검증을 안내한다. 시나리오·세션·수업 설정 스냅샷·과거 비교 결과를 자동 변경하지 않는다.
- [ ] 모델과 effort 셀렉터에 명확한 label이 연결되고 키보드로 선택·저장·재편집할 수 있다.
- [ ] 공식 출처·확인 날짜·기능 정의 버전이 기록되며 아래 영향별 검사와 저장소 필수 검사를 통과한다.

## 구현 시 검증

현재는 조사와 이슈 등록만 수행했으며 아래 검사는 **향후 구현의 검증 계획**이다.

- 대상 테스트: `tests/test_model_management.py`, `test_openai_catalog.py`, `test_anthropic_catalog.py`, `test_google_catalog.py`, `test_google_capabilities.py`와 영향받는 provider invocation/probe 테스트. 허용 옵션의 실제 SDK 전송과 미지원 조합의 호출 전 차단을 확인한다.
- 브라우저: `npm run test:browser -- ai_`로 모델 셀렉터·모델별 옵션·저장 후 복원·빈/실패 목록·키보드 접근성을 확인하고 시나리오/비교의 공통 옵션 소비까지 영향이 있으면 해당 browser 테스트를 추가한다.
- 공유 기능 정의/validator 변경은 학생봇·멘토·사후 분석·비교·문맥 예산의 영향도를 먼저 평가한다. 영향받는 통합 검사와 필요한 회귀 범위를 실행하며 광범위하거나 범위를 신뢰할 수 없으면 로컬 전체 pytest를 실행한다. 전체 회귀는 CI에서도 통과해야 한다.
- 저장소 필수 명령: `uv run --frozen ruff check .`, `uv run --frozen black --check .`, `npm run test:browser`, `uv run --frozen python tests/check_student_live.py`. README와 AGENTS.md의 검사 정책을 따른다.

## 구현 착수 전 확인할 사항

- 실제 제공자 연결의 키·계정 권한·지역·쿼터로 최신 모델이 조회되고 실행되는지는 이번 조사에서 확인하지 않았다. 문서 표를 실제 계정 접근 보장으로 사용하지 않는다.
- 정확한 지원 모델 집합과 alias/snapshot, 모델별 생성·구조화 출력/출력 한도는 구현 착수일에 공식 문서와 현재 연결 목록으로 재확인한다. 대표 표만으로 전체 카탈로그를 고정하지 않는다.
- deprecated 수동 thinking 설정을 유지/표시하는 세부 UX는 triage에서 정하되, 기존 저장 옵션은 보존한다. 신규 모델 지원 여부는 해당 역할 시험으로 확인한다.
- 이번 작업은 제품 코드·DB·키·저장 옵션을 변경하지 않았고, 실제 외부 모델 목록 조회·유료 생성 호출·제품 테스트는 실행하지 않았다.
