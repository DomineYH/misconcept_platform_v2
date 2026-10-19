# D5-03 운영 인계 (#208)

상태: **1단계 골격 — 미완료.** 값은 공개 뒤 실제 산출물에서만 채운다. 값을 만들어 내지 않는다.
승인자·실행 책임자·인계 수신자·복구 권한은 모두 @DomineYH다(2026-10-18 위임 답변 3).
상한·TTL·cleanup·복구 절차의 본문은 #206 `docs/demo/d5-01-operations.md`가 단일 출처다. 이 문서는 링크만 하고 복사하지 않는다
(main 미병합; 병합 뒤 유효).

## 1. 공개 URL

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| demo 공개 URL | | @DomineYH | `d5-03-approval.md` §1 |
| normal 화면 링크가 위 URL과 같음 | | 에이전트/reviewer | `d5-03-evidence/link-check-<sha7>.md` |

## 2. 배포된 SHA

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| normal 실제 배포 SHA (전체) | | @DomineYH | 배포 기록 |
| demo 실제 배포 SHA (전체) | | @DomineYH | 배포 기록 |
| 승인 tuple과 일치 | | 에이전트/reviewer | `d5-03-evidence/link-check-<sha7>.md` |

## 3. fixture·상한·TTL

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| `fixture_version` / `manifest_sha256` | | @DomineYH | `d5-03-approval.md` §2 |
| `seed_sha256` | | @DomineYH | `d5-03-approval.md` §2 |
| 상한·quota 실제값 | `d5-01-operations.md` §4 표 참조 | @DomineYH | `d5-02-approval.md` |
| idle/absolute TTL 최종값 | `d5-01-operations.md` §4 표 참조 | @DomineYH | `d5-02-approval.md` |

기본값은 계약 §8 표이며 운영 용량 승인이 아니다.

## 4. cleanup·복구 담당

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| cleanup 감시·잔량 처리 담당 | | @DomineYH | `d5-01-operations.md` §5 |
| demo 복구 실행 담당 | | @DomineYH | `d5-03-approval.md` §4 |
| 복구 절차 | `d5-01-operations.md` §8 | — | #206 |
| normal 복구 절차 (일반 업데이트 포함 시) | `docs/s6-acceptance.md` §3 | — | 기존 운영 |

## 5. 승인 참조와 승인 범위

| 항목 | 값 | 기입자 | 출처 |
| --- | --- | --- | --- |
| D5-02 승인 기록 | `docs/demo/d5-02-approval.md` | — | #207 |
| D5-03 공개 승인 댓글 링크 | | @DomineYH | `d5-03-approval.md` §6 |
| 일반 서비스 업데이트 승인 범위 | | @DomineYH | `d5-03-approval.md` §3 |
| 링크 변경(`DEMO_PUBLIC_URL`) 승인 범위 | | @DomineYH | `d5-03-approval.md` §3 |

승인 범위 밖의 일반 서비스 변경·링크 변경은 새 승인을 받는다.

## 6. 증거

- 공개 smoke: `d5-03-evidence/public-smoke-<sha7>.md`
- tuple·링크 일치: `d5-03-evidence/link-check-<sha7>.md`
- rollback 결정: `d5-03-evidence/rollback-decision-<sha7>.md`
- 별도 reviewer 검토: `d5-03-evidence/handover-review-<sha7>.md` — 통과 전에는 이 인계를 완료로 표시하지 않는다.
