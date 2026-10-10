# S4 CSV 연구 자료 인계

교사 단일/다중 내보내기와 관리자 단일/필터/선택/사용자별 내보내기는
기존 행과 열 순서, `classification_status`, 고정된 분류 표시 이름을 유지한다.
기존 열 뒤에 아래 **다섯 열**이 추가되므로 연구용 소비자는 헤더와 열 개수
검사를 갱신해야 한다. 위치 기반으로 기존 열만 읽는 소비자는 기존 값을
계속 읽을 수 있지만, 전체 열 개수를 고정하거나 모든 열을 모델 입력으로
사용하는 스크립트는 검토가 필요하다.

| 추가 열 | 의미 |
| --- | --- |
| `analysis_schema_version` | 저장된 구조화 보고서 버전. 보고서가 없으면 `unknown` |
| `analysis_status` | 채택된 보고서의 `ok`/`degraded`/`failed`/`no_dialogue`; v1/요약만 있으면 `legacy`. 채택된 결과가 없으면 최신 실행 상태, 실행도 없으면 `unknown` |
| `analysis_coverage_json` | v2 summary 행에만 실제 서버 범위 JSON. 과거 결과와 message 행은 빈 값 |
| `misconception_findings_json` | v2 summary 행에만 저장된 관찰 배열 JSON. 검증된 관찰이 없는 v2는 `[]`, 과거 결과와 message 행은 빈 값 |
| `message_analysis_disposition` | 해당 교사 message 행에만 `classified`/`non_analyzable`/`unclassified` 또는 서버가 기록한 `missing`. 알 수 없는 과거/미채택 분류는 `unknown`; 분류 off와 다른 역할/summary 행은 빈 값 |

상태와 버전은 모든 기존 행에 반복된다. 재시도 실패 후 기존 보고서가 보존된
경우 CSV는 채택된 보고서의 상태와 범위를 내보낸다. 최신 실행 실패는 화면/API의
`latest_run`에서 별도로 확인한다. 결과가 없는 실패 실행은 새 summary 행을
만들지 않으며, 이미 존재하는 message 행의 상태로만 표시한다.

JSON은 UTF-8 원문을 보존한 JSON 문자열이며 기존 CSV writer의 따옴표 처리를
따른다. 쉼표/따옴표/줄바꿈을 직접 분리하지 말고 CSV parser로 셀을 읽은 뒤
JSON parser로 복원한다. 기존 수식 시작 문자 escaping 정책은 유지된다.
전체 범위의 메시지 ID는 저장된 ID다. 관리자 CSV의 `message_id`로 해당 행을
연결할 수 있다. 기존 교사 CSV에는 message ID 열이 없으며 이번 변경은
다섯 열만 추가하므로 ID 열을 별도로 추가하지 않는다.

v1에는 v2 검증이나 과거 범위 추정을 소급 적용하지 않는다. `legacy`/`unknown`과
빈 JSON 셀을 100% 범위, 검증된 빈 관찰, 정상 분석으로 해석하면 안 된다.
분류 off는 기존 `classification_disabled` 값으로 식별한다. 부분 분석의
분포는 유효하게 분류된 발화만 포함하며 전체 대화 평가로 해석하지 않는다.
단일/분할 결과는 같은 v2 열 계약을 사용한다.

내보내기 권한과 익명화 범위는 기존과 동일하다. 원본 수업 설정 스냅샷,
내부 분류 기준, 제공자 비밀과 원시 오류는 새 열에 포함되지 않는다.
실제 교육 품질 승인과 운영 출시는 별도 절차이며 이 호환성 검사가 대신하지 않는다.
