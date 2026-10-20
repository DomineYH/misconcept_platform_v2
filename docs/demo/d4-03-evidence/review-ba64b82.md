> 독립 reviewer r205의 기준선 리뷰 결과 원문 사본(2026-10-20). 본문이 언급하는 `path-mapping.tsv`, `lock_order_check.py`, `lock-order-result.txt`는 reviewer 작업 공간에 있고 이 저장소에는 넣지 않았다.

# Independent release review — REJECT

- Reviewed SHA: `ba64b82ef74226094a5b51888b1bbcb1734bb062`.
- Base: `427890033d206a4f043ffc946009bd8ce6a437c2`; two-endpoint diff, 325 paths.
- Reviewer: Codex, independent session r205 / r205rev. Work-order harness identity: `codex / gpt-6-astra / medium`.
- Fresh reviewer session; no authorship/review of #191–#204 product PRs in this session. No workers contacted.
- Contract: v1-r3 semantics; v2 §31 applied only to the two utils files, which are unchanged.
- Checkout HEAD matched the target and `git status --short` was empty. All writes are reviewer artifacts in this workspace.
- Assumed deployment: the documented single asynchronous worker, multiple concurrent visitors/tabs.

## Findings

### 1. P1 — Analysis and generation can deadlock the shared admission lock

Location: `src/services/analysis_runs.py:166` (visitor guard → resource lock → admission), versus `src/services/generation_runs.py:189` (admission → visitor guard). Mentor admission uses the latter order too (`src/services/mentor_generation.py:48`).

Failure scenario: visitor B temporarily holds admission while its reservation awaits DB I/O. A student request for visitor A queues for admission. An analysis request for A then takes A's visitor guard and queues behind the student for admission. When B releases admission, A's student takes it and waits for A's guard, which analysis holds while waiting for admission. Neither proceeds. This occurs before the analysis reservation validates its session or starts its execution deadline; even a subsequently rejectable request can participate. The global lock also stalls other visitors' admissions/rechecks. Reset/cleanup cannot acquire A's visitor transition until the cycle is broken.

Reachability: `src/api/routes/student_generation.py:197` invokes `reserve_student`; `src/api/routes/session_analysis.py:147` and `src/services/analysis_runs.py:593` reach `reserve_analysis`. `src/services/call_admission.py:28` supplies one lock per event loop. `src/demo/lifecycle.py:336` delegates demo writes to `src/demo/visitors.py:796`, whose transition takes the visitor lock at `:697`. Normal's write guard is a no-op, so this particular cycle is demo-specific.

Fresh diagnostic: `python3 lock_order_check.py` passed its assertions and printed `CONFIRMED: analysis owns visitor and waits admission; student owns admission and waits visitor.` It executes the candidate's unchanged AST-extracted `reserve_analysis` and `reserve_student` functions with asyncio locks and minimal collaborators, deterministically scheduling the above contention. Neither function reaches its DB body. This is a focused lock diagnostic, not a full HTTP reproduction; source tracing establishes the public callers.

Minimal fix: use one lock order in `reserve_analysis`, taking `execution_lock()` before the visitor write guard, matching student/mentor admission. Add a concurrent reservation regression that queues student and analysis behind an occupied admission lock and verifies both finish. Owner: #192/#198 coordination for `analysis_runs.py`. Recheck reset/currentness after the change.

### 2. P2 — C27 ownership evidence is missing for nine changed paths

Requirement: `docs/demo/d0-03-contract-v1.md:701` requires ownership/change management for unlisted paths; the work order explicitly requires every path in this release diff to map. The full mapping is in `path-mapping.tsv`.

No table row or approved exception was found in the supplied local records for:

- `docs/ci-performance-runbook.ko.md:1`
- `tests/aggregate_browser_checks.mjs:1`
- `tests/browser_duration_estimates.json:1`
- `tests/ci_browser_sharding.mjs:1`
- `tests/ci_browser_sharding.test.mjs:1`
- `tests/ci_controls.test.mjs:1`
- `tests/run_browser_checks.mjs:1`
- `tests/test_ci_controls.py:1`
- `src/demo/__init__.py` (empty file; no line exists; ownership requirement at contract `:701`).

Failure scenario: C27 is marked complete for the entire base→candidate diff even though these paths have no evidenced owner/exception. The eight CI/documentation paths originate in maintainer commits `f2b638b`, `99f9481`, and `ca50e83`; authorship alone is not a #190 change record or coordinator-approved exception. `_coord/t205/REPORT-205.md:23` explicitly identifies the CI-control files as unowned and only proposes a future assignment. The empty package initializer has no behavioral risk, but still needs an explicit mapping under the requested all-path rule.

