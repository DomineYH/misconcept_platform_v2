# D4-03 무효화 기록 (#205)

후보 SHA·main·계약이 바뀌어 이전 증거가 무효가 되는지를 적는다. 항목마다 변경, 영향 범위, 무효가 된 증거, 재검사 이유·결과를 둔다.

## 1. G4 후보 de3054a → 릴리스 후보 ba64b82 (2026-10-20)

- 변경: G4 승인 후보 main `de3054a00282e1bbfc97784918059f653ac08811` 뒤에 세 PR이 병합되어 릴리스 후보가 main `ba64b82ef74226094a5b51888b1bbcb1734bb062`가 됐다.
  - #253(#203) merge `d1ef65e`: `tests/test_demo_isolation.py`, `tests/test_demo_reset.py`, `tests/test_demo_security.py`, `docs/demo/d4-01-*`.
  - #255(#204) merge `2d26eda`: `tests/browser_demo_isolation.mjs`, `tests/browser_demo_journey.mjs`, `tests/browser_demo_recovery.mjs`, `tests/demo_d4_02_server.py`, `docs/demo/d4-02-*`.
  - #254(#202) merge `ba64b82`: `tests/browser_demo_d3_04_smoke.mjs`, `docs/demo/d3-04-*`.
- 제품 diff: `git diff --stat de3054a ba64b82ef74226094a5b51888b1bbcb1734bb062 -- src static migrations` 출력 없음(exit 0). 전체 diff는 40개 파일이며 모두 `tests/` 또는 `docs/demo/` 아래다. `.github/`, `package.json`, `uv.lock`, `pyproject.toml` 변경 없음. 계약 문서(`docs/demo/d0-03-contract-v1.md`) 변경 없음.
- 검사 파일 동일성: 각 이슈의 G4 실행 commit과 ba64b82의 검사 파일이 같다(`git diff --stat` 출력 없음).
  - #203 `b4ac931` ↔ ba64b82: `tests/test_demo_isolation.py`, `tests/test_demo_reset.py`, `tests/test_demo_security.py`.
  - #204 `fdb3096` ↔ ba64b82: `tests/browser_demo_isolation.mjs`, `tests/browser_demo_journey.mjs`, `tests/browser_demo_recovery.mjs`, `tests/demo_d4_02_server.py`.
  - #202 `7c8b6e1` ↔ ba64b82: `tests/browser_demo_d3_04_smoke.mjs`.
- 무효가 된 제품 증거: **없음.** 제품 코드·migration·static·의존성·CI 설정이 같으므로 de3054a(및 그 위 test commit)에서 낸 #202/#203/#204 증거와 G4 승인은 ba64b82에도 그대로 적용된다. seed 입력(`src/demo/`, fixture, migration)도 같아 manifest의 `logical_data_sha256`·`manifest_sha256`·`migration_set_sha256`은 유지된다.
- 다시 실행한 것: 전체 회귀(ruff, black, `pytest -q`, `tests/check_student_live.py`, browser 3 shard + 집계)를 ba64b82의 CI run에서 실행했다. 이유: 병합된 test 변경이 서로 및 기존 suite와 함께 도는 것은 이 SHA에서만 확인할 수 있고, §28이 고정 후보 SHA의 전체 회귀를 요구한다. 결과는 [인수 기록 §3](../d4-03-release-acceptance.md#3-검사-결과-28-검사-문단)에 있다.
- 범위 확대 여부: 보안·DB·공유 API에 영향을 주는 변경이 없으므로 #203/#204 대상 검사를 로컬에서 다시 돌리지 않았다. 같은 검사가 CI 전체 회귀에 포함된다.

## 2. 기준선 ba64b82 → 릴리스 후보 8f97776 (D6 수정)

- 변경: 독립 리뷰 r205가 `ba64b82`를 REJECT했다(P1, [인수 기록](../d4-03-release-acceptance.md) §5 D6). 수정은 `fix/lock-order`(소유 #192/#198)에서 `src/services/analysis_runs.py`의 lock 순서를 학생·멘토 admission과 같게(admission → visitor guard) 바꾸고 동시 reservation 회귀 검사를 더한다. PR #256(head `9b48f26`)이 main `8f97776dcb3456b5df0fb198daf520168752117c`로 병합되어 릴리스 후보가 됐다. `git diff --stat ba64b82 8f97776`: `src/services/analysis_runs.py` +2/−1, `tests/test_lock_order.py` 신규 81줄. 그 밖의 diff 없음.
- 영향 범위: 제품 코드 변경이며 분석 reservation·visitor write guard·admission lock을 지난다. 따라서 분석·reset·stream 경로를 검사한 증거가 무효다.
  - #203 D4-01 G4(`b4ac931`): `tests/test_demo_reset.py`(reset race 10건 포함), `tests/test_demo_isolation.py`·`tests/test_demo_security.py`의 분석 사례.
  - #198 G3: `tests/test_demo_d2_04_g3.py`.
  - #202 D3-04 smoke 실행 4(`7c8b6e1`): 4단계 분석, 6단계 reset.
  - #204 D4-02 G4(`fdb3096`): `browser_demo_journey.mjs` 분석·reset 단계, `browser_demo_recovery.mjs` reset·두 탭 사례.
  - 기준선 `ba64b82` CI run과 리뷰 결과 전체(G5 근거로 쓰지 않는다).
- 유지되는 것: manifest의 fixture·migration·seed 값(`manifest_sha256`, `migration_set_sha256`, `logical_data_sha256`)은 수정이 `src/demo/`·fixture·migration을 바꾸지 않으면 유지된다. 확인: `src/demo/`·fixture·migration diff 없음(`-- src static` diff는 `analysis_runs.py`뿐)이므로 유지된다.
- 재검사: 새 후보의 CI run을 전체 회귀로 쓴다. `pytest -q`가 G3와 D4-01 세 파일, 새 회귀 검사를 다시 실행하고, browser 3 shard가 D3-04 smoke와 D4-02 세 모듈을 다시 실행한다. r205가 새 후보를 다시 리뷰한다. 결과: CI run [39397378254](https://github.com/DomineYH/misconcept_platform_v2/actions/runs/39397378254) attempt 1 success — pytest 3167 passed(D6 회귀 1 포함), ruff·black clean, live PASS, browser 148 모듈 PASS(D3-04 smoke, D4-02 세 모듈 포함). 수정 리뷰 [`fix-review-9b48f26.md`](fix-review-9b48f26.md) PASS, 릴리스 재리뷰 [`review-8f97776.md`](review-8f97776.md) ACCEPT. 남은 무효 증거 없음. 단 #203/#204/#202 문서의 개별 실행 기록은 `de3054a` 기준 그대로이며, 새 후보에서의 재검사는 위 CI 결과다.
