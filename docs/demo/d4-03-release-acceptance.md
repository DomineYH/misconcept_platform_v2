# D4-03 릴리스 인수 기록 (#205)

계약: `d0-03-v1-r3` 의미(§26–§28). `d0-03-v2`(§31)는 `src/utils/openai_helpers.py`·`src/utils/session_feedback.py`의 패치 필요성 판단에만 적용한다.
이 문서는 **1단계 골격**이다. 기준 main `2dc7840963e6ca700915c2c63cff7729ca54afa9`에는 G4 후보가 아직 없으므로 아래 값은 모두 "미산출"이며 G4/G5 통과를 뜻하지 않는다. 2단계에서 고정 후보 SHA 하나에 대해 채운다. 증거 파일 배치는 [`d4-03-evidence/README.md`](d4-03-evidence/README.md)를 따른다.

상태: **G5 미승인** — 후보 없음, 리뷰·회귀 미실행.

## 1. 후보 tuple (§28 C29)

값은 #202의 `docs/demo/d3-04-integration-manifest.json`에서 그대로 복사한다. manifest에 없는 값은 "미산출"로 두고 만들어 내지 않는다. 비밀 값·비밀 hash는 넣지 않는다.

| 항목 | 값 | 출처 |
| --- | --- | --- |
| `candidate_commit` | 미산출 | #202 manifest |
| `normal_commit` | 미산출 (`candidate_commit`과 동일 전체 SHA여야 함) | #202 manifest |
| `demo_commit` | 미산출 (`candidate_commit`과 동일 전체 SHA여야 함) | #202 manifest |
| `contract_version` / `contract_commit` | 미산출 | #202 manifest |
| `content_version` | 미산출 | #202 manifest |
| `fixture_version` / `manifest_sha256` | 미산출 | #202 manifest |
| `db_schema_version` / `migration_set_sha256` | 미산출 | #202 manifest |
| `seed_sha256` / `logical_data_sha256` / 생성 명령 | 미산출 | #202 manifest |
| `settings_sha256` (비밀 제외 정규화 설정·주입 참조/버전만) | 미산출 | #202 manifest |
| #203 실행 SHA (`docs/demo/d4-01-security-evidence.md`) | 미산출 | #203 evidence |
| #204 실행 SHA (`docs/demo/d4-02-browser-evidence.md`) | 미산출 | #204 evidence |

확인: #203·#204 실행 SHA = `candidate_commit` — 미확인.

## 2. 독립 리뷰 (§27 G5 / C28, §26 C27)

결과 본문은 `d4-03-evidence/review-<sha7>.md`에 둔다.