Minimal fix: record the independent CI work as an approved release-range exception, identify its owner/review, and assign the empty package initializer to #191. Update the C27 ledger; no product edits are needed for this finding. If an approval exists outside the supplied local copies, provide that record rather than creating a duplicate. This is a documentation/traceability finding, not a claim that the CI implementation is defective.

## Per-item results

| Item | Result | Evidence and limit |
| --- | --- | --- |
| 1. C27 every-path mapping | FAIL | 316 mapped, 9 unresolved; finding 2. Contract `:639–701`; exhaustive TSV supplied. |
| 2a. Migration 037 correctness / failure rollback | PASS (static review) | `src/db/migrations/037_demo_provider.sql:1`, `:18`, `:46`, `:66`; installer `migrate.py:69–145` preserves dependent rows with FK-off rebuild, restores indexes/triggers, checks FKs before committing history, rolls back on failure, and restores PRAGMAs. External key rules remain; demo key material must be NULL. No existing migration is modified. Preservation/failure tests inspected at `tests/test_demo_d2_01_schema.py:753`, `:803`, `tests/test_migrations.py:261`, `:378`; not rerun. No down migration is provided; successful-upgrade downgrade/backup restoration was not exercised or asserted. |
| 2b. DB isolation, write guards, lease, reset/cleanup | FAIL | Finding 1 is an availability failure in these shared boundaries. Otherwise, inspected visitor identity/factory/lease checks (`visitors.py:1258`), write/commit guards (`:796`, database.py `:898`), retirement/drain (`visitors.py:1091`), reset handoff before shielded task (`session.py:781`), root lock and validation (`database.py:140`), and verified backup/publish (`:492`). No concrete cross-visitor disclosure or late-write defect found in those inspected paths. |
| 2c. Permission / CSRF / session boundaries | PASS (spot-check) | `dependencies.py:129`, `:163`, `:458`, `:644`, `:773`; server-selected DB/user, signed bootstrap, origin + signed double-submit CSRF, stale namespace checks before DB resolution. `main.py:529`, `:592` separates middleware and wraps demo routes. `admin_providers.py:85`, `provider_reauthentication.py:17` fail closed for dangerous actions. |
| 2d. AI boundary / secrets | PASS (spot-check) | `config.py:23`, `:38`, `:131` pins process mode, excludes demo dotenv sources/forbidden credentials; `db/connection.py:35` disables normal DB factory in demo. `call_admission.py:82`, `:110` rejects mismatched providers/context; `call_execution.py:138` restricts demo to a local DemoProvider; `generation_runs.py:119` constructs it from server inputs. Egress-zero claim is reused from final #203 evidence `d4-01-security-evidence.md:24–41`, not newly measured. Demo auth signing secrets are required; “no secrets” here means no external AI credentials or disclosure, not absence of signing keys. |
| 3. Normal-mode regression spot-check | PASS | `call_admission.py:85`, `:122`, `:148`; external decrypt/counting retained. `call_execution.py:34`, `:159`; normal adapter map retained and injected demo context rejected. `generation_runs.py:186` has `demo=None`; `analysis_runs.py:164`, `:375`, `lifecycle.py:336` retain normal/no-op branches. `dependencies.py:55`, `:148`, `:181` retains normal template/auth/DB behavior; admin `save_selected` rethrows unchanged in normal (`admin_scenarios.py:58`), and normal create keeps its prior path (`:166`). This is the requested spot-check, not proof of every changed branch. |
| 4. Evidence consistency | PASS with disclosed limits | All inspected final G3/G4 cited commits are ancestors of the target. Final G4 test commits and release candidate have zero product diff from de3054a; details below. E4 and BFCache are explicitly not run. |

## C27 path summary and approved exceptions

Counts are disjoint and sum to 325:

| Mapping class | Paths | Authority |
| --- | ---: | --- |
| Explicit §26 path/directory ownership | 272 | Contract `:635–701`, including existing-file, extra-patch, new-path and existing-test entries. |
| Generic §26 new migration / dedicated tests-fixtures allowance | 20 | One numbered migration (`:686`) and 19 new dedicated tests/fixture files (`:701`). |
| Additional local approved ownership records | 24 | Records listed below; one authority per path in TSV. |
| Unmapped | 9 | Finding 2. |

