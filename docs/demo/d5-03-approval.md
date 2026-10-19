# D5-03 공개 승인 기록 (#208)

상태: **미승인 — 1단계 template.** 값 열은 모두 빈칸이다. 에이전트는 값을 만들어 내지 않는다.
빈칸은 **D5-02/D5-03 승인 입력**이며 기입자는 모두 @DomineYH다(2026-10-18 위임 답변 3).
자동 merge, CI 통과, 배포 설정은 공개 승인이 아니다. 아래 §6 서명이 있어야 공개를 시작한다.

근거: 계약 `docs/demo/d0-03-contract-v1.md`(`d0-03-v1-r3` 의미) §26:633, §28:767-771.
운영 절차는 #206 `docs/demo/d5-01-operations.md` §8을 따른다(main 미병합; 병합 뒤 유효).

## 1. 공개 URL·origin 재확인 (§26:633, 위임 답변 1)

`docs/demo/d5-02-approval.md`(#207)에 기록된 값을 그대로 옮겨 재확인한다. 두 문서의 값이 다르거나
공개 환경이 리허설 환경과 다르면 공개를 멈추고 추가 검증·승인을 받는다.

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| demo 공개 URL (`DEMO_PUBLIC_URL`) | | @DomineYH | `d5-02-approval.md` |
| demo origin (`DEMO_ORIGIN`) | | @DomineYH | `d5-02-approval.md` |
| normal origin (`NORMAL_ORIGIN`) | | @DomineYH | `d5-02-approval.md` |
| demo host ≠ normal host 확인 | | @DomineYH | `src/config.py:192-210` 검증 규칙 |
| 공개 환경 = 리허설 환경 (같음/다름 → 추가 승인 참조) | | @DomineYH | `d5-02-rehearsal-evidence.md` |

normal 링크는 승인된 별도 origin으로 이동만 하며 normal의 mode·인증·DB·provider를 바꾸지 않는다(§26:633).

## 2. 배포 tuple (§28:767)

#205 `docs/demo/d4-03-release-acceptance.md` §1과 #207 성공 리허설 증거에서 복사한다. SHA·fixture·영향 설정이
하나라도 다르면 기존 성공 증거를 재사용하지 않고 재리허설을 요청한다. secret 값과 secret 자체의 hash는 넣지 않는다.

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| `candidate_commit` (전체 SHA) | | @DomineYH | #205 §1 |
| `normal_commit` (= candidate) | | @DomineYH | #205 §1 |
| `demo_commit` (= candidate) | | @DomineYH | #205 §1 |
| #207 리허설 성공 SHA (= candidate) | | @DomineYH | `d5-02-rehearsal-evidence.md` |
| `contract_version` / `contract_commit` | | @DomineYH | #205 §1 |
| `content_version` | | @DomineYH | #205 §1 |
| `fixture_version` / `manifest_sha256` | | @DomineYH | #205 §1, #207 증거 |
| `db_schema_version` / `migration_set_sha256` | | @DomineYH | #205 §1 |
| `seed_sha256` / `logical_data_sha256` | | @DomineYH | #205 §1, #207 증거 |
| `settings_sha256` (secret 제외, 주입 참조/버전만) | | @DomineYH | #207 증거 |

## 3. 일반 서비스 업데이트 범위

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| 일반 서비스 업데이트 포함 여부 (예/아니오) | | @DomineYH | 이 승인 |
| 대상 버전 (전체 SHA) | | @DomineYH | 이 승인 |
| 변경 위치 (링크 설정만 / 코드 포함) | | @DomineYH | 이 승인 |
| 재시작 범위 | | @DomineYH | 이 승인 |

두 서비스를 코드가 같다는 이유로 자동 동시 갱신하지 않는다(§28:771). 일반 서비스 업데이트를 포함하면 그 복구는
기존 운영 절차 `docs/s6-acceptance.md` §3을 따르고, 새 쓰기 뒤 DB를 자동으로 덮어쓰지 않는다.

## 4. rollback 대상·권한 (§28:771)

순서: 신규 start 차단 → demo 중단 → 진행 작업 취소/drain/dispose → 승인된 이전 동일 tuple.
첫 공개라 이전 tuple이 없으면 **중단 상태**가 복구 목표다. normal DB·설정은 바꾸지 않는다.

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| demo rollback 대상 (이전 tuple 또는 "중단 상태") | | @DomineYH | `d5-02-approval.md` |
| normal 링크 rollback (`DEMO_PUBLIC_URL` 비움 등) | | @DomineYH | `d5-02-approval.md` |
| 서비스·secret 주입 권한 보유자 | | @DomineYH | `d5-02-approval.md` |
| 복구 실행 권한자 | | @DomineYH | 위임 답변 3 |

## 5. 공개 중단 조건

`d5-03-evidence/smoke-checklist.md`의 필수 행이 하나라도 failed이면 공개·유입을 멈추고 §4 순서로 복구한다.
결정은 `d5-03-evidence/rollback-decision-<sha7>.md`에 기록한다.

## 6. 승인 서명

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| 승인자 | | @DomineYH | 위임 답변 3 |
| 승인 시각 (UTC) | | @DomineYH | 승인 댓글 |
| 승인 댓글 링크 | | @DomineYH | GitHub #208 |
| 승인 시점 main SHA (전체) | | @DomineYH | `git rev-parse origin/main` |
| 결정 (공개 승인 / 보류 / 거절) | | @DomineYH | 승인 댓글 |
