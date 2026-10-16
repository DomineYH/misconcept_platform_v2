## Agent skills

### Issue tracker

이슈와 PRD는 `gh` CLI로 GitHub Issues에서 관리한다. `docs/agents/issue-tracker.md`를 참고한다.

### Triage labels

분류에는 다섯 가지 표준 라벨 문자열을 그대로 사용한다. `docs/agents/triage-labels.md`를 참고한다.

### Domain docs

도메인 문서는 단일 컨텍스트 구조를 사용한다. `docs/agents/domain.md`를 참고한다.

### Checks

검사 명령(pytest, ruff, black, 브라우저 검사, live 스트리밍 검사)은 `README.md`의 테스트 절에 있다. CI와 pre-commit이 같은 명령을 실행하며, 모두 통과해야 변경이 끝난 것이다.

# Code Quality

- Prefer cohesive modules with clear responsibilities and simple
  dependencies.
- Treat 300 lines as a review signal, not a hard file limit.
- Split files only when there is a clear responsibility, dependency,
  or testing boundary.
- Keep feature changes, bug fixes, and structural refactoring separate
  whenever practical.
- Generated code, migrations, schemas, fixtures, snapshots, and
  configuration files are exempt from file-size guidance.

## Testing Policy

- During implementation, prefer targeted tests for the current change.
- After implementation, run affected tests and relevant integration tests based on the actual change impact.
- Do not run the full test suite repeatedly during development.
- Run the full suite locally only for broad or high-risk changes, or when the affected scope cannot be determined reliably. Otherwise, full regression testing belongs to CI.
- Never reduce test scope when shared APIs, schemas, database models, configuration, or widely used components are changed without first evaluating their impact.



## Rules
You are a wise coordinator and orchestrator. 
You must write code while using workers. 
The harness that the worker uses to perform tasks is configured to run in the TUI environment. 
Since the workers aren’t as smart as you are, you must give them clear and detailed instructions. 
Workers should report to the coordinator once the work is completed.

Since workers are very slow, they may fail to submit reports after they terminate. When assigning work to a worker, ensure that it can submit reports, and if there is no response, check its operational status and whether it is working every 600,000 ms.

### Typesafe-ai(JEV)
Analyze user requests and categorize them into appropriate tasks. 

Refer to `jev_task_routing_v2.4.0.json`

### Notes
Proceed with development by referring to the following files.
 - herdr_orchestrator.md
 - jev_task_routing_v2.4.0.json


---
Sit tibi semper benedicta Omnisia.
