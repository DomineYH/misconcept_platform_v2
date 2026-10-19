# D5-03 공개 증거 (#208)

계약 §26:699가 #208에 준 증거 경로다. 실제 host·URL·경로·secret·cookie 값·대화 원문은 어디에도 적지 않는다.
origin은 `$DEMO_ORIGIN`, `$NORMAL_ORIGIN`, `$DEMO_PUBLIC_URL` 이름으로만 남긴다.

| 파일 | 단계 | 내용 |
| --- | --- | --- |
| `smoke-checklist.md` | 1 | 공개 smoke 명령·기대값·중단 규칙 |
| `public-smoke-<sha7>.md` | 2 | 체크리스트 실행 결과 (아래 template A) |
| `link-check-<sha7>.md` | 2 | 배포 SHA·tuple·링크 일치 (template B) |
| `rollback-decision-<sha7>.md` | 2 | 공개 전 rollback 결정과 실패 시 실행 기록 (template C) |
| `handover-review-<sha7>.md` | 2 | 작성자와 다른 reviewer 세션의 검토 (template D) |

`<sha7>`은 `d5-03-approval.md` §2 `candidate_commit`의 앞 7자다. 2단계에서 파일을 만들 때 실제 값으로 바꾼다.

## 1단계 로컬 dry run (2026-10-20, main `2dc7840`)

localhost만 사용했고 외부 host에 접속하지 않았다. 임시 root는 `$R=~/.cache/mp2-tmp/w208/dry`(일회용),
secret은 실행마다 `secrets.token_urlsafe(48)`로 만들고 기록하지 않았다.
demo 정상 부팅은 #194 4단계와 #197 seed 연결 전이라 불가하므로 **부정 사례만** 실행했다.

| 사례 | 명령 요지 | 결과 |
| --- | --- | --- |
| P03 normal `/health` | `APP_MODE=normal DATABASE_URL=sqlite+aiosqlite:///$R/normal.db uvicorn src.main:app --host 127.0.0.1 --port 18208`; `curl -w '%{http_code}' $NORMAL_ORIGIN/health` | 200 |
| N01 normal의 demo route | `GET /demo`, `GET /demo/state` | 404, 404 (`src/main.py:582`, `:603`에서 demo mode만 router 포함) |
| N01 보조 | `POST /demo/start` (normal) | 403 (normal CSRF가 먼저 거절; 체크리스트는 GET만 사용) |
| L01 빈 `DEMO_PUBLIC_URL` | `curl $NORMAL_ORIGIN/login \| grep -c demo-public-link` | 0 |
| L02 링크 렌더 | `DEMO_PUBLIC_URL=http://localhost:18209/demo`로 재기동, `grep -cF "href=\"$DEMO_PUBLIC_URL\""` | 0 — main에는 링크 template이 없다(#199 미병합). 2단계 전 #199 병합 필요 |
| 설정: normal origin 없음 | `NORMAL_ORIGIN='' DEMO_PUBLIC_URL=http://localhost:18209/demo python -c 'import src.config'` | `CONFIGURATION_INVALID` (`src/config.py:199-201`) |
| 설정: 같은 host | `NORMAL_ORIGIN=http://127.0.0.1:18208 DEMO_PUBLIC_URL=http://127.0.0.1:18209/demo` | `CONFIGURATION_INVALID` (`src/config.py:202-210`, port가 달라도 host 비교) |
| 설정: 허용 밖 path | `DEMO_PUBLIC_URL=http://localhost:18209/other` | `CONFIGURATION_INVALID` (`src/config.py:203-207`: path는 `/` 또는 `/demo`) |
| 설정: 유효 | `DEMO_PUBLIC_URL=http://localhost:18209/demo` | accepted |
| demo 부팅: normal origin 없음 | `APP_MODE=demo ENV=development DEMO_DATA_ROOT=$R/demo-root DEMO_ORIGIN=http://localhost:18209 uvicorn …` | exit 1, `DEMO_CONFIGURATION_INVALID` (`src/config.py:218-223`) |
| demo 부팅: seed 미연결 | 위 + `NORMAL_ORIGIN`, `DEMO_SESSION_SECRET`, `DEMO_CSRF_SECRET` | exit 3, `Application startup failed`, `DEMO_CONFIGURATION_INVALID`; `/health` 접속 불가 (`src/main.py:200-206` fail-closed) |

2단계 전 재확인: #199 병합 뒤 L02가 1이 되는지, #194 4단계·#197 병합 뒤 같은 체크리스트 전체를 리허설 환경(#207)에서 먼저 실행한다.

## template A — `public-smoke-<sha7>.md`

```md
# D5-03 공개 smoke — <sha7>
- 실행 SHA(전체): / 실행자: / 시각(UTC): / 실행 위치(셸 환경):
- 승인 참조: d5-03-approval.md §6 댓글 링크
| ID | 실제 | 결과(passed/failed/not run) | 이유 |
| --- | --- | --- | --- |
| P01 … E01 (smoke-checklist.md 표의 모든 ID) | | | |
- 판정: 전 행 passed / failed 있음 → rollback-decision-<sha7>.md
```

## template B — `link-check-<sha7>.md`

```md
# D5-03 tuple·링크 일치 — <sha7>
| 항목 | 승인값(d5-03-approval.md §2) | 배포 관찰값 | 일치 |
| --- | --- | --- | --- |
| normal_commit / demo_commit / candidate_commit | | | |
| #207 리허설 성공 SHA | | | |
| fixture_version / manifest_sha256 | | | |
| seed_sha256 / logical_data_sha256 | | | |
| settings_sha256 | | | |
| normal 링크 href = DEMO_PUBLIC_URL (L02) | | | |
- 하나라도 불일치: 공개 중단, "재리허설 필요" 표시, #207에 요청
```

## template C — `rollback-decision-<sha7>.md`

```md
# D5-03 rollback 결정 — <sha7>
- 공개 전 결정: rollback 대상(d5-03-approval.md §4) / 권한자 / 결정 시각
- 실행 기록(실패 시): 실패 ID / 신규 start 차단 시각 / demo 중단 / 취소·drain·dispose / 복구 대상 / normal 링크 처리
- normal DB·설정 불변 확인: / 일반 업데이트 포함 시 docs/s6-acceptance.md §3 기록 링크:
```

## template D — `handover-review-<sha7>.md`

```md
# D5-03 인계 검토 — <sha7>
- reviewer 세션(작성자와 다름): / 시각(UTC):
| 검토 | 근거 | 결과(passed/failed) |
| --- | --- | --- |
| 승인 서명·공개 URL이 d5-02-approval.md와 같음 | | |
| link-check 전 행 일치 | | |
| public-smoke 전 행 passed 또는 rollback 기록 | | |
| E01이 같은 SHA의 #203 증거 링크 | | |
| d5-03-handover.md 빈칸 없음, 비밀·실제 경로 없음 | | |
- 판정: 통과 / 미통과(완료 표시 금지)
```
