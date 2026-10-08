# 도메인 문서

이 저장소는 단일 컨텍스트 구조를 사용한다. 코드베이스를 탐색하는 스킬은 다음 규칙으로 도메인 문서를 읽는다.

## 탐색 전에 읽기

- 저장소 루트의 `GLOSSARY.md`를 읽는다.
- `docs/adr/`에서 작업 영역과 관련된 ADR을 읽는다.

파일이 없으면 별도 안내나 생성 제안 없이 진행한다. 용어나 결정이 실제로 확정될 때 `/domain-modeling`이 필요한 문서를 생성한다.

## 파일 구조

```text
/
├── GLOSSARY.md
├── docs/adr/
│   ├── 0001-provider-keys-encrypted-in-db.md
│   ├── 0002-scenario-config-json-single-source.md
│   └── 0003-misconception-analysis-post-session-only.md
└── src/
```

## 용어집의 표현 사용

이슈 제목, 리팩터링 제안, 가설, 테스트 이름 등에서 도메인 개념을 언급할 때 `GLOSSARY.md`에 정의된 용어를 사용한다. 용어집이 피하도록 명시한 동의어로 바꾸지 않는다.

필요한 개념이 용어집에 없으면 프로젝트에서 쓰지 않는 표현인지 먼저 검토한다. 실제 누락이라면 `/domain-modeling`에서 다룰 항목으로 기록한다.

## ADR 충돌 알리기

작업 결과가 기존 ADR과 충돌하면 해당 ADR과 결정을 재검토할 이유를 명시한다. 예: “ADR-0001과 충돌하지만, 다음 이유로 재검토가 필요함: …”.