Exceptions and cross-owner changes inspected:

- #195 mode propagation: ten otherwise-unlisted service files and five existing tests, `_coord/t195/c190-change.md:7`; explicit cross-owner pass-through allowance at `:8`. These account for 15 of the 24 additional mappings.
- Demo entry template, browser fixture bridge, i18n test: `_coord/tshared/c190-199-201.md:8–13` (3 mappings).
- Existing student failures test: `_coord/tshared/c190-198.md:5`; comparison concurrency test: `_coord/wo-flake.md:18` (2 mappings). The flake order also covers the student test.
- Migration fixture and latest-version pins: `_coord/wo-195-s1-r3.md:11–13` (2 mappings); D1 synthetic metadata fixture changes additionally approved in `_coord/t195/c190-fixture.md:8–12` and `c190-fixture-add.md:5–6`.
- `context_budget.py`: `_coord/wo-198-s2.md:5–6`; `sessions.py` close scope: `_coord/wo-198-g3-a1.md:3`, guarded route integration explicitly reviewed by coordinator order `_coord/wo-198-g3-review.md:17` (2 mappings).
- `analysis_runs.py` pipeline pass-through: `_coord/wo-198-s3-a1.md:3`; later demo-adapter integration in `_coord/wo-198-g3-review.md:16`. File already has §26 ownership, so not counted twice.
- `admin_scenarios.py` unsupported-model envelope: `_coord/wo-200-pr4-d3.md:6–8` (existing #200 ownership).
- `dependencies.py` display()/tour facts: `_coord/wo-d5-tourfacts.md:15–18` (bounded #194-owner repair assigned by coordinator).
- `src/demo/seed.py` provenance and admin session detail: `_coord/wo-202-d2.md:4`, `:8–15` (bounded #197/#200 repair assigned to #202).
- CLI/health/reauth/CRUD patch rows are represented in the diff and inspected at their entry/guard boundaries. The unchanged `admin_scenario_ops.py` and `admin_api_usage.py` are not unmapped changed paths: quota enforcement is in guarded route code and existing visitor-scoped query behavior is reused. The two utils files are unchanged, as §31 permits.

## Evidence ancestry / equivalence

Fresh read-only Git checks:

- `de3054a`, `7c8b6e1` (#202), `b4ac931` (#203), `fdb3096` (#204) are all ancestors of `ba64b82`; each has zero `src/` + `static/` diff from `de3054a`.
- `git diff --name-only de3054a ba64b82` contains only 32 docs/evidence paths and 8 test/helper paths. No product/config/dependency changes in that delta.
- G3 `f0a0c21`, A1 `f969a3c`, repair `b6e1abb`, and #200 `881d563` / `e4f349f` are ancestors too. They are historical evidence, not byte-identical final trees (respectively 6/5/4/1/1 product paths differ from de3054a). Later G4 evidence covers the integrated fixes; do not relabel the historical runs as candidate runs.
- Final references: `d3-04-integration-evidence.md:7`, `d4-01-security-evidence.md:8`, `d4-02-browser-evidence.md:135`; candidate/tested SHA distinction is explicit.
- E4 expiry is not run in smoke (`d3-04-integration-evidence.md:38`); #204 I7 instead uses a disclosed test clock. BFCache I1b is not run under Chromium's no-store restrictions (`d4-02-browser-evidence.md:147`, `:171`). Neither is treated here as freshly passed.
- Manifest `settings_sha256` remains null, with its deployment-input/canonicalization reason at `d3-04-integration-manifest.json:264`. This review does not fill it or grant G5/deployment approval.

## Checks and limitations

Fresh: source/diff/caller inspection; exhaustive path enumeration and local-record search; Git ancestry/product-equivalence checks; `python3 path_mapping_check.py`; `python3 lock_order_check.py` (stdlib only, no DB/app/network). Diagnostic output: `lock-order-result.txt`. No source fixes made.

Not run: full pytest, browsers, live checks, lints, migrations against a running DB, real HTTP deadlock reproduction, deployment/backup restore, external AI/network, or independent CI retrieval. Full regression is assigned to candidate CI by the order; its success was not verified by this offline reviewer. Existing test/evidence statements are reused, not new execution claims.

Remaining risk: the confirmed lock cycle blocks release acceptance even if existing CI is green; C27 also remains incomplete until the nine mappings have approved evidence. Fix finding 1, add its concurrency regression, record finding 2's ownership, and re-review the resulting fixed SHA.
