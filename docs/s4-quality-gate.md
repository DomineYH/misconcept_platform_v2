# S4 고정 표본과 교육 품질 출시 차단

**현재 상태: 표본·오프라인 절차 준비, 실제 교육 승인 미완료, `release_blocked`.**
#85는 [S4 명세 D10](https://github.com/DomineYH/misconcept_platform_v2/issues/77)의
평가 준비 작업이다. 에이전트가 작성한 기대 근거는 교육 담당자의 정답 승인이나
실제 모델 품질 측정이 아니다. 초기 교육 담당자는 product owner이며 후임은
명시적으로 지정한다. 설계의 사전 자동 승인, mock CI, 역할 검증 성공으로
미래 품질 결과나 유료 실행을 승인하지 않는다.

## 1. 표본과 기대 근거를 사전 검토·고정한다

[corpus](../tests/fixtures/s4_quality_corpus.json)는 개인정보 없는 합성 한국어
대화 12개를 **완전히 펼친 원문**으로 저장한다. 표본마다 안정적 메시지 ID,
v1 수업 설정 스냅샷, transcript/config/sample SHA-256이 있다. 전체 corpus
해시는 기대 근거 초안·검토 상태·경계 fixture까지 포함한다.
해시는 기존 `lesson_snapshots.canonical_hash` 방식(UTF-8, 정렬된 키,
공백 없는 JSON, NaN 금지)이다. 자체 해시는 무결성 검사이며 인간 승인 서명이 아니다.
기준 hash는 커밋된 비교 양식과 대조하고, 변경된 파일의 hash만 새로 계산하여
기존 승인에 끼워 넣지 않는다.

| 표본 ID | 길이 / 완료 턴 | 분류 | 초안 관찰 / 경계 |
| --- | --- | --- | --- |
| math-short-01 | short / 1 | on | 근거 부족, 인사만 |
| math-short-02 | short / 1 | off | 근거 부족, 미응답 마지막 질문 |
| math-ordinary-01 | ordinary / 8 | on | 분수 덧셈 오개념 유지 |
| math-ordinary-02 | ordinary / 10 | on | 음수 곱셈 설명 변화 |
| math-long-01 | long / 20 | on | 비례 설명 변화, 미응답 마지막 질문 |
| math-long-02 | long / 20 | off | 넓이·둘레 설정 이탈 |
| science-short-01 | short / 2 | on | 무게만으로 부력 설명 유지 |
| science-short-02 | short / 1 | on | 계절 설명 근거 부족, 미응답 마지막 질문 |
| science-ordinary-01 | ordinary / 8 | on | 용해와 질량 보존 설명 변화 |
| science-ordinary-02 | ordinary / 10 | on | 직렬 전류 설정 이탈 |
| science-long-01 | long / 20 | on | 운동 유지에 힘이 필요하다는 설명 유지 |
| science-long-02 | long / 22 | off | 실온 증발 설명 변화 |

열린·탐색·유도 질문은 ordinary/long 표본에 함께 배치했다.
초안에는 근거 ID·역할·정확한 인용,
일부 발문의 허용 rubric IDs, 허용/금지 해석이 있다. 모든 상태는
`agent_draft_requires_education_lead_review`, 검토자·일자는 null이다.
교육 담당자가 원문과 설정을 **실제 실행 전에** 검토하고 기대 근거·복수 해석·
N/A 사유를 수정·고정해야 한다. 수정 시 corpus 버전과 모든 해당 해시, 비교 양식의
해시를 함께 갱신하고 검토자·일자·고정 hash를 남긴다. 학생봇 변화는 발화상의
관찰이며 교사의 인과적 효과나 실제 학생 능력으로 단정하지 않는다.

장문 4개의 `planner_fixture`는 **실제 제공자 한도가 아닌** 입력 예산 5,000의
모의 한도로 분할을 유발할 입력이다. 낮은 한도를 실제 model/options로 바꾸지 않는다.
`boundary_fixtures`는 입력 5,000/5,001, 8/9분할, 단일 단위 초과, 종합 초과의
기대 결과를 고정한다. #82/#83에서 실제 planner의 prompt/schema/estimator로
연결하여 분할, 한도 일치, 최대 8개, 초과 시 zero calls를 검증해야 한다.
현재 리허설은 S4 planner나 v2 분석을 구현·검증하지 않는다.

## 2. mock으로 비교 기록 절차를 리허설한다

```bash
uv sync --frozen --extra dev --python 3.12
uv run --frozen python -m pytest -q \
  tests/test_s4_quality_corpus.py tests/test_s4_quality_comparison.py \
  --basetemp .pytest_cache/s4-quality-rehearsal
```

기존 분석의 인증·CSRF HTTP → 실제 서비스 → 임시 SQLite → pinned SDK의
`analysis_transport`/MockTransport 경계를 재사용한다. `conftest.no_network`는
외부 소켓을 거부한다. 환경 파일·운영 DB를 사용하지 않고 유료 호출은 불가능하다.
각 표본은 같은 고정 원문·ID·설정으로 첫 분석과 관리자 재분석을 수행한다.
baseline은 현재 S2 bridge 계약이며 candidate는 **동일 S2 경로의 mock placeholder**다.
S4 품질·호출 감소·findings 성공으로 해석하지 않는다. `quality_reply` pytest fixture가
제공자 응답을 교체하는 지점이다. #79 이후 같은 SDK mock 경계에 v2 응답을 연결하고
현재 계약 메타데이터와 기대 상태를 갱신한다. 제품에 이중 런타임 선택기를 추가하지 않는다.

