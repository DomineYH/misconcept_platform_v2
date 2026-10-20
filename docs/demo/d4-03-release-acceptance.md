# D4-03 릴리스 인수 기록 (#205)

계약: `d0-03-v1-r3` 의미(§26–§28). `d0-03-v2`(§31)는 `src/utils/openai_helpers.py`·`src/utils/session_feedback.py`의 패치 필요성 판단에만 적용한다. 증거 파일 배치는 [`d4-03-evidence/README.md`](d4-03-evidence/README.md)를 따른다.

상태: **G5 미승인 — maintainer 승인 대기.** 독립 리뷰 r205가 main `ba64b82ef74226094a5b51888b1bbcb1734bb062`을 REJECT했다(§2, 결함 D6). D6 수정(`fix/lock-order`) 병합 뒤의 새 main SHA가 릴리스 후보가 되고, 그 SHA의 CI와 r205 재리뷰로 아래 "새 후보" 칸을 채운다. `ba64b82` 결과는 수정 전 기준선(pre-fix baseline)으로만 남기며 G5 근거가 아니다.

| 구분 | SHA | 역할 |
| --- | --- | --- |
| G4 승인 후보 | `de3054a00282e1bbfc97784918059f653ac08811` | #202 manifest `candidate_commit`, #202/#203/#204 G4 증거의 제품 코드 |
| 수정 전 기준선 | `ba64b82ef74226094a5b51888b1bbcb1734bb062` | de3054a와 제품 동일(test·doc만 추가). 리뷰 r205 REJECT, CI 기준선 |
| 릴리스 후보 (새 후보) | 미산출 — D6 수정 병합 대기 | G5 대상. CI·재리뷰·이 기록의 최종 값 |

## 1. 후보 tuple (§28 C29)

값은 #202의 `docs/demo/d3-04-integration-manifest.json`에서 그대로 복사했다(G4 후보 de3054a 기준). manifest에 없는 값은 만들어 내지 않는다. 비밀 값·비밀 hash는 넣지 않는다.

