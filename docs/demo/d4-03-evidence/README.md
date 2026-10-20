# D4-03 증거 디렉터리 (#205)

[인수 기록](../d4-03-release-acceptance.md)이 링크하는 증거를 둔다. 파일명은 #205 Agent Brief의 배정명이며 `<sha7>`은 고정 후보 `candidate_commit`의 앞 7자다. 후보 SHA가 바뀌면 새 `<sha7>` 파일을 추가하고 이전 파일은 `invalidation-log.md`에 무효로 기록한다(덮어쓰지 않는다).

`<sha7>` 파일은 두 벌이다. `8f97776`은 릴리스 후보 main `8f97776dcb3456b5df0fb198daf520168752117c`(D6 수정 PR #256 merge)이며 G5 근거다. `ba64b82`는 수정 전 기준선(독립 리뷰 REJECT)으로 G5 근거가 아니며 덮어쓰지 않고 남긴다([`invalidation-log.md`](invalidation-log.md) §1–§2).

| 파일 | 내용 | 상태 |
| --- | --- | --- |
| `README.md` | 이 배치 설명 | 2단계 갱신 |
| [`normal-demo-coverage.md`](normal-demo-coverage.md) | normal/demo 각각을 증명하는 CI job·step·테스트, tests.yml 변경 필요성 평가 | ba64b82 기준, 8f97776 재확인 |
| [`review-8f97776.md`](review-8f97776.md) | 독립 reviewer r205rev의 릴리스 재리뷰 원문(ACCEPT) | 릴리스 후보 |
| [`fix-review-9b48f26.md`](fix-review-9b48f26.md) | D6 수정(PR #256 head `9b48f26`) 리뷰 원문(PASS) | 릴리스 후보 |
| [`review-ba64b82.md`](review-ba64b82.md) | 독립 reviewer r205의 기준선 리뷰 원문(REJECT, P1 D6·P2 C27) | 기준선 |
| `pytest-<sha7>.log` ([8f97776](pytest-8f97776.log), [ba64b82](pytest-ba64b82.log)) | CI `test` job의 `uv run --frozen python -m pytest -q --durations=30` 출력 | CI 발췌 |
| `ruff-<sha7>.log` ([8f97776](ruff-8f97776.log), [ba64b82](ruff-ba64b82.log)) | CI `test` job의 `uv run --frozen ruff check .` 출력 | CI 발췌 |
| `black-<sha7>.log` ([8f97776](black-8f97776.log), [ba64b82](black-ba64b82.log)) | CI `test` job의 `uv run --frozen black --check .` 출력 | CI 발췌 |
| `live-<sha7>.log` ([8f97776](live-8f97776.log), [ba64b82](live-ba64b82.log)) | CI `test` job의 `uv run --frozen python tests/check_student_live.py` 출력 | CI 발췌 |
| `browser-shard-{1,2,3}-<sha7>.json` (8f97776: [1](browser-shard-1-8f97776.json) [2](browser-shard-2-8f97776.json) [3](browser-shard-3-8f97776.json); ba64b82: [1](browser-shard-1-ba64b82.json) [2](browser-shard-2-ba64b82.json) [3](browser-shard-3-ba64b82.json)) | CI `browser-shard` job artifact `browser-shard-N-attempt-1`의 shard report 원본 | CI artifact |
| [`invalidation-log.md`](invalidation-log.md) | 후보·main·계약 변경으로 무효가 된 증거, 재검사 이유·범위·결과 | §1 de3054a → ba64b82, §2 ba64b82 → 8f97776 |

각 로그의 첫머리에는 실행 SHA, worktree 상태(clean 여부), 환경(OS·Python·Node 버전, CI run 링크 또는 로컬), 정확한 명령, exit code를 적는다.

넣지 않는 것: 비밀 값과 비밀 hash, `.env` 내용, 실제 host·절대 경로·사용자 home 경로, cookie·CSRF 토큰 값, 대화 원문. 필요한 곳은 `<redacted>`로 바꾼다.

기존 `tests/browser_server.py` 화면 fixture 통과만으로 실제 demo backend 통과를 주장하지 않는다. 실제 backend 증거는 #204의 `tests/demo_d4_02_server.py` 기반 검사와 `docs/demo/d4-02-evidence/`에서 연결한다.
