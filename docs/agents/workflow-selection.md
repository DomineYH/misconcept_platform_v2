# 워크플로 선택

`jev_task_routing_v2.4.0.json`이 기준이다. Jev 호출에는 `TYPESAFE_API_KEY`가 필요하다. 키가 없으면 코디네이터가 아래 표로 직접 고르고, 작업 지시서에 "Jev unavailable, coordinator selected <workflow>"라고 기록한다.

| 워크플로 | 고르는 경우 | 워커 역할 |
| --- | --- | --- |
| minor | 영향 범위가 한 파일·한 동작으로 확인되고 보안·데이터·공유 코어·운영 영향이 없음 | implementer |
| standard | 일반 기능·버그 수정 | implementer → task-verifier |
| high | 마이그레이션·스키마, 인증·권한·비밀번호, 공유 서비스, 운영 전환 | planner(필요 시) → implementer → task-verifier |
| exploration | 조사·비교만, 제품 변경 없음 | 없음(코디네이터) 또는 inspector/researcher |
| emergency | 진행 중인 장애 복구 | implementer → task-verifier |

- 테스트 모드: 동작 변경은 `tdd` 스킬, 문서·설정만이면 생략한다.
- 역할별 하네스·모델·노력은 JSON `roles`를 따르고, 사용자가 프롬프트로 지정하면 그것이 우선한다.
- task-verifier는 구현에 참여하지 않은 모델로 고정 커밋 범위를 리뷰한다.

워커 실행 방법은 `docs/agents/workers.md`.
