# S6 최종 인수·전환·복구 runbook (#110)

**기술 구현과 운영 허가는 별도다. 현재 인간 gate와 실제 운영 증거는 미완료이며
`release_blocked`다.** 이 문서는 전환을 자동 실행하거나 승인하지 않는다.
계약은 [S6 #101 D1–D12](https://github.com/DomineYH/misconcept_platform_v2/issues/101),
[ADR-0012](adr/0012-verified-copy-cutover-and-explicit-recovery.md),
[S4 품질 gate](s4-quality-gate.md)다. 아래 표의 pending을 CI 성공으로 채우지 않는다.
실제 교육 승인, 유료 실행/제공자 지연 측정, 운영 DB·비밀 접근, 설치·재개,
cron 설치·운영 삭제는 이번 구현에서 **미수행**이다.

## 1. 하나의 인수 기록을 준비한다

운영 책임자는 실행 전 비공개 인수 기록에 아래 항목을 실제 값으로 채운다.
공유 보고서에는 비밀·암호문·비밀번호·대화·원문 오류를 넣지 않는다.

| 항목 | 필수 기록 / 현재 상태 |
| --- | --- |
| 책임과 권한 | 전환 책임자, 교육 담당자(초기 product owner), 비용 승인자, 복구 승인자, 운영 전환 승인자·일시·권한 근거: pending |
| 식별 | 초기/최종 원본 code revision·schema, 후보 revision·schema(035), config/snapshot hash, 도구 fingerprint, 시작·종료 UTC: pending |
| 보존·복원 | 원본/최종 백업·S2 archive/receipt/report(해당 시)·S6 receipt/report·두 복원 DB의 보호된 위치와 hash, 단계별 결과/건수·필수 검사: pending |
| 자격 증명 | 원본/후보 복원별 필요한 key version, 버전별 오프라인 검증 보고서·DB/tool hash, 별도 비밀 보관 위치의 참조(키 값 제외): pending |
| 배포 세트 | old/new code·config, master key/version 및 session signing secret의 별도 비밀 보관 참조, 복구 방법·권한: pending |
| 전환 창 | 사전 공지·종료 시각·유입 차단 방식/검증, 종료 부수 효과와 유료 분석 승인, idle 검사, 모든 writer 목록/정지 증거: pending |
| 기술 검사 | 아래 AC 증거 및 README 5개 검사 명령의 revision·일시·건수·exit, 실패/단독 재실행 여부: 구현 보고서에 기록 |
| 계측 | workload별 조건·fixture hash·환경·limits·clock·원시 표본·요약 위치, attempted/admitted/completed/failed/rejected/cancelled 및 unknown: 합성 검사만 |
| 운영 동작 | health/login·키 상태·reader/ACL/CSV·시나리오 검토/게시·역할 검증 상태, 유지보수 중 모든 쓰기 목록: pending |
| 정리·백업 | 아래 담당자·경로·기관 보존정책·일정·최근 성공/exit·알림·복구 기록: pending |
| 출시 판단 | HG1–HG5와 별도 실제 전환 권한의 일치 여부, 승인자·일시·기록 hash: pending → `release_blocked` |

HG1: 실행 전 기대 근거 검토·고정/hash. HG2: 별도 유료 평가 비용 승인.
HG3: 예정 exact model/options **각각** 합성 12개 실제 baseline/candidate 실행 기록.
HG4: 교육 담당자의 표본·차원별 점수/판정(적용 차원 ≥4/5, 공통 차원 하락 ≤1,
잘못된 참조·중대한 무근거 주장 0, N/A 이유).
HG5: 실제 승인자·서명·일자·기록 hash 및 별도 운영 승인.
미평가/unknown/일치하지 않는 승인 하나라도 있으면 운영 DB 설치/전환과 트래픽
재개를 허용하지 않는다. 설계 승인·mock 점수·역할 probe·기술 exit 0은 해제 근거가 아니다.
prompt/schema/chunking/estimator/model/options 또는 관련 실행 계약이 바뀌면
[S4 무효화 규칙](s4-quality-gate.md)에 따라 영향받는 평가부터 다시 수행한다.

## 2. 전환 창을 실행한다 (D1/D2/D6)

1. HG와 실제 전환 권한·운영 검증 계획을 확인하고 교사에게 종료 시각을 공지한다.
   인프라 접근 통제로 신규 수업/일반 생성 유입을 막고 실제 차단을 확인한다.
   기존 관리자 종료·상태 조회만 가능한 유지보수 접근을 남긴다.
2. 정해진 시각에 **구 버전의 기존 종료 기능**으로 남은 세션을 끝낸다.
   관리자 종료는 분석을 예약할 수 있으므로 해당 버전의 부수 효과와 기존 운영
   비용 승인을 먼저 확인한다. 분석 없는 교사 close는 기존 소유권으로만 사용한다.
   승인되지 않은 유료 부수 효과 또는 종료 실패면 전환을 연기한다. SQL 강제 종료나
   우회 권한을 만들지 않는다. HTTP 실패만으로 ended_at 미커밋을 추정하지 않고 재조회한다.
3. 명시적으로 승인한 종료 분석 외의 새 생성을 막는다. 학생/멘토/분석/역할 probe/
   모델 목록/비교/usage 작업은 완료 또는 기존 취소·복구 절차로 마무리한다.
   소프트 삭제된 세션까지 미종료 0, 모든 실행·probe·analysis·comparison·usage의
   미완료 0을 확인한다. 시간 경과나 화면의 active 수만으로 idle을 선언하지 않는다.
4. app worker, 예약 retention, admin/seed 스크립트, DB shell 등 **모든 writer**를
   정지하고 책임자·시간·대상을 기록한다. 구/신 코드가 같은 DB에 동시에 쓰지 않는다.
5. 멈춘 최종 원본을 SQLite backup API로 일관 백업한다. main 파일만 복사하지 않고
   live WAL/SHM을 삭제하지 않는다. [S2 백업 절차](s2-cutover.md)와
   [README 백업/비밀 보관](../README.md)을 따른다.
6. 최종 백업으로 **새 비공개 workspace**에서 [S6 리허설](s6-cutover.md)을
   dry-run → apply한다. 공식 023–030은 실제 effective settings와 별도 S2 workspace를
   사용해 변환·보존·복원·receipt 검증 후 031을 거친다. 031–035는 현재 snapshot/config를
   그대로 유지한다. 최신은 035이며 새 migration은 없다. 미지원 schema/이력은 거부한다.
7. 초기 리허설 증거로 최종 검사를 대체하지 않는다. 최종 원본이 백업 이후 변하지
   않았음을 일관 복사본의 schema·전수 원래 열/ID/값/hash로 다시 확인하고 최종 후보와
   code/config/evidence를 묶는다. 원본 변경·stale fingerprint면 멈추고 새 workspace로 재검증한다.
   S2 및 S6 봉인 산출물을 편집하거나 실패 evidence를 지워 통과시키지 않는다.
8. 원본/후보의 별도 복원과 integrity/FK/schema/전수 보존을 검증한다.
   [키 복원 절차](s6-credential-restore.md)로 양쪽 복원별 **모든 필요한 버전**의
   보고서를 같은 DB/tool hash에 연결한다. 암호문 없는 경우만 N/A다.
   여러 버전은 각각의 격리 프로세스에서 검증하며 incomplete 하나를 완료로 해석하지 않는다.
   sealed candidate는 쓰지 않고 reader/ACL/CSV는 별도 일회용 복사본에서 확인한다.
   운영 로그인 검증은 별도 승인 절차이며 운영 비밀번호를 수집·변경하지 않는다.
9. 모든 필수 gate와 실제 전환 승인 후, writer가 멈춘 목적지에 SQLite backup API로
   검증 후보를 설치하고 matching code/config/secrets를 적용한다. 단일 instance·단일 async
   worker, production·`TESTING=false`로 기동한다. 자동 게시·자동 probe·유료 생성을 하지 않는다.
10. 유지보수 접근에서 health/login, 키 상태, 역사 reader/ACL/CSV, 시나리오 검토·게시 및
    현재 역할 검증 상태를 확인하고 **모든 새 쓰기**를 기록한다. 유료 역할 재검증이
    필요하면 별도 비용 승인을 받는다. 검증/기동 smoke 실패면 재개 금지다.
    승인자가 최종 증거 일치를 확인하고 재개 시각·판정을 기록한 뒤 유입을 연다.

schema/data/FK/복원/키/ACL/CSV 실패, stale evidence, writer 잔존, 승인 부재,
기동 실패는 중단 사유다. p95 숫자만으로 rollback하지 않는다.
[계측](s6-measurement.md)의 합성 수치·실 제공자 지연·인간 승인을 구분한다.
[provider smoke](provider-smoke.md)는 기본 zero calls, 별도 승인 계획 합산 USD 3 한도이며
12개 HG 평가를 대체하지 않는다. unknown 사용량/비용/기간은 0으로 채우지 않는다.
관리자 사용자/시나리오 목록은 20개씩, 세션 JSON은 기본 첫 20개와 metadata다.
전체 연구 자료는 기존 CSV export를 사용하며 pagination으로 잘라내지 않는다.

## 3. 실패와 복구 결정을 기록한다

재개를 금지하고 후보·실패 증거를 보존한다. 자동 rollback/down migration/병합이나
무손실 복원을 약속하지 않는다. DB 복원은 실제 제공자 과금을 취소하지 않는다.

| 경계 | 운영 책임자의 절차 |
| --- | --- |
| 새 쓰기가 없음을 증명 | 모든 writer 정지 → 후보 보존 → backup API로 old DB 복원 → matching old code/config/key/version/signing secret 복구 → integrity/FK/reader/ACL/CSV/기동 검증 → 승인 후 구 버전 재개 |
| 새 쓰기 발생 또는 불명확 | 즉시 유입·writer 정지 → 현재 상태 일관 백업 → old/new DB와 증거 모두 보존 → 전후 ID·본문·설정·usage/원장 대조 → 전진 수정 우선. 복원 불가피 시 사람이 복구·대조 계획과 손실 범위를 명시적으로 승인한 뒤 수행 |

기동 orphan 복구, 로그인, 관리자 게시/설정/키 변경, 분석/비교/usage도 새 쓰기다.
교사 대화가 아직 없다는 이유로 첫 경계를 선택하지 않는다. 발생 여부를 증명할 수
없으면 이후 경계다. 옛 백업 강제 덮어쓰기나 자동 데이터 병합을 하지 않는다.
합성 `test_old_legacy_backup_restore_loses_new_writes_without_changing_evidence`는
옛 백업 복원이 후속 기록을 잃는다는 증거이며 운영 복원 권한이 아니다.

## 4. 매일 retention과 백업 운영을 인계한다

[기존 비교 retention 절차](s5-comparison-retention.md)의 dry-run/execute를 그대로 사용한다.
UTC 생성+30일 만료, running 작업 제외, 한 transaction 최대 100개, nullable usage/cost
보존 정책은 바꾸지 않는다. 운영 담당자가 외부 scheduler에 매일 실행을 설치한다.
이 티켓은 cron 설치나 운영 DB 삭제를 수행하지 않는다.

실행 전 인수 기록에 scheduler 담당/계정·job ID·시간대·실행 주기·앱/DB 절대 경로,
로그/exit 저장 경로·알림 수신 담당·최근 성공 UTC/exit/건수·복구 담당을 필수로 남긴다.
겹치는 cleanup은 금지한다. 매일 최근 successful completed_at, exit 0, deleted/failed/
skipped-running을 확인한다. nonzero exit 또는 26시간 내 성공 없음은 담당자에게 알린다.
실패 시 배포/저장소 문제 해결 → dry-run → execute 재실행; 앞선 완료 batch는 이미
삭제됐고 실패 batch는 rollback된다. running skip 지속/후보 누적을 조사한다.
crash 후 정상 단일 worker 재기동으로 orphan 정리를 거친다. 건강한 live 작업을
강제로 orphan 처리하지 않는다.

기관 승인 백업 보존정책의 식별자·보존 기간·회전/폐기 주기·보관 경로·접근 권한·담당자·
마지막 백업/복원 시험 UTC·실패 알림/대응을 실행 전 기록한다. DB 백업, S2 archive,
S6 증거와 old/new code/config/별도 비밀 보관 참조를 파일럿 인수까지 유지하고 그 뒤
승인된 정책을 따른다. 비교 30일 만료를 백업 전체 30일 삭제로 확대하지 않는다.
행 삭제가 backup/WAL/storage snapshot까지 즉시 지운다고 주장하지 않는다.

## 5. AC와 gate 증거 지도

아래 자동 증거는 합성 임시 DB·SDK transport mock·localhost이며 운영 수행 증거가 아니다.
최종 실행 결과/commit/정확한 명령·건수는 #110 구현 보고서에 남긴다.

| 기준 | 실제 자동 증거 (tests/) | 운영에서 추가로 필요한 증거 |
| --- | --- | --- |
| AC-26 최종 보존/복원 | `test_s6_cutover.py::test_official_current_revisions_preserve_all_original_values`, `test_native_snapshot_is_preserved_alongside_reconstructed_history`; `test_s6_legacy_cutover.py::test_legacy_rehearsal_seals_s2_then_upgrades_separate_candidate`, `test_every_official_legacy_schema_boundary_and_custom_schema_refusal` | 최종 원본·후보 전수 보존/hash·두 복원 결과·S2 receipt 연결 |
| AC-26 WAL/불변/거부 | `test_s6_cutover.py::test_current_wal_backup_retains_committed_pages_without_source_writes`, `test_completed_rehearsal_refuses_changed_inputs_and_evidence`; `test_s6_cutover_inputs.py::test_unfinished_work_is_refused_including_deleted_sessions` | 최종 writer 정지·원본 재검증·같은 code/config/evidence |
| AC-26 reader/권한/CSV | `test_s6_cutover_readers.py::test_authenticated_history_acl_csv_and_comparison_isolation_leave_candidate_immutable`; `test_s6_pagination.py::test_research_csv_still_exports_all_sessions` | 별도 disposable copy의 실제 로그인·소유/다른 교사/그룹/관리자·비교 소유 격리·CSV 의미 |
| AC-29 DB/키 별도 복원 | `test_s6_credentials.py::test_matching_key_checks_all_credentials_and_preserves_restored_bytes`, `test_each_version_needs_its_own_evidence`; `test_credential_failure_is_separate_from_database_integrity`, `test_database_without_credentials_is_not_applicable` | old/latest 복원별 모든 key version·DB/tool fingerprint·별도 승인된 비밀 경로 |
| AC-29 키 없이 조회/AI 거부 | `test_s6_credentials_readers.py::test_restored_login_and_history_work_but_ai_admission_is_denied` | 자료 접근 성공과 AI 복구 성공을 구분 |
| AC-30 schema/route | `test_s6_retirement.py::test_final_schema_has_no_retired_tables_columns_or_foreign_keys`, `test_registered_routes_have_no_retired_execution_or_management`; `test_scenario_retirement.py::test_retired_management_routes_are_absent`, `test_native_contract_rejects_retired_fields_atomically` | 최종 code/schema에 증거 고정 |
| AC-30 legacy join/SDK/env/fallback | `test_scenario_retirement.py::test_admin_reads_list_counts_and_status_without_legacy_joins`; `test_s6_retirement.py::test_runtime_references_do_not_restore_legacy_joins_or_environment_keys`, `test_sdk_imports_and_generation_calls_stay_in_provider_adapters`; `test_student_connections.py::test_unavailable_student_configuration_has_no_fallback_or_attempt` | import/static 검사와 실제 SQL/HTTP/zero-attempt 거부를 함께 판단 |
| 구조·계측 | `test_s6_workloads.py::test_student_measurement_and_zero_call_replay`, `test_short_analysis_one_call_and_replay_measurement`, `test_twenty_teachers_and_three_panel_capacity`; `test_s6_fault_workloads.py::test_explicit_capacity_slow_mentor_leaves_student_and_next_input_free`; `test_s6_measurement.py::test_nearest_rank_and_all_outcome_denominators` | 원시 표본·조건·분모·unknown, 실제 제공자 측정은 미수행 |
| 유료 승인 경계 | `test_provider_smoke.py::test_execute_without_human_approval_refuses_before_calls`, `test_three_dollar_boundary_and_multi_provider_aggregate` | 별도 계획별 비용 승인; HG 비용 승인과 구분 |
| retention/복구 | `test_comparison_retention.py::test_cleanup_failure_rolls_back_usage_detachment_and_can_rerun`; `test_s6_legacy_cutover.py::test_old_legacy_backup_restore_loses_new_writes_without_changing_evidence` | 매일 최근 성공/exit, 백업 정책, 복구 책임/손실 승인 |
| HG1–HG5·운영 전환 | [S4 기록 양식](../tests/fixtures/s4_quality_comparison_template.json), 위 인수 기록 | **pending / release_blocked**; mock/CI는 승인 아님 |

AC-30의 허용 참조는 immutable `src/db/migrations/*.sql`/공식 baseline 및 역사 runner
`migrate.py`, `migrate_015.py` 및 역사 006 검증 shell/문서(현행 실행 절차가 아님); 오프라인 `scenario_conversion.py`,
`legacy_conversion_values.py`, `s2_verification.py`(변환·archive·backup/receipt 검증)다.
이들은 옛 실행 엔진이 아니다. `tests/legacy_models.py`, `tests/fixtures/legacy_schema.sql`,
`tests/s2_analysis_invocations.py` 등 격리된 역사/품질 baseline은 제품 import 금지다.
`load_prompt_template`와 Jinja HTML/CSV template는 유효한 현행 loader다.
`config.OPENAI_API_KEY`는 호환 선언만 허용하고 runtime 소비는 금지한다.
SDK import는 기존 provider generation/catalog/client/error adapter만, SDK 생성 호출은
세 provider generation adapter만 허용한다. 새 동적 우회까지 정적 검사가 증명하지는
않으므로 기존 실제 HTTP→서비스→SDK transport/ledger 동작 회귀를 함께 실행한다.

## 6. 최종 기술 검사

무거운 병렬 작업 없이 README 명령을 한 번씩 순서대로 실행하고 exit·건수를 기록한다.

```sh
uv run --frozen python -m pytest -q
uv run --frozen ruff check .
uv run --frozen black --check .
npm run test:browser
uv run --frozen python tests/check_student_live.py
```

기존 timing 실패가 나오면 해당 검사만 단독 재실행한다. 단독 통과는 최초 실패와
함께 환경 timing 실패로 기록하고 timeout/assertion을 느슨하게 바꾸지 않는다.
단독도 실패면 관련 원인을 해결한다. 기술 성공이어도 인간 gate·실제 운영 항목은
각각 미수행으로 남기고 `release_blocked`를 유지한다.