각 pytest 임시 디렉터리의 `comparison-<sample-id>.json`에 해당 표본의 두 출력,
raw mock 출력, schema/prompt 해시, 코드 revision, 검증 상태, 모의 호출 수·시간을
기록한다. 나머지 11개 행과 인간 점수는 unknown/pending이며 각 파일은 부분 리허설이다.
실제 제공자 시간·사용량·비용은 unknown이고 mock 측정값은 별도 필드다.
출력의 `ok`는 구조 검증 결과일 뿐 교육 점수나 승인으로 전환하지 않는다.

## 3. 승인된 비용 범위 안에서 실제 baseline/candidate를 비교한다

[비교 기록 양식](../tests/fixtures/s4_quality_comparison_template.json)을 예정
pilot model/config마다 복사한다. 출시에 쓸 **exact provider/model/options**를
기록하고 12개 전부를 평가한다. 좋은 결과를 낸 다른 모델이나 옵션으로 대체하지 않는다.
각 표본의 분류 on/off, rubric, 전체 원문·ID·설정을 양쪽에 동일하게 적용한다.
등록 ID는 격리 환경에 매핑하고 이 매핑도 기록하며 실제 생성 옵션을 덮어쓰지 않는다.

S2 bridge 보존 기준은 `1b5a0f9`이며, 실제 baseline/candidate 코드 revision을 별도로
기록한다. 격리된 합성 환경에서 각각의 코드를 실행하고 출력·검증 결과를 보존한다.
실제 제공자 실행은 **사람이 별도 비용 승인자·일자·모델/표본/호출 범위·최대 비용·
통화를 기록한 뒤** 수행한다. 이 mock 명령에는 유료 실행 스위치가 없다.
실제 제공자 비교는 자동 CI 조건이 아니며 이 티켓에서는 실행하지 않는다.

코드, 모든 분석 prompt 및 실제 rendered prompt hash, schema 버전/hash,
estimator/chunk policy 버전, plan hash, corpus/sample/transcript/config hash,
모델/옵션, 양쪽 출력·검증 상태·점수·호출 수·시간·토큰·비용을 남긴다.
없는 측정은 `unknown`이며 0으로 채우지 않는다. baseline에 없는 findings는
baseline 점수를 꾸미지 않고 사전 고정한 인간 기대 근거와 직접 비교한다.

## 4. 교육 담당자의 판정으로만 출시 gate를 연다

| 필수 기준 (표본별) | 통과 조건 |
| --- | --- |
| 정상으로 채택한 잘못된 ID/역할/인용 참조 | 0 |
| 근거 없는 중대한 주장 | 0 |
| 근거 충실성 | 5점 중 4 이상 |
| 분류 적합성 | 분류 on일 때 5점 중 4 이상 |
| 오개념 관찰 타당성 | 5점 중 4 이상 |
| 피드백 실행 가능성 | 5점 중 4 이상 |
| 적용 가능한 공통 차원의 baseline 대비 하락 | 표본·차원마다 1점 이하 |

점수 기준: 1은 원문과 충돌/사용 불가, 2는 큰 수정 필요, 3은 일부 타당하나
중요 근거·실행 안내 부족, 4는 근거와 실행 안내가 적절하고 경미한 수정만 필요,
5는 근거·한계·구체적 다음 질문이 모두 충실함이다. 교육 담당자가 각 점수의
근거 ID와 이유를 기록한다. 중대한 무근거 주장은 교육적 결론을 바꾸는 허위 변화,
교정 성공·인과 효과·능력 확정 등을 포함한다.

분류 off는 분류 적합성 N/A다. 인사만/교사만의 교과 판단, baseline에 없는 findings,
평가 가능한 발문이 없는 경우는 교육 담당자가 **차원별 이유**를 기록해 N/A로 정한다.
근거 부족을 정확히 말하는 관찰은 그 자체로 평가할 수 있다. N/A는 0점으로 총점에
넣지 않고 평균이나 총점으로 낮은 개별 점수를 숨기지 않는다. unknown은 N/A가 아니다.

다음 체크리스트가 모두 충족되어야 해당 예정 model/config의 `release_blocked`를
해제할 수 있다. 한 표본이라도 실패·미평가·검증 실패·필수 범위 누락이면 승인 불가다.
부분 분석을 정상 전체 분석으로 채택하지 않는다.

- [ ] 교육 담당자가 실행 전에 기대 근거를 검토·고정하고 hash/일자를 기록했다.
- [ ] 별도 비용 승인을 받은 실제 baseline/candidate 출력이 12개 모두 존재한다.
- [ ] exact model/options와 corpus 및 버전들이 예정 pilot 설정에 일치한다.
- [ ] 모든 적용 차원이 4/5 이상이며 공통 차원 하락이 각각 1 이하이다.
- [ ] 잘못된 참조와 중대한 무근거 주장이 모두 0이고 N/A 사유가 기록되어 있다.
- [ ] 교육 담당자의 실제 승인자·일자·판정과 승인 기록 hash가 있다.
- [ ] 예정 pilot model/config **각각** 위 기록이 있고 운영 출시 체크리스트에 연결했다.

prompt/schema/chunking/estimator 또는 intended model/options가 바뀌면 영향받는
승인 기록을 다시 만든다. 코드 변경도 해당 실행 계약에 영향을 주면 재평가하며,
corpus/기대 근거 변경 시 사전 검토부터 반복한다. 기존 승인을 새 설정에 복사하지 않는다.
현재 양식과 mock 산출물에는 실제 승인 기록이 없으므로 계속 `release_blocked`다.
#86 통합/운영 인계는 이 문서를 필수 gate로 연결해야 한다.
