# 데모는 방문객별 임시 DB와 demo 전용 keyless 제공자로 격리한다

기존 관리자 목록·집계·CSV·삭제는 선택한 DB 전체를 대상으로 하며, 생성·멘토·사후 분석에는 요청 밖에서 계속되는 작업과 프로세스 실행 registry가 있다. 공개 체험에 하나의 공유 DB를 사용하면 역할을 관리자까지 바꾸는 방문객끼리 자료와 실행을 공유한다. 화면 응답만 예시로 바꾸면 실제 저장·근거 검증·삭제 흐름을 검증할 수도 없다. 반면 기존 제공자 연결은 암호화 키·역할 검증을 요구하므로 로컬 예시를 가짜 키로 등록하는 방식은 그 경계를 훼손한다.

서버가 시작 시 확정하는 `APP_MODE=normal|demo`를 배포 보안 설정 `ENV`와 분리한다. 같은 코드·화면을 별도 origin/프로세스/데이터 root로 실행하고, demo에서는 검증한 방문 세션마다 정식 installer로 만든 허구 seed의 임시 SQLite 복사본을 선택한다. 요청에서 SSE·background·원장·완료 저장까지 처음 선택한 factory/generation/auth_epoch와 각 작업의 lease를 유지한다. 실행 namespace는 방문객·세대·자원으로 나누고 방문 상태/전환 lock의 단일 소유자 visitors.py(#191)의 공개 transition handle로 reset/만료의 세대 폐기·역할 epoch 변경·실행 등록·transaction commit을 직렬화한다. 역할 전환이 먼저면 과거 epoch의 일반 CRUD도 STALE_ROLE로 rollback한다. lifecycle.py(#192)는 registry/취소/drain을 소유하고 전환 lock 밖에서 취소/대기를 수행한다. 오래된 URL·callback은 같은 숫자 ID의 새 자료에 연결하거나 DB를 재생성할 수 없다.

**ADR-0001의 외부 제공자 API 키 암호화·마스터 키 분리·매 요청 재인증 규칙과 ADR-0005의 모델 목록/역할 검증 분리·버전 결합 규칙은 보존한다.** keyless 예외는 **`APP_MODE=demo`, provider=`demo`인 로컬 예시 연결에만** 적용한다. 모든 key material은 SQL NULL이며 fake encrypted key를 만들지 않는다. 로컬 fixture의 출력 형식·근거 검증으로 만든 역할 evidence는 콘텐츠/기능/역할 계약 버전에 결합하고 실제 외부 제공자 시험이나 교육적 품질 보증으로 표시하지 않는다. normal에서는 demo 선택/admission/adapter 실행을 거절하고, demo에서는 외부 제공자를 admission과 execution 양쪽에서 거절한다. query/header/cookie/`TESTING`으로 mode를 바꾸지 않는다.

## Considered Options

- 공유 데모 DB와 tenant 컬럼: 모든 관리자 query·통계·CSV·background·고유 제약의 tenant 범위를 다시 작성해야 하므로 MVP의 누락 위험과 변경 범위가 크다.
- 방문객마다 앱 프로세스 분리: 격리는 강하지만 시작·정리·배포 비용이 5분 체험에 비해 크다.
- 프런트엔드 응답 가로채기 또는 테스트 서버 fixture 재사용: 실제 FastAPI·CSRF·원장·본문/분석 저장·삭제 계약을 통과하는 제품 체험이 되지 않는다.
- 외부 모델 이름/가짜 암호화 키를 쓰는 예시 연결: 외부 자격 증명·역할 검증의 의미를 흐리므로 거절한다.
- 방문객별 DB와 명시적 로컬 adapter: 기존 DB별 관리자 권한·저장 서비스를 재사용하면서 격리 책임을 DB resolver·lifecycle·공통 AI·위험 route 경계에 모을 수 있어 채택한다.

## Consequences

- MVP는 단일 앱 worker다. 공유 registry와 소유권 설계 없이 다중 worker/replica로 확장하지 않는다. root 독점 lock·방문객/디스크/실행 quota·TTL·lease 기반 cleanup이 필요하다.
- reset은 해당 방문객 공간만 새 seed로 교체한다. 계정 삭제의 종료 기록 보존·세션 soft delete와 구별하고 늦은 성공/실패/취소 저장도 막는다. 준비 실패를 성공으로 표시하거나 과거 DB로 자동 연결하지 않는다.
- demo 데이터/키 제약은 새 migration으로 명시하며 외부 제공자의 기존 CHECK·자료·FK·인덱스를 보존한다. normal DB에 demo 자료를 자동 seed하지 않는다. 기존 ADR-0012 두 파일은 변경하거나 재번호하지 않는다.
- session cookie는 Max-Age=28800이고 CSRF cookie는 별도 이름·서명·session 수명이다. 별도 origin/host-only 쿠키와 실제 CSRF를 유지한다. 일반 서비스의 demo 링크는 URL 연결일 뿐 인증·DB·제공자 전환 기능이 아니다.
- 비교 실행·응답 채택은 MVP에 추가하지 않는다. 위험 endpoint는 직접 요청도 거절하며 자유 탐색은 허용된 허구 자료 수정과 기존 조회에 한정한다.
- 예시 콘텐츠/fixture/schema/역할 계약 버전이 불일치하면 시작/실행을 거절한다. 사용량 미측정은 unknown이며 화면·CSV에서 준비된 예시/외부 호출 없음·콘텐츠 버전·seed/신규 자료 출처를 식별한다.
- 계약 문서는 실행 증거가 아니다. 소비자·별도 reviewer의 G0 검토와 @DomineYH 승인, 이후 실제 DB/AI/CSRF/브라우저 격리·경합·무호출 검증이 필요하다. 실제 host/origin/URL/용량·quota·최종 TTL은 D5-02 승인 입력이다.

결정 근거: #190 Agent Brief C01–C29와 maintainer 위임 답변, [D0-01](../demo/d0-01-boundary-survey.md) §6–7, [D0-02](../demo/d0-02-experience-spec.md) §11, 계획 §2/4.1/5/9.2/9.5. 동작 규범은 [d0-03-v1-r3](../demo/d0-03-contract-v1.md), G0 승인 상태는 [별도 기록](../demo/d0-03-g0-acceptance.md)을 따른다. 이 ADR은 승인 후보이며 G0 통과를 선언하지 않는다.