| 항목 | 값 |
| --- | --- |
| reviewer 세션 (#191–#204 제품 PR 작성자와 달라야 함) | 미지정 |
| 리뷰 대상 SHA | 미산출 |
| 리뷰 시점 main SHA | 미산출 |
| 결과 (ACCEPT / REJECT) | 미실행 |
| 결함 목록 | §5 원장 참조 |

리뷰 체크리스트:

- [ ] 후보 diff(base→후보)의 모든 경로가 계약 §26 네 표(기존 파일·추가 패치·신규 경로·기존 테스트) 중 하나에 대응한다. 표에 없는 경로는 #190 변경관리 기록 링크가 있다.
- [ ] §26 추가 패치 표의 각 행 처리 여부를 적었다: `src/api/routes/health.py`(#194), `src/cli/cleanup_comparisons.py`(#192), `src/cli/provider_smoke.py`(#198), standalone DB CLI 8개(#194), `src/db/migrations/migrate.py`(#195), `src/services/provider_reauthentication.py`(#200), 허용 CRUD route·service(#200), `src/api/routes/admin_api_usage.py`(#200). 패치 없음 행(`s2/s6_verification`, `s6_legacy`, `role_output_contracts`, `analysis_output_contract`, `htmx.min.js`)이 바뀌지 않았다.
- [ ] §31 v2: `src/utils/openai_helpers.py`·`src/utils/session_feedback.py`에 SDK guard 패치가 필요 없다는 판단을 이 두 파일에만 적용했다.
- [ ] migration: 새 번호 migration(#195)이 기존 자료를 보존하고 FK·인덱스가 계약 §12와 맞는다.
- [ ] DB 경계: 방문객 scope별 factory 분리, 쿠키/URL 조작 시 DB open 0(C02/C03).
- [ ] 권한·CSRF: 위험 method 직접 요청 403, 정상 서비스 쿠키를 demo에서 인증하지 않음(C10/C20).
- [ ] AI 경계: demo에서 SDK·HTTP·socket 외부 시도 0, demo key는 NULL만(C12/C14).
- [ ] maintainer가 작성한 코드도 이 리뷰 결과에 연결되어 있다.

## 3. 검사 결과 (§28 검사 문단)

고정 후보를 checkout한 환경에서 실행한다. 로그는 `d4-03-evidence/`에 두며 비밀·실제 경로·cookie 값·대화 원문을 넣지 않는다. 결과는 passed / failed / not run 중 하나이며 not run에는 이유를 적는다.

| 명령 | SHA | 환경 | exit | 결과 | 증거 | 이유 |
| --- | --- | --- | --- | --- | --- | --- |
| `uv run --frozen python -m pytest -q` | 미산출 | — | — | not run | `pytest-<sha7>.log` | G4 후보 없음 |
| `uv run --frozen ruff check .` | 미산출 | — | — | not run | `ruff-<sha7>.log` | G4 후보 없음 |
| `uv run --frozen black --check .` | 미산출 | — | — | not run | `black-<sha7>.log` | G4 후보 없음 |
| `npm run test:browser` | 미산출 | — | — | not run | `browser-shard-{1,2,3}-<sha7>.json` | G4 후보 없음 |
| `uv run --frozen python tests/check_student_live.py` | 미산출 | — | — | not run | `live-<sha7>.log` | G4 후보 없음 |
| `uv run --frozen python -m pytest -q tests/test_ci_controls.py` | — | — | — | 해당 없음 | — | tests.yml 변경 없음 |

## 4. normal/demo 적용 범위 (이슈 §11)

1단계 평가: 두 mode가 이미 기존 CI job `test`·`browser-shard`/`browser`에서 실행되므로 `.github/workflows/tests.yml`을 **변경하지 않는다**. job/step/테스트별 근거는 [`d4-03-evidence/normal-demo-coverage.md`](d4-03-evidence/normal-demo-coverage.md)에 있다. 2단계에서 후보 SHA 기준으로 그 표를 다시 확인한다.

§28 필수 검증 matrix 대조(이슈 §10 A–I). #205는 재구현하지 않고 #203/#204 증거의 해당 행을 연결한다. 빠진 행은 not run과 이유로 적는다.

| §28 matrix 행 | #203 증거 | #204 증거 | 결과 |
| --- | --- | --- | --- |
| 두 browser context A/B 격리 | 미산출 | 미산출 | not run (후보 없음) |
| normal 부정 | 미산출 | 미산출 | not run (후보 없음) |
| AI 무호출 | 미산출 | 미산출 | not run (후보 없음) |
| 실제 CSRF/cookie | 미산출 | 미산출 | not run (후보 없음) |
| reset race | 미산출 | 미산출 | not run (후보 없음) |
| 출력/재전송 | 미산출 | 미산출 | not run (후보 없음) |
| 삭제/export | 미산출 | 미산출 | not run (후보 없음) |
| 실제 여정/복구 | 미산출 | 미산출 | not run (후보 없음) |

## 5. 결함·재검증 원장

#203/#204/리뷰에서 나온 결함은 §26 소유 이슈가 고친다. #205는 고치지 않고 기록·재검증만 한다. 미해결 결함이 하나라도 있으면 승인 불가다.

| # | 결함 (file:line, 재현) | 발견 출처 | 소유 이슈 | 수정 PR | 새 후보 SHA | 재검증 결과 |
| --- | --- | --- | --- | --- | --- | --- |
| — | 없음 (1단계) | — | — | — | — | — |

## 6. 무효화 기록

리뷰 뒤 main 변경·rebase·계약 변경이 있으면 무효화된 항목, 재검사 이유와 결과를 `d4-03-evidence/invalidation-log.md`에 적는다. 보안·DB·공유 API에 영향을 주는 변경이면 재검사 범위를 넓힌다. 1단계에는 기록할 무효화가 없다.

## 7. 승인 기록 (G5)

G5 승인은 PR이 아니라 이슈 댓글로 남기며, 여기에는 그 링크만 적는다. 승인 완료로 표시하려면 같은 `candidate_commit`에서 다음이 모두 성립해야 한다(계약 §27 G5, §28 matrix). 하나라도 빠지거나 다른 SHA의 결과이면 승인 완료로 표시하지 않는다.

- §2 독립 리뷰 결과가 ACCEPT다.
- §4 matrix 여덟 행 모두에 #203/#204 증거가 연결되어 있고 결과가 passed다. not run이나 failed인 행이 없다.
- §3 필수 검사가 모두 passed다. failed 또는 not run인 행이 없다.
- §5에 미해결 결함이 없다.

1단계에서는 위 조건이 모두 미충족(pending)이다.

| 항목 | 값 |
| --- | --- |
| 승인자 | @DomineYH (미승인) |
| 승인 댓글 | 미산출 |
| 승인 시각 (UTC) | 미산출 |
| 승인 시점 main SHA | 미산출 |
| 리뷰 시점 main SHA와 같은가 | 미확인 (다르면 §6 기록 필요) |
