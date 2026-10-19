# D5-02 증거 파일 규칙 (#207)

기준: main `2dc7840963e6ca700915c2c63cff7729ca54afa9`, 계약 `d0-03-v1-r3`
(`docs/demo/d0-03-contract-v1.md`). `<sha7>`은 검사한 SHA의 앞 7자다.
승인 기록은 `docs/demo/d5-02-approval.md`, 판정 본문은 `docs/demo/d5-02-rehearsal-evidence.md`다.

## 파일

| 파일 | 단계 | 내용 |
| --- | --- | --- |
| `checklist.md` | 1단계 | 2단계 리허설 절차·명령·기대값. 결과를 적지 않는다 |
| `local-dryrun-<sha7>.md` | 1단계 | 일회용 root 로컬 dry run 기록(기존 harness만 사용) |
| `rehearsal-<sha7>.md` | 2단계 | 검사표 전체 실행 기록(행별 결과) |
| `health-<sha7>.log` | 2단계 | `/health` 호출 시각·HTTP 코드·단계 이름만 |
| `egress-<sha7>.log` | 2단계 | 외부 시도 건수와 허용 목적지 대조 결과만 |
| `isolation-<sha7>.md` | 2단계 | 두 방문객 격리·쿠키·CSRF 결과 |
| `recovery-<sha7>.md` | 2단계 | 복구 실행 기록과 normal 전후 대조 |

판정 본문(`d5-02-rehearsal-evidence.md`)은 위 파일을 이름으로 링크한다.
다른 이름의 파일은 만들지 않는다.

## 결과 한 건에 적는 것

- SHA와 worktree 상태(clean 여부)
- 환경 참조: 값이 아니라 "승인 기록 §N 값" 같은 참조
- 실행한 명령 원문(placeholder 그대로)과 범위
- 결과: `passed` / `failed` / `not run` 중 하나. `not run`과 `failed`는 이유를 적는다
- 시각(UTC), 근거 로그 파일명

## 적지 않는 것 (계약 §4:74, §28:767)

- 요청·응답 본문, 쿠키 원문, 키, secret과 그 hash, 실제 DB 경로, 실제 일회용 root 경로
- 허용: code, operation, generation, 건수, 시간 수치
- 실제 host·origin·URL·경로·IP는 승인 기록에만 있다. 증거에서는 "승인 기록 §N 값"으로 참조한다
- 증거 화면·로그를 붙일 때 위 항목이 보이면 먼저 지운 뒤 붙인다

## 판정

- 필수 행이 하나라도 `failed` 또는 `not run`이면 판정은 **공개 중단**이다(2026-10-18 위임 답변 3, §28:771).
- #208(D5-03)은 승인 기록과 이 판정을 읽기만 한다. 판정을 바꾸지 않는다.
- G6 기록(승인자 @DomineYH, 시각, SHA)은 `d5-02-rehearsal-evidence.md`에 있고 2단계 전에는 비어 있다.

## 점검

- `git diff --name-only origin/main...HEAD`에는 `docs/demo/d5-02-*`만 나와야 한다.
- 실제 주소·경로·키 패턴 검색은 brief의 검증 명령(`grep -rnE ...`)을 쓴다. 이 README에는 검색 패턴을 옮기지 않았다(패턴 자체가 걸리기 때문).
