# D4-03 증거 디렉터리 (#205)

[인수 기록](../d4-03-release-acceptance.md)이 링크하는 증거를 둔다. 파일명은 #205 Agent Brief의 배정명이며 `<sha7>`은 고정 후보 `candidate_commit`의 앞 7자다. 후보 SHA가 바뀌면 새 `<sha7>` 파일을 추가하고 이전 파일은 `invalidation-log.md`에 무효로 기록한다(덮어쓰지 않는다).

| 파일 | 내용 | 단계 |
| --- | --- | --- |
| `README.md` | 이 배치 설명 | 1단계 |
| `normal-demo-coverage.md` | normal/demo 각각을 증명하는 CI job·step·테스트, tests.yml 변경 필요성 평가 | 1단계 (2단계에 후보 SHA로 재확인) |
| `review-<sha7>.md` | 독립 reviewer 세션·대상 SHA·체크리스트 결과·결함 목록 | 2단계 |
| `pytest-<sha7>.log` | `uv run --frozen python -m pytest -q` 출력 | 2단계 |
| `ruff-<sha7>.log` | `uv run --frozen ruff check .` 출력 | 2단계 |
| `black-<sha7>.log` | `uv run --frozen black --check .` 출력 | 2단계 |
| `browser-shard-{1,2,3}-<sha7>.json` | CI `browser-shard` job의 shard report(`$RUNNER_TEMP/browser-results/shard-N-of-3.json`) 원본 | 2단계 |
| `live-<sha7>.log` | `uv run --frozen python tests/check_student_live.py` 출력 | 2단계 |
| `invalidation-log.md` | 리뷰 뒤 main/rebase/계약 변경으로 무효가 된 증거, 재검사 이유·범위·결과 | 2단계 (변경이 생길 때) |

각 로그의 첫머리에는 실행 SHA, worktree 상태(clean 여부), 환경(OS·Python·Node 버전, CI run 링크 또는 로컬), 정확한 명령, exit code를 적는다.

넣지 않는 것: 비밀 값과 비밀 hash, `.env` 내용, 실제 host·절대 경로·사용자 home 경로, cookie·CSRF 토큰 값, 대화 원문. 필요한 곳은 `<redacted>`로 바꾼다.

기존 `tests/browser_server.py` 화면 fixture 통과만으로 실제 demo backend 통과를 주장하지 않는다. 실제 backend 증거는 #204의 `tests/demo_d4_02_server.py` 기반 검사와 `docs/demo/d4-02-evidence/`에서 연결한다.
