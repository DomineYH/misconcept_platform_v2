> 독립 reviewer r205rev의 릴리스 재리뷰 원문 사본(2026-10-20). 본문이 언급하는 `c27-ledger-8f97776-snapshot.txt`, `release-8f97776-checks.json`은 reviewer 작업 공간에 있고 이 저장소에는 넣지 않았다. 본문의 행 번호(`:59–75` 등)는 reviewer가 읽은 작업 중 파일 기준이다.

# Independent release re-review — ACCEPT

Reviewed release candidate: `8f97776dcb3456b5df0fb198daf520168752117c`.
Base: `427890033d206a4f043ffc946009bd8ce6a437c2`.
Reviewer: independent Codex session r205/r205rev; same reviewer as `review-ba64b82.md` and `fix-review-9b48f26.md`.

This is the full independent review verdict for the candidate, carrying forward the unchanged items from the original release review and closing both findings. It is not maintainer G5 approval or a claim that candidate CI has been independently verified.

## Re-review results

| Item | Result | Evidence |
| --- | --- | --- |
| P1 closed in merged candidate | PASS | `git diff 9b48f26c0366349a2a9fb70fb1cd6a651c353ce5 8f97776dcb3456b5df0fb198daf520168752117c -- src` is empty. The unrestricted tree diff is also empty, so the merged regression test is identical too. `src/services/analysis_runs.py:166–171` acquires admission before visitor guard and session resource lock. The PASS in `fix-review-9b48f26.md` applies unchanged. |
| P2 C27 disposition recorded | PASS | Current working-file ledger `docs/demo/d4-03-release-acceptance.md:59–75` records exactly the authorized eight CI/documentation exceptions and the #191 package-initializer assignment. Details below. |
| No additional changes | PASS | The merge's parents are ba64b82 and 9b48f26. `git diff ba64b82 8f97776` changes only `src/services/analysis_runs.py` (lock reorder/comment, +2/−1) and adds `tests/test_lock_order.py` (81 lines). No other product, test, configuration, fixture or documentation delta. |

P1 closure rests on the already completed fix review: the fresh controlled diagnostic reproduced the old deadlock and showed both fixed reservations reaching their database bodies with locks released; the complete lock-site audit found no remaining reverse edge; the regression is meaningful; normal-mode behavior was reviewed. No identical tests were rerun for this merge-only re-review.

## C27 ledger verification

The ledger was read from the requested worktree as of 2026-10-20 09:00:17 UTC, independently of its potentially lagging HEAD. Snapshot: `c27-ledger-8f97776-snapshot.txt`. Full working-file SHA256 at that read: `953a1d2531411e9ff0d7ee8fee43ca7b6fcc42216490571848045da9d076130c`.

At lines 65–72 it records these exact eight paths as maintainer-owned, approved release-range exceptions for CI-performance commits `f2b638b` / `99f9481` / `ca50e83`, outside demo scope, reviewed and CI-gated in their own PRs:

- `docs/ci-performance-runbook.ko.md`
- `tests/aggregate_browser_checks.mjs`
- `tests/browser_duration_estimates.json`
- `tests/ci_browser_sharding.mjs`
- `tests/ci_browser_sharding.test.mjs`
- `tests/ci_controls.test.mjs`
- `tests/run_browser_checks.mjs`
- `tests/test_ci_controls.py`

Line 73 assigns the empty `src/demo/__init__.py` to #191 by coordinator decision. This is the exact named-file disposition requested; it is not treated as a blanket authorization for other unlisted demo files. The authoritative approval is the coordinator decision supplied in this re-review order, now reflected in the ledger. I did not independently retrieve those historical PRs or CI runs.

Updated mapping totals: **326 paths, zero unresolved** = 272 explicitly mapped + 21 generic migration/dedicated-test mappings + 33 approved additional mappings/exceptions. Relative to the original 325-path ledger, the nine unresolved entries are now approved, and the sole new path is `tests/test_lock_order.py`, the dedicated regression under the bounded #192/#198 fix. `analysis_runs.py` already had ownership and fix authorization. No paths disappeared from the release range.

The ledger's surrounding candidate/CI/D6 status sections still contained pending/pre-merge placeholders at the time read. They are an in-progress acceptance record, not evidence that the merged fix is absent. This report supplies the requested current candidate review; #205 must reconcile those sections and record candidate CI before claiming G5 completion.

## Original release items carried forward

| Original item | Candidate verdict | Basis / retained limits |
| --- | --- | --- |
| 1. C27 every-path ownership | PASS | P2 resolved as above; 326 paths mapped. |
| 2a. Migration 037 correctness / failure rollback | PASS, static review | Unchanged from `review-ba64b82.md`; schema/installer and preservation/rollback tests inspected there. No down-migration or backup-restoration execution claimed. |
| 2b. DB isolation, guards, leases, reset/cleanup | PASS, reviewed scope | Original sole concrete defect P1 is fixed and independently passed. Other original inspected boundaries are unchanged. Fix audit/currentness/reset assessment is carried from `fix-review-9b48f26.md`. |
| 2c. Permission / CSRF / session boundaries | PASS, spot-check | Unchanged source and original findings. |
| 2d. AI boundary / secrets | PASS, spot-check | Unchanged source and original evidence attribution; no new egress measurement claimed. |
| 3. Normal-mode regression | PASS, spot-check | Original review plus the fix review's no-op normal guard / single resource-lock-site assessment and reused normal analysis test logs. |
| 4. Evidence consistency | PASS with explicit invalidation boundary | Historical G3/G4 evidence and ancestry conclusions remain; the candidate now differs from G4 product code by the reviewed lock fix, so the old zero-product-diff statement applies only through ba64b82. Affected analysis/reset/stream evidence must be linked to candidate CI, as the acceptance record already requires. E4 expiry and BFCache restore remain disclosed not-run cases. |

No remaining independent-review findings for this candidate. The prior REJECT is superseded by this ACCEPT for `8f97776`.

## Verification and limitations

Fresh: pinned-SHA `git show`/`git diff` checks only; full-tree equivalence to the passed fix; exact two-path delta; release-range path-set comparison (325 → 326); current C27 ledger snapshot and hash. Machine-readable results: `release-8f97776-checks.json`. No worktree edits, network calls, worker messages or source fixes.

Not rerun: pytest, browsers, live checks, lints, migration execution, or CI retrieval. The prior reviewer pytest fixture-setup stall remains disclosed in `fix-review-9b48f26.md`; it was not converted into a passing run. The successful focused lock diagnostic and implementer test evidence retain their original attribution.

Remaining release-gate requirements: #205 must finalize the candidate/CI/invalidation record, and the maintainer must give G5 approval, including disposition of the known not-run cases. This ACCEPT is the independent review component, not a substitute for those gates.
