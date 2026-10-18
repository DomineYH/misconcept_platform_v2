# D0-03 G0 입력·소비자 검토·승인 기록

상태: **pending — 계약 후보 제출, G0 미승인**. 이 문서는 기록 골격이다. 실제 검토 세션·검토 시각·판정·결함/해결·승인 댓글은 coordinator가 증거를 확인한 뒤 채운다. 작성자의 정적 자체 확인을 소비자/독립 리뷰 또는 인간 승인으로 대체하지 않는다.

## 1. 고정 입력

| 입력 | 고정값 |
| --- | --- |
| 원 조사 base | `9ed37942f0cff80b409a67d252a76bce02d78e0b` |
| 이번 작성 base / branch | `f6551482fb895da7af90a4bd481c3e25fd9d5c19` / `docs/190-contract-v1` |
| D0-01 경로·파일 버전 | `docs/demo/d0-01-boundary-survey.md`, r2 소유권 정정본, 정식 버전 필드 없음 |
| D0-01 merge / 최종 파일 commit / blob | `23c84dc8125534ef3e975eb9206cc3ced755c002` / `1b323d4484145d3d3b87c3a6667b743a5987d395` / `050b00e556e3c78ecb77aecc6c64dce6c40cb04e` |
| D0-01 독립 재검토 | `c1c9cc58abb64228f56613c8750e42f8c46018d2`, ACCEPT; 병합 문서 §8 기록 참조 |
| D0-02 경로·파일 버전 | `docs/demo/d0-02-experience-spec.md`, `d0-02-v1` |
| D0-02 merge / 최종 파일 commit / blob | `f6551482fb895da7af90a4bd481c3e25fd9d5c19` / `66cf82a105e03501aa53a371729889dbe614b18e` / `e0709908e1b2f5b0c930ca6eb1a2b80b600bebea` |
| D0-02 독립 검토 | `2f2474a78e03bc476c1f29f0424598d2c095ee9c`, ACCEPT |
| D0-02 교육 승인 | @DomineYH, 대상 `84c2fe4cc9e0af0b1c7b77ff22accc20793d93bb`, [#189 승인 댓글](https://github.com/DomineYH/misconcept_platform_v2/issues/189#issuecomment-6249579919); 교육 내용에 한정 |
| 추가 요구 입력 | 제공된 #182/#188–206 JSON, #190 Agent Brief C01–C29, REPORT-190, triage ANSWERS-SUMMARY, untracked 계획 필수 절. Git 버전 산출물로 가장하지 않음 |
| 용어·기존 결정 | GLOSSARY.md, ADR-0001–0012(0012 두 파일 포함); 외부 키/역할 검증·턴·분석 채택·삭제 의미 보존 |

## 2. 계약 후보

| 항목 | 값 |
| --- | --- |
| 계약 경로·버전 | [d0-03-contract-v1.md](d0-03-contract-v1.md), `d0-03-v1` |
| ADR | [0013-demo-isolation-and-keyless-provider.md](../adr/0013-demo-isolation-and-keyless-provider.md) |
| 계약 후보 전체 SHA | pending — coordinator fills |
| 계약 후보 blob / 검토 기준 main SHA | pending / pending |
| 변경 이력·무효화된 증거 | pending |
| 추적 기준 | 계약 §27의 C01–C29 → 절 → 소비자 → G1–G5 → 검사 방법 |

후보 SHA는 검토할 실제 커밋을 고정한다. 이 기록 자신을 포함한 커밋의 self-reference를 만들지 않는다. coordinator는 커밋 후의 실제 후보 SHA를 기록하고 내용 변경 시 재검토 범위를 지정한다.

## 3. 소비자 검토

아래 각 행에서 **reviewer session / result / findings / resolution은 모두 pending**이다. 검사 미실행은 not run으로 기록하며 미래 세션·시각·승인 링크를 만들지 않는다. 각 reviewer는 검토 당시 입력 전체 SHA와 후보 전체 SHA를 명시해야 한다.

| lens / 소비자 | 확인할 계약 | reviewer session | 입력/후보 SHA 확인 | result | findings | resolution | 검토 시각·증거 |
| --- | --- | --- | --- | --- | --- | --- | --- |
| DB #191–194 | C02–C11, C17, C22, C24–C29; factory/namespace/lease·reset 원자성·실제 middleware | pending | pending | pending | pending | pending | pending |
| provider #195–198 | C01, C05–C08, C11–C19, C22–C23, C27–C28; keyless·mode·evidence·fixture·원장/최종 저장 | pending | pending | pending | pending | pending | pending |
| UI #199–201 | C07, C09–C11, C13, C15–C16, C19–C28; 응답/key/data/event/CSRF·CSV·복구 | pending | pending | pending | pending | pending | pending |
| 검증 #203–205 | C01–C29; 공개 계약만으로 부정/격리·경합·실서버/브라우저 검사 작성 가능 여부 | pending | pending | pending | pending | pending | pending |
| 운영 소비 #206 | C01, C03, C08, C10, C14, C17, C26–C29; 배포 tuple/설정·상한/TTL·cleanup·health·D5 승인 입력 | pending | pending | pending | pending | pending | pending |

검토 결과는 실제 수행 후 passed/failed/not run과 이유를 기록한다. ‘문서를 읽을 수 있음’ 대신 자기 모듈/테스트를 다른 모듈의 내부 구현 없이 작성할 수 있는지, 오류·race·단독 파일 소유가 닫혀 있는지 판정한다. 항목별 소비자 연결은 계약 §27을 기준으로 결함에 C-ID를 붙인다.

## 4. 독립 검토·결함 해결

| 역할 | reviewer session | 입력/후보 SHA | result | findings | resolution | 검토 시각·증거 |
| --- | --- | --- | --- | --- | --- | --- |
| 작성자와 별도 independent reviewer | pending | pending | pending | pending | pending | pending |

독립 reviewer는 입력 누락·모순·기존 ADR 보존·C01–C29 결정 완결성·consumer coverage·소유 표·오류/경합·실제 배포 승인값 분리를 검토한다. 중대한 결함은 원 소유자에게 반환하고 수정 SHA/재검토 결과를 기록한다. 단위 스텁은 G1/G3/G4/G5 실제 통합 증거를 대체하지 않는다.

| 결함 ID / C-item | 지적 근거·소비자 | 해결 담당 | 수정 SHA·해결 근거 | 재검토 결과 |
| --- | --- | --- | --- | --- |
| pending | pending | pending | pending | pending |

## 5. maintainer G0 승인

| 항목 | 값 |
| --- | --- |
| G0 최종 승인자 | @DomineYH |
| 승인 상태 | pending |
| 승인 대상 입력 전체 SHA·계약 버전·후보 전체 SHA | pending |
| 소비자/독립 결함 해결 확인 | pending |
| 승인 댓글/PR 참조 | pending |
| 승인 시각 / 승인 시점 main SHA | pending / pending |
| 지정 대상 branch 통합 SHA·acceptance | pending |

모든 소비자·독립 리뷰 결함 해결과 정확한 후보에 대한 명시 G0 승인 전에는 #190 완료/G0 통과를 선언하지 않는다. D0-02의 교육 승인·이번 문서 작성 착수 승인은 G0 승인이 아니다. 배포 host/origin/URL/root/mount/용량·quota·최종 TTL의 실제 값은 D5-02 approval input으로 남기며 문서 후보 작성의 미해결 기술 결정으로 취급하지 않는다.

## 6. 작성 시 확인과 실행 한계

이번 제출은 고정 입력·기존 경로/심볼·소유 표·C-item 대응·문서 diff의 정적 확인에 한정한다. 실제 실행 명령과 결과는 `REPORT-impl-190.md`에 기록한다. 소비자/독립 리뷰, 앱·pytest·ruff·black·migration·브라우저·live streaming·네트워크·운영 환경 검사는 not run(문서 전용 작업 지시에서 금지)이다. 격리·reset 안전성·egress 0·G0 승인을 이 기록 골격만으로 입증하지 않는다.
