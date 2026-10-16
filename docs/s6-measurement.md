# 합성 지연 측정 기록

S6 #107의 로컬 도구는 제품 HTTP/SSE → 임시 SQLite → 기존 SDK mock과
실제 localhost Chromium 화면을 사용한다. 운영 DB·`.env`·제공자 네트워크를
사용하지 않는다. 새 제품 라우트, telemetry schema, dashboard는 없다.

```sh
uv sync --frozen --extra dev --python 3.12
npm ci
npx playwright install chromium
s6_measurement_dir="$(mktemp -d)"
uv run --frozen python tests/s6_measurement.py --output "$s6_measurement_dir/report.json"
```

출력 파일은 새 경로를 지정한다. DB와 중간 표본은 임시 디렉터리를 사용한다.
각 workload는 warmup(index 0) 한 번과 측정(index 1–5) 다섯 번이다. warmup 원시
기록은 남지만 요약에는 포함하지 않는다. 테스트 실패는 종료 코드 1과
`checks[].passed=false`로 남긴다. 실패 메시지/HTTP 본문을 보고서에 복사하지
않는다. 테스트 원인은 해당 테스트를 따로 실행해 확인한다.

학생 단일 턴은 학생 호출 1, 턴별 오개념 호출 0, request replay 추가 호출 0을
ledger와 SDK 경계에서 확인한다. 짧은 사후 분석도 한 호출과 zero-call replay를
확인한다. 멘토는 검증된 최종 결과 시간만 기록하며 본문 TTFT와 혼동하지 않는다.
관리자 `/admin/ai/state` 조회와 `/admin/ai/settings/update` 저장은 제공자 호출 0이다.
#161부터 브라우저 실험도 완료된 전체 대화를 사용하며 context_turn_limit은 null로
기록한다. warmup과 측정 다섯 턴의 실제 SDK 입력에 과거 쌍이 0–5개 누적됨을
확인하고, 각 표본에 입력 hash와 bytes를 기록한다. 이전의 한 쌍 고정 입력 표본과
입력 크기가 다르므로 지연 수치를 직접 비교하지 않는다.

20개 독립 교사 세션과 관리자 비교 3패널은 수업 우선/비교 우선 순서를 각각
기록한다. 기본 limits에서는 전체 23개 역할 시도에 거부된 패널도 포함한다.
비교 한 HTTP 요청이 3개 패널 시도이므로 HTTP 요청 수는 21이다. 격리 capacity
조건의 total=24, openai=24, admin=3은 mock barrier에서 23개 동시 진입을 확인한다.
운영 기본 limits를 수정하지 않는다. 원시 SDK 시작 시각의 panel_id와 시작 편차는
동일 Python monotonic clock에서 관측한다. 모든 요청은 단일 worker다.

#108은 같은 runner에 느린 멘토 독립성과 장애 표본을 추가한다.
`slow_mentor_student`는 학생 두 턴, `slow_mentor_final`은 첫 턴을 대상으로 대기한
멘토 한 호출이다. 임시 DB에 total=24/openai=24/admin=3을 명시하고 멘토를 barrier로
붙잡은 동안 다음 학생 턴의 저장·조회·다음 입력을 확인한다. 이는 별도 두 턴 실험이며
20개 교사의 성공 실적은 위 capacity workload에서만 확인한다. 대기 barrier에는
고정 지연 숫자가 없으며 해제 시각과 최종 검증 시간을 server clock으로 기록한다.

`panel_provider`/`panel_429`는 기존 3패널 장애 검증을 재사용하며 각 반복에 실패 1개와
정상 완료 2개를 기록한다. 같은 비교 요청 replay가 추가 호출·원장을 만들지 않는다.
`student_storage_failure`/`student_disconnect`는 기존 commit 실패/출력 중 끊김 검증과
zero-call replay를 실행한다. 끊긴 실행의 실제 상태 `interrupted`는 원장·조건에 남기고
요약에서는 `cancelled`로 분류한다. 이 재사용 검사의 request 시간에는 조회/assertion
시간이 포함되며 SDK 시간은 unknown이다. 패널별 시간 대신 전체 검사 시간의 상한을
기록한다. `analysis_invalid_evidence`는 정상 분석 뒤 원문에 없는 인용으로 명시적
재분석을 시도하며 이전 정상 결과와 원장이 보존되고 replay가 호출을 늘리지 않는지
검사한다. 잘못된 근거의 검증 실패는 failed 분포에 넣고 실제 분석 상태는 조건에 남긴다.
정상 분석 준비 호출은 장애 시도의 분모에 넣지 않는다. 모든 새 workload도 warmup 1회와
측정 5회를 실행하며 coverage 누락은 runner 실패다. 제품 코드·운영 limits 변경은 없다.

`raw_ns`는 Python monotonic clock, `raw_browser_ms`는 browser `performance.now`,
ledger 날짜는 UTC wall clock이다. 서로 다른 clock을 차감하지 않는다. ledger의
시각은 원시 증거로만 제공한다. 브라우저 전송→첫 화면 본문과 완료 표시 시간은
브라우저 내부 차이이다. 추론/heartbeat/빈 delta/로딩 UI는 첫 본문이 아니다.

mock 첫 본문→화면 확인 콜백과 저장→완료 표시 확인 콜백은 같은 Python clock에서
차감한 **관측 상한**이다. Playwright binding, Node stdout, IPC와 scheduling의
오버헤드가 포함되며 순수 앱 지연으로 해석하지 않는다. 저장은 SQLAlchemy의
실제 성공 commit 직후, 완료 이벤트는 실제 ASGI SSE send 직전의 test-only 관측이다.
저장→완료 이벤트는 server 내부 clock 차이다. 브라우저 전송→정확한 서버 저장은
clock이 다르므로 unknown(null)이다. ASGI 부하 표본은 응답이 buffered되어 화면
TTFT와 정확한 저장 경계가 unknown이다. SDK mock 경계의 실제 요청은 본문 대신
hash와 bytes로 기록한다. provider duration은 SDK 호출 시작 기준이며
request→provider 첫 본문은 별도 server 구간이다. 분석 최종 결과에는 기존 테스트의 20ms
status polling 오버헤드가 포함된다. 실 제공자 환경에서 이런 구간을 분리할 수
없으면 unknown을 유지해야 한다.

예: completed 5, refused 2라면 전체 attempted 7, admitted 5다. completed의
p95는 정상 완료 분포에만 적용하며 “7개 모두 처리”라고 해석하지 않는다.
`known=0`인 분포의 median/p95는 null이며 0ms가 아니다. 실패/거부/취소 분포는
별도이고, 각 metric의 unknown 수를 숨기지 않는다. 표본 [1,2,3,4,100]의 median은
3, nearest-rank p95는 100이다. 다섯 회는 소표본이며 숫자 p95/개선은 CI gate가 아니다.

schema_version=1의 표본은 workload, repeat, status, 조건(hash/입력 bytes/limits/
mock delay/runtime/provider/model), clock별 원시 시각, duration, allowlisted ledger를
포함한다. 환경은 OS/CPU/메모리/디스크/Python/SDK/브라우저/코드 revision/worker 수/
날짜를 기록한다. 합성 토큰과 미확정 가격을 실 비용으로 주장하지 않는다.
대화·프롬프트·키·내부 멘토 이유는 출력하지 않는다. #108/#109는 이 표본 형식과
`summarize`를 재사용할 수 있지만 실제 제공자/교육 품질/운영 전환 승인을 대신하지 않는다.