| 항목 | 값 (manifest) | 새 후보에서 |
| --- | --- | --- |
| `candidate_commit` | `de3054a00282e1bbfc97784918059f653ac08811` | 새 후보 SHA로 교체 |
| `normal_commit` | `de3054a00282e1bbfc97784918059f653ac08811` | 새 후보 SHA와 같아야 함 |
| `demo_commit` | `de3054a00282e1bbfc97784918059f653ac08811` | 새 후보 SHA와 같아야 함 |
| `tested_commit` (#202 smoke 실행 4) | `7c8b6e1516c9302baa63f1d550b031f06a52ac61` | 새 후보 CI run으로 대체 |
| `contract_version` / `contract_commit` | `d0-03-v1-r3` / `d6455d10c2d75acde811f85a53143be086d67545` | 계약 문서 변경 없으면 유지 |
| `content_version` | `d0-02-v1` | 유지 예상 |
| `fixture_version` / `manifest_sha256` | `demo-fixture-v1` / `60cc355c4a5cee0cf1316bb97d8c52b09a03e39bb242514f66062e34159c0516` | fixture 변경 없으면 유지 |
| `db_schema_version` / `migration_set_sha256` | `037_demo_provider.sql` / `d245be48d9b05c13264a43b146343388d85d81a43ad199490054ee45f79beec1` | migration 변경 없으면 유지 |
| `seed_sha256` / `logical_data_sha256` / 생성 명령 | `095e0d17568ef6033533c3e3beabc952130a76a7bad2ed1ca079fea55dca6987`(물리 bytes, build마다 다름) / `93808c612ae6496a31f100dde1ce5c6780a3a6fd8cd136b82bc871eb6d746141` / `python -m src.demo.seed build --out <tmp>/seed` | `logical_data_sha256` 유지 예상, CI receipt로 재확인 |
| `settings_sha256` | 미산출 (manifest `null`: 정규화 절차·배포 주입 참조가 아직 없음, D5-02 입력) | 미산출 |
| #203 실행 SHA (`d4-01-security-evidence.md`) | 검사 commit `b4ac931`, 후보 `de3054a`, 제품 diff 0 | D6가 분석·reset 경로를 바꾸므로 새 후보 CI pytest로 재실행 |
| #204 실행 SHA (`d4-02-browser-evidence.md`) | 실행 commit `fdb3096`, 후보 `de3054a`, 제품 diff 0 | 새 후보 CI browser shard로 재실행 |
| #202 실행 SHA (`d3-04-integration-evidence.md`) | 실행 commit `7c8b6e1`, 후보 `de3054a`, 제품 diff 0 | 새 후보 CI browser shard로 재실행 |

확인(기준선): `git diff --stat de3054a ba64b82ef74226094a5b51888b1bbcb1734bb062 -- src static migrations` 출력 없음. #203/#204/#202의 검사 파일은 각 실행 commit과 `ba64b82`에서 같다([`invalidation-log.md`](d4-03-evidence/invalidation-log.md) §1). 새 후보에서는 제품 diff가 D6 수정뿐인지 같은 명령으로 다시 확인한다.

## 2. 독립 리뷰 (§27 G5 / C28, §26 C27)

결과 본문은 `d4-03-evidence/review-<sha7>.md`에 둔다.

| 항목 | 기준선 리뷰 | 새 후보 재리뷰 |
| --- | --- | --- |
| reviewer 세션 | r205 (codex / gpt-6-astra / medium). #191–#204 제품 PR 작성·리뷰 이력 없는 새 세션 | r205 |
| 리뷰 대상 SHA | `ba64b82ef74226094a5b51888b1bbcb1734bb062` (base `427890033d206a4f043ffc946009bd8ce6a437c2`, 경로 325개) | 미산출 |
| 리뷰 시점 main SHA | `ba64b82ef74226094a5b51888b1bbcb1734bb062` | 미산출 |
| 결과 | **REJECT** — [`review-ba64b82.md`](d4-03-evidence/review-ba64b82.md) | pending |
| 결함 | P1 lock 순서 교착 → §5 D6. P2 C27 소유 기록 누락 9경로 → 아래 C27 원장(문서 추적 결함, 제품 결함 아님) | pending |

기준선 리뷰 체크리스트 결과(`review-ba64b82.md` "Per-item results"):

- [ ] C27 모든 경로 대응: FAIL — 325개 중 316개 대응, 9개 미대응(P2). 아래 C27 원장으로 9개를 기록했다. 재리뷰에서 확인한다.
- [x] §26 추가 패치 표 행: CLI/health/재인증/CRUD 패치가 diff에 있고 진입·guard 경계를 검토. `admin_scenario_ops.py`·`admin_api_usage.py`는 바뀌지 않았다(quota는 guard된 route 코드, 방문객 scope query 재사용).
- [x] §31 v2: 두 utils 파일은 바뀌지 않았다.
- [x] migration 037: PASS (정적 검토). down migration 없음, 업그레이드 뒤 downgrade·backup 복원은 실행하지 않음.
- [ ] DB 경계·write guard·lease·reset: FAIL — P1(D6) 가용성 결함. 그 밖의 방문객 간 노출·늦은 쓰기 결함은 찾지 못함.
- [x] 권한·CSRF·세션 경계: PASS (spot-check).
- [x] AI 경계·비밀: PASS (spot-check). egress 0은 #203 최종 증거 재사용.
- [x] normal 회귀 spot-check: PASS.
- [x] 증거 일관성: PASS (E4·BFCache not run 공개).

### C27 경로 원장 (§26 `d0-03-contract-v1.md:701`)

기준선 리뷰의 경로 분류(합계 325): §26 명시 소유 272, §26 일반 허용(새 번호 migration 1, 전용 test·fixture 19) 20, 별도 승인 기록 24, 미대응 9. 미대응 9개는 coordinator 결정(2026-10-20)으로 다음과 같이 기록한다.

| 경로 | 처리 | 소유 | 근거 |
| --- | --- | --- | --- |
| `docs/ci-performance-runbook.ko.md` | 승인된 릴리스 범위 예외 | maintainer | maintainer CI 성능 작업(commit `f2b638b`, `99f9481`, `ca50e83`). demo 계약 범위 밖이며 해당 PR에서 리뷰·CI gate를 거쳤다 |
| `tests/aggregate_browser_checks.mjs` | 같음 | maintainer | 같음 |
| `tests/browser_duration_estimates.json` | 같음 | maintainer | 같음 |
| `tests/ci_browser_sharding.mjs` | 같음 | maintainer | 같음 |
| `tests/ci_browser_sharding.test.mjs` | 같음 | maintainer | 같음 |
| `tests/ci_controls.test.mjs` | 같음 | maintainer | 같음 |
| `tests/run_browser_checks.mjs` | 같음 | maintainer | 같음 |
| `tests/test_ci_controls.py` | 같음 | maintainer | 같음 |
| `src/demo/__init__.py` (빈 파일) | §26 `src/demo/` 소유 배정 | #191 | coordinator 결정. 동작 없는 package initializer |

새 후보의 diff에 D6 수정 경로가 더해지면 그 경로의 소유(#192/#198)를 재리뷰에서 확인한다.

## 3. 검사 결과 (§28 검사 문단)

전체 회귀는 고정 SHA의 GitHub Actions "Regression tests" run이다(`tests.yml` job `test`·`browser-shard`×3·`browser`). 로그는 CI job 로그 발췌로 `d4-03-evidence/`에 두며 runner 작업 경로·ANSI 코드를 가렸고 비밀·cookie 값·대화 원문은 없다. 결과는 passed / failed / not run 중 하나이며 not run에는 이유를 적는다.

### 3.1 새 릴리스 후보 (G5 근거)

| 명령 | SHA | 환경 | exit | 결과 | 증거 | 이유 |
| --- | --- | --- | --- | --- | --- | --- |
| `uv run --frozen python -m pytest -q` | 미산출 | — | — | not run | `pytest-<sha7>.log` | D6 수정 병합 대기 |
| `uv run --frozen ruff check .` | 미산출 | — | — | not run | `ruff-<sha7>.log` | 같음 |
| `uv run --frozen black --check .` | 미산출 | — | — | not run | `black-<sha7>.log` | 같음 |
| `npm run test:browser` | 미산출 | — | — | not run | `browser-shard-{1,2,3}-<sha7>.json` | 같음 |
| `uv run --frozen python tests/check_student_live.py` | 미산출 | — | — | not run | `live-<sha7>.log` | 같음 |
| `uv run --frozen python -m pytest -q tests/test_ci_controls.py` | — | — | — | 해당 없음 | — | tests.yml 변경 없음 |

### 3.2 수정 전 기준선 `ba64b82` (G5 근거 아님, 리뷰 REJECT)

CI run [39392009613](https://github.com/DomineYH/misconcept_platform_v2/actions/runs/39392009613), main push, head `ba64b82ef74226094a5b51888b1bbcb1734bb062`, attempt 1(재실행 없음), 2026-10-20 08:14:49–08:37:47 UTC, run 결론 success. 환경: GitHub Actions ubuntu-24.04, CPython 3.12.3(`uv sync --frozen --extra dev --python 3.12`), Node 24, checkout clean. flake 재실행 없음.

| 명령 (CI step) | SHA | 환경 | exit | 결과 | 증거 | 이유·수치 |
| --- | --- | --- | --- | --- | --- | --- |
| `uv run --frozen python -m pytest -q --durations=30` | `ba64b82` | job `test` | 0 | passed | [`pytest-ba64b82.log`](d4-03-evidence/pytest-ba64b82.log) | 3166 passed, 0 failed, 0 skipped, 1489 warnings, 1338.76 s. worker 1(xdist 없음) |
| `uv run --frozen ruff check .` | `ba64b82` | job `test` | 0 | passed | [`ruff-ba64b82.log`](d4-03-evidence/ruff-ba64b82.log) | All checks passed |
| `uv run --frozen black --check .` | `ba64b82` | job `test` | 0 | passed | [`black-ba64b82.log`](d4-03-evidence/black-ba64b82.log) | 변경 필요 파일 없음 |
| `npm run test:browser -- --shard N/3` + `aggregate_browser_checks.mjs` | `ba64b82` | job `browser-shard` (1)(2)(3), `browser` | 0 | passed | [`browser-shard-1-ba64b82.json`](d4-03-evidence/browser-shard-1-ba64b82.json), [`-2-`](d4-03-evidence/browser-shard-2-ba64b82.json), [`-3-`](d4-03-evidence/browser-shard-3-ba64b82.json) | 148 모듈 PASS(shard 1/2/3 = 3/72/73), 집계 "Browser fleet PASS: 148 modules across 3 shards". demo 모듈 6개 포함(`browser_demo_d3_04_smoke`·`browser_demo_recovery` shard 1, `browser_demo_isolation`·`browser_demo_d3_01_ui` shard 2, `browser_demo_journey`·`browser_demo_d3_03_recovery` shard 3) |
| `uv run --frozen python tests/check_student_live.py` | `ba64b82` | job `test` | 0 | passed | [`live-ba64b82.log`](d4-03-evidence/live-ba64b82.log) | PASS, 학생 호출 1, first output 150 ms |
| `uv run --frozen python -m pytest -q tests/test_ci_controls.py` | — | — | — | 해당 없음 | — | tests.yml 변경 없음(전체 pytest에는 포함) |

기준선이 모두 passed여도 D6는 기존 검사가 잡지 못한 교착이므로 G5 근거가 되지 않는다. 새 후보 CI는 D6 회귀 검사를 더한 suite로 3.1을 채운다.

## 4. normal/demo 적용 범위 (이슈 §11)

두 mode가 기존 CI job `test`·`browser-shard`/`browser`에서 실행되므로 `.github/workflows/tests.yml`을 **변경하지 않는다**(`ba64b82` 기준 재확인). job/step/테스트별 근거는 [`d4-03-evidence/normal-demo-coverage.md`](d4-03-evidence/normal-demo-coverage.md)에 있다.

§28 필수 검증 matrix 대조(이슈 §10 A–I 아홉 행). #205는 재구현하지 않고 #203/#204/#202 증거의 해당 행을 연결한다. 증거가 없는 부분은 not run과 이유로 적는다. 경로는 `docs/demo/` 기준이다.

| 행 | 검증 내용 | #203 증거 | #204 증거 | #202 증거 | 실행 SHA | 결과 |
| --- | --- | --- | --- | --- | --- | --- |
| A | 실제 브라우저 여섯 단계 (실제 서버·backend·CSRF, CSV 파싱) | 보조: `tests/test_demo_isolation.py::test_teacher_csv_has_prefix_20_and_demo_suffix_5` (`d4-01-evidence/g4-isolation.log`) | `browser_demo_journey.mjs` passed 25.8 s: 여섯 단계 map·TourFacts, 투어 CSV 파일 파싱(27+5열, 6행), 삭제 §22, reset·이전 탭 409 (`d4-02-evidence/browser-de3054a.log`, `journey-admin.csv`, `journey-*.png`) | `tests/browser_demo_d3_04_smoke.mjs` 실행 4: step 1–6 passed (`d3-04-evidence/run-4.log`, `result-4.json`) | #203 `b4ac931`, #204 `fdb3096`, #202 `7c8b6e1` (모두 후보 `de3054a`) | passed |
| B | 두 방문객 동시 격리 (A/B context, 위조, ID 교차) | `tests/test_demo_isolation.py` 7 passed (`g4-isolation.log`): `test_http_visitors_are_isolated_through_mutation_forgery_and_reset`, `test_conversation_analysis_and_run_ledger_stay_private_per_visitor`, `test_http_delete_and_csv_export_stay_private_and_skip_deleted_rows` + unit/synthetic 3건 | `browser_demo_isolation.mjs` passed 20.7 s: 두 context, 서명 위조 401, `path`/`visitor_id` 주입 422, namespace 409, B 총계·목록 불변; `browser_demo_journey.mjs` B 격리 세 번 (`browser-de3054a.log`) | 해당 없음(smoke는 단일 방문객, `d3-04-integration-evidence.md` 범위 밖 절). 비교 ID 교차는 비교 실행이 MVP 범위 밖이라 검사 없음 | #203 `b4ac931`, #204 `fdb3096` | passed |
| C | 운영 모드 부정 (normal에서 demo 404, 주입 무효) | `tests/test_demo_security.py` 17 passed (`g4-security.log`): `test_normal_mode_ignores_client_mode_switches`, `test_normal_admin_api_rejects_the_demo_provider[baseline]`(둘 다 unit, 부모 프로세스 `TESTING=true`), `test_demo_import_refuses_invalid_app_mode[*]`, `test_dotenv_is_ignored_in_demo_and_read_in_normal` | `browser_demo_isolation.mjs`: 실제 서버 `--normal`에서 `GET /demo`·`/demo/state`·`POST /demo/start`·`/demo/role`·`/demo/reset` 404, demo cookie 없음 | N1 passed: `APP_MODE=normal`, `TESTING=false`, `/health` 200, admin 로그인 → `/scenarios`, `/demo` 404 (`run-4.log`) | #203 `b4ac931`, #204 `fdb3096`, #202 `7c8b6e1` | passed (정상 모드 전체 회귀는 §3 CI) |
| D | 외부 AI 호출 0 (SDK·HTTP·socket 감시, 키 거절) | `test_demo_lifecycle_makes_no_external_calls`, `test_demo_runs_make_no_external_calls`, `test_demo_admin_dangerous_endpoints_are_forbidden`, `test_demo_rejects_injected_external_model`, `test_demo_config_refuses_every_forbidden_input`, `test_demo_import_refuses_forbidden_input[*]`, `test_demo_import_refuses_bootstrap_admin` (`g4-security.log`) | `browser_demo_isolation.mjs` helper: 외부 socket/SDK 시도 0 (`d4-02-browser-evidence.md` 실행 1 항목, G4 실행에 포함) | egress guard 없음(`d3-04-integration-evidence.md` 범위 밖 절). stream provenance `external_ai_called=false`만 확인 (step 3) | #203 `b4ac931`, #204 `fdb3096`, #202 `7c8b6e1` | passed (비교 실행 경로는 MVP 범위 밖: 직접 요청 거절만 #202 E5 passed) |
| E | 초기화·만료·취소 경합 | `tests/test_demo_reset.py` 25 passed (`g4-reset.log`): `test_reset_during_student_mentor_analysis_and_shield_ledger_writes[*]` 10건(5 지점 × 2 순서), `test_reset_close_failure_is_db_locked_and_keeps_lease_and_file`, `test_caller_disconnect_after_mark_still_completes_reset`, `test_reset_alone_drains_and_separate_live_lease_times_out`, `test_two_tabs_send_different_reset_ids_concurrently`, unit/synthetic: `test_duplicate_reset_increments_generation_once`, `test_ttl_during_reset_preparation_beats_publish`, 재시작·잠금·저장 상한·257번째 request_id | `browser_demo_recovery.mjs` I4(두 탭 role/reset 안내·409), I7(테스트 시계로 TTL 경과 후 410 `DEMO_EXPIRED`·명시 start) passed; journey 7단계 reset 뒤 이전 탭 409 | step 6 reset·`DEMO_STALE_GENERATION` passed. **E4 만료 후 재진입 not run**: `DEMO_IDLE_TTL_SECONDS` 하한 300초(`src/demo/visitors.py:49`)라 CI shard에서 대기 불가, G4에서 수용 | #203 `b4ac931`, #204 `fdb3096`, #202 `7c8b6e1` | passed, 단 #202 E4 not run (TTL 하한 300초, G4 수용). 실제 시계 만료는 검사하지 않았고 I7·unit만 있음. 비교 실행 도중 reset은 MVP 범위 밖 |
| F | 인증·CSRF (`TESTING=false`, 쿠키 속성, 교차 인증, 역할 삭제) | `test_demo_csrf_origin_cookie_matrix`, `test_demo_cross_auth_and_role_deleted` (`g4-security.log`) | `browser_demo_isolation.mjs`: §10 쿠키 속성, CSRF·Origin 행렬 403 `DEMO_CSRF_INVALID`, 삭제 교사 전환 409 `DEMO_ROLE_DELETED`, 관리자 자기 삭제 400 | E2 passed: 역할 전환 `auth_epoch`+1, 이전 epoch 409 `DEMO_STALE_ROLE`; step 6 teacher 전환 409 `DEMO_ROLE_DELETED` | #203 `b4ac931`, #204 `fdb3096`, #202 `7c8b6e1` | passed |
| G | 가짜 AI도 계약 준수 (결정성, replay, retry, SSE, 오류·무효 출력, 멘토 미개입, 분석 부분 성공) | `test_demo_runs_make_no_external_calls`: 같은 request_id replay, 명시 retry(같은 turn·새 run), 멘토 수동·자동, 교사·관리자 분석 | `browser_demo_journey.mjs` 1–4단계: 완료 학생 턴 2·자동 코칭 1·미개입, 분석 열람 | step 3 stream 4개 provenance, step 4 분석 `feedback_status=ok`·분포, E6 seed provenance passed | #203 `b4ac931`, #204 `fdb3096`, #202 `7c8b6e1` | passed (정상 경로·replay·retry). 오류·부분 성공 경로는 #203/#204/#202 증거에 없고 #196 `tests/test_demo_provider.py`가 CI pytest에서 검사한다: `test_failure_cases_are_deterministic_and_never_substituted`(:408, timeout·transient·invalid_output), `test_cancellation_produces_one_interrupted_terminal`(:588), `test_analysis_partial_retains_validated_sections_and_error`(:1328), `test_analysis_failed_has_no_adoptable_result`(:1480), `test_total_deadline_cancellation_keeps_timeout_code`(:1493), `test_mentor_first_and_unsupported_auto_contract`(:1409, 미개입). 결과는 §3 pytest |
| H | 삭제·내보내기 (삭제 세션·교사 차단, export, reset 비영향) | `test_http_delete_and_csv_export_stay_private_and_skip_deleted_rows`, `test_teacher_csv_has_prefix_20_and_demo_suffix_5` (`g4-isolation.log`) | `browser_demo_journey.mjs` 삭제 §22: 삭제 세션 교사 6경로 404, 관리자 download·analysis·modal·run 404, 전체 export에서 제외, 선택 export CSV 없음, 삭제 교사 export 404 (`d4-02-browser-evidence.md` G4 최종 실행) | step 6: 교사 삭제 뒤 CSV 교사 식별 열 빈 값, 세션 삭제 뒤 download·analysis 404, tombstone 본문 없음; step 5 CSV 32열·6행 | #203 `b4ac931`, #204 `fdb3096`, #202 `7c8b6e1` | passed (기존 `tests/test_deleted_session_access.py` 등 회귀는 §3 CI) |
| I | 브라우저 복구·언어·접근성 | 해당 없음(브라우저 검사 아님) | `browser_demo_recovery.mjs` I1·I2–I7 passed (모듈 36.2 s): 새로고침·뒤로·앞으로, 이중 클릭, 투어 닫기·건너뛰기·재개, 두 탭, ko/en(미전송 초안 보호), 390 px·200% 확대·키보드, 만료 재진입(테스트 시계). **I1b BFCache 복원 not run**: demo 응답 `Cache-Control: no-store`(계약 §11)가 우선해 Chromium 153이 복원하지 않음(`notRestoredReasons`: `response-cache-control-no-store`, `response-cache-control-no-store-with-js-network-request`, `masked`). `d4-02-browser-evidence.md`가 "계약 두 조항(§11, §25) 사이의 결정 사항"으로 기록, 검사에서 우회하지 않음 | E3 한영 전환 passed; E4 만료 후 재진입 not run (TTL 하한 300초) | #204 `fdb3096`, #202 `7c8b6e1` | passed, 단 I1b BFCache 복원 not run (§11 no-store 우선, 결정 사항으로 기록)·#202 E4 not run (G4 수용) |

- #203 G4 검사는 후보 `de3054a` 위 test·doc commit `b4ac931`(제품 diff 0), #204는 `fdb3096`(후보 대비 제품 diff 0), #202는 `7c8b6e1`(후보 대비 제품 diff 0)에서 실행했다. 세 증거 모두 같은 후보 `de3054a`의 제품 코드를 검사한 것이다.
- 릴리스 후보 main `ba64b82ef74226094a5b51888b1bbcb1734bb062`는 `de3054a` 대비 `src`·`static` diff가 비어 있고 이후 병합(#253 = #203, #255 = #204, #254 = #202)은 test·doc뿐이다. 근거와 재확인은 `d4-03-evidence/invalidation-log.md`에 있다.
- 알려진 not run은 #202 E4(만료 후 재진입)와 #204 I1b(BFCache 복원)로, G4에서 수용한 제한이다(`d3-04-integration-evidence.md` "G4 승인" 절). 행 G의 오류·부분 성공 경로는 위 세 증거에 없고 #196 pytest로 연결했다.
- **D6 무효화:** D6 수정은 `src/services/analysis_runs.py`의 admission·visitor guard 순서를 바꾼다. 그래서 위 표 중 분석·reset·stream 경로의 증거(행 A 4·6단계, B·E·G·H의 분석 사례, #203 reset race 10건)는 새 후보에서 다시 확인해야 한다. 새 후보 CI의 `pytest -q`가 G3(`tests/test_demo_d2_04_g3.py`)·D4-01(`tests/test_demo_isolation.py`·`test_demo_reset.py`·`test_demo_security.py`)을, browser shard가 D3-04 smoke(`browser_demo_d3_04_smoke.mjs`)와 D4-02 모듈(`browser_demo_isolation.mjs`·`browser_demo_journey.mjs`·`browser_demo_recovery.mjs`)을 다시 실행한다. 새 후보 결과는 §3에 적는다([`invalidation-log.md`](d4-03-evidence/invalidation-log.md) §2).
- 전체 회귀(pytest·ruff·black·browser·live)는 §3의 CI 결과를 쓴다. 이 표는 후보 `de3054a`의 대상 실행을 연결한다.

## 5. 결함·재검증 원장

#203/#204/리뷰에서 나온 결함은 §26 소유 이슈가 고친다. #205는 고치지 않고 기록·재검증만 한다. 미해결 결함이 하나라도 있으면 승인 불가다.

| # | 결함 (file:line, 재현) | 발견 출처 | 소유 이슈 | 수정 PR | merge SHA | 재검증 증거 | 상태 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| D1 | `src/services/analysis_plan.py:65-67` → `src/services/context_budget.py:13` `PROVIDER_ADAPTERS["demo"]` `KeyError`. demo 세션 종료·관리자 분석이 503 `DEMO_CONFIGURATION_INVALID` | #202 실행 1 step 4, #203, #204 | #198 | #251 | `fbbcad74f5e382c56cab7fbc1e42c53112125361` | #202 실행 3·4 step 4 passed (`d3-04-evidence/run-3.log`, `run-4.log`); #203 G4 `test_conversation_analysis_and_run_ledger_stay_private_per_visitor`, `test_teacher_csv_has_prefix_20_and_demo_suffix_5` (`b4ac931`); #204 G4 journey 4단계 | 해소 |
| D1b | `src/services/analysis_runs.py:379-380`, `src/services/call_admission.py:85-96`: `execution["demo_adapter"]`를 넣는 코드가 없어 run `failed`·`configuration_unavailable` | #202 실행 1 step 4, #203, #204 | #198 | #251 | `fbbcad74f5e382c56cab7fbc1e42c53112125361` | D1과 같음 | 해소 |
| D2 | `src/demo/seed.py:519-530`, `src/api/routes/admin_session_actions.py:246-258`: seed 완료 예시 분석 JSON에 §19 `provenance` 없음 | #202 실행 1 E6 | 문서에 명시 없음(manifest는 #250을 #202 항목으로 기록) | #250 | `0cc3fab04b6580b999b0ca95ca222687f7ffea85` | #202 실행 3·4 E6 passed (seed `logical_data_sha256` `93808c61…`); #204 G4 receipt 같은 값 | 해소 |
| D3 | `src/services/scenario_drafts.py:137`: demo에서 외부 모델 주입한 `POST/PUT /admin/scenarios`가 §20의 422 `DEMO_UNSUPPORTED_CONFIGURATION` 대신 필드 오류 `model_identity_mismatch` | #203 | #200 | #248 | `190d74baf317d6e267cfa0e342c067f8cb2f0f51` | #203 G4 `test_demo_rejects_injected_external_model` passed (`g4-security.log`, `b4ac931`) | 해소 |
| D4 | `src/api/routes/admin_session_actions.py:79`: 관리자 `/admin/sessions/{id}/end`·분석 재생성이 demo adapter를 받지 못해 `configuration_unavailable` | #203 | #200 | #248 | `190d74baf317d6e267cfa0e342c067f8cb2f0f51` | #203 G4 `test_demo_runs_make_no_external_calls` passed (`g4-security.log`, `b4ac931`) | 해소 |
| D6 | `src/services/analysis_runs.py:166`(visitor guard → resource lock → admission) ↔ `src/services/generation_runs.py:189`, `src/services/mentor_generation.py:48`(admission → visitor guard). 계약 §5.1(`d0-03-contract-v1.md:107`) "admission lock 다음에 방문 전환 lock; 역순 금지" 위반. demo에서 다른 방문객이 admission을 잡은 동안 A의 학생 요청과 분석 요청이 줄을 서면 서로의 lock을 기다려 교착 | 독립 리뷰 r205 P1 (`review-ba64b82.md`, lock 진단 스크립트로 확인) | #192/#198 | TBD (`fix/lock-order`) | 미산출 | 새 후보 CI pytest(동시 reservation 회귀 검사 포함) + r205 재리뷰 | **미해결** |
| D5 | `src/api/dependencies.py` `DemoRequestDB.display()`: 모든 demo 화면의 `#demo-config.tour_facts`가 null이라 투어 map이 분석 단계에서 멈추고 투어 CSV 버튼이 숨겨짐 | #204 | #194 (`d4-02-browser-evidence.md` §26 표기) | #252 | `de3054a00282e1bbfc97784918059f653ac08811` | #204 G4 `browser_demo_journey.mjs` passed 25.8 s (`fdb3096`, `d4-02-evidence/browser-de3054a.log`) | 해소 |

**미해결 결함 1(D6) — 승인 불가.** D1–D5는 후보 `de3054a`에 병합된 제품 결함이며, 재검증은 `de3054a` 위의 G4 실행(#203 `b4ac931`, #204 `fdb3096`, #202 `7c8b6e1`)이다. D6는 기준선 `ba64b82` 리뷰에서 나왔고 수정 PR 병합·새 후보 CI·재리뷰 뒤 해소로 바꾼다. 리뷰 P2(C27 소유 기록)는 제품 결함이 아니며 §2 C27 원장에 기록했다.

검사 쪽 보강(제품 결함 아님): #202 G4 review r1의 지적 둘(4단계 재열람 화면의 보고서, 6단계 tombstone 본문)은 smoke assertion 추가로 처리했고 새 제품 결함은 없었다(실행 4). #203 repair r1(일부 사례가 대체 seed를 씀)과 #204 독립 리뷰 r204g4 FAIL(삭제 검사 status·형식, 빠진 삭제 경로, BFCache 분리)도 test 쪽 수정이다.

## 6. 무효화 기록

리뷰 뒤 main 변경·rebase·계약 변경이 있으면 무효화된 항목, 재검사 이유와 결과를 `d4-03-evidence/invalidation-log.md`에 적는다. 보안·DB·공유 API에 영향을 주는 변경이면 재검사 범위를 넓힌다.

- §1: G4 후보 `de3054a` → 기준선 `ba64b82`. test·doc만 바뀌어 제품 증거 무효 없음.
- §2: 기준선 `ba64b82` → 새 후보(D6 수정). `analysis_runs.py` 제품 변경으로 분석·reset·stream 증거가 무효가 되고, 새 후보 CI 전체 회귀와 r205 재리뷰로 다시 검사한다.

## 7. 승인 기록 (G5)

G5 승인은 PR이 아니라 이슈 댓글로 남기며, 여기에는 그 링크만 적는다. 승인 완료로 표시하려면 같은 `candidate_commit`에서 다음이 모두 성립해야 한다(계약 §27 G5, §28 matrix). 하나라도 빠지거나 다른 SHA의 결과이면 승인 완료로 표시하지 않는다.

- §2 독립 리뷰 결과가 ACCEPT다.
- §4 matrix A–I 아홉 행 모두에 #203/#204/#202 증거가 연결되어 있고 결과가 passed다. failed인 행이 없다. not run 항목(#202 E4 만료 재진입, #204 I1b BFCache 복원)은 G4에서 수용한 제한이며, G5에서도 같은 제한을 수용하는지 maintainer가 승인 댓글에 명시해야 한다. 명시가 없으면 미충족이다.
- §3.1(새 후보) 필수 검사가 모두 passed다. failed 또는 not run인 행이 없다. §3.2 기준선 결과는 대신 쓸 수 없다.
- §5에 미해결 결함이 없다.

상태: **G5 미승인 — maintainer 승인 대기.** 현재 리뷰 REJECT(기준선), D6 미해결, 새 후보 SHA·CI 미산출로 조건 미충족.

| 항목 | 값 |
| --- | --- |
| 승인자 | @DomineYH (미승인) |
| 승인 댓글 | 미산출 |
| 승인 시각 (UTC) | 미산출 |
| 승인 대상 `candidate_commit` | 미산출 (새 후보) |
| 승인 시점 main SHA | 미산출 |
| 리뷰 시점 main SHA와 같은가 | 미확인 (다르면 §6 기록 필요) |
