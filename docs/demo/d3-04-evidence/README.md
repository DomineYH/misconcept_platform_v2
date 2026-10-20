# D3-04 증거 폴더 (#202)

[`../d3-04-integration-evidence.md`](../d3-04-integration-evidence.md)가 참조하는 첨부 증거를 둔다. smoke 모듈은 실행할 때 같은 파일을 git에서 제외된 `test-results/d3-04/`에 쓰며, 여기에는 기록할 실행의 파일만 복사한다.

- `run-<N>.log`: smoke 실행 N의 `START/RECEIPT/STEP/FAIL/END` 줄 발췌. 임시 경로는 `<tmp>`로 표기한다.
- `result.json`: 실행 1의 seed receipt와 단계별 결과(passed/failed/not run, 이유, 소요 ms).
- `step<N>-<설명>.png`, `e<N>-<설명>.png`: 해당 단계의 전체 화면 스크린샷(실행 1: `step1-start.png`, `step3-dialogue.png`, `e3-english.png`).
- `step5-admin.csv`: 5단계에서 내려받은 관리자 CSV. 5단계가 실행된 경우에만 있다(실행 1에는 없음).

이 폴더에는 secret, cookie 값, 실제 host 경로를 넣지 않는다.
