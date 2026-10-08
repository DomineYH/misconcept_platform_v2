# 이슈 트래커: GitHub

이 저장소의 이슈와 spec은 `DomineYH/misconcept_platform_v2`의 GitHub Issues에서 관리한다. 모든 작업에 `gh` CLI를 사용한다.

## 규칙

- **이슈 생성**: `gh issue create --title "..." --body-file <file>`. 여러 줄 본문은 파일로 작성한다.
- **이슈 읽기**: `gh issue view <number> --json number,title,body,labels,comments`.
- **이슈 목록**: `gh issue list --state open --json number,title,body,labels,comments --jq '[.[] | {number, title, body, labels: [.labels[].name], comments: [.comments[].body]}]'`. 필요에 따라 `--label`과 `--state`로 필터링한다.
- **하위 이슈 연결**: `gh api -X POST repos/DomineYH/misconcept_platform_v2/issues/<parent>/sub_issues -F sub_issue_id=<child-id>`. `<child-id>`는 `gh api repos/DomineYH/misconcept_platform_v2/issues/<child> --jq .id`로 읽은 숫자형 데이터베이스 ID이며 이슈 번호나 `node_id`가 아니다.
- **댓글 작성**: `gh issue comment <number> --body-file <file>`.
- **라벨 추가·제거**: `gh issue edit <number> --add-label "..."` / `--remove-label "..."`.
- **이슈 닫기**: `gh issue close <number> --comment "..."`.

저장소 안에서 실행하면 `gh`가 `git remote -v`의 원격 저장소를 사용한다. 다른 위치에서는 `--repo DomineYH/misconcept_platform_v2`를 지정한다.

## PR을 요청 접수 창구로 사용

**PRs as a request surface: no.** (`/triage`가 읽는 설정이다. 외부 PR도 요청으로 분류하려면 `yes`로 바꾼다.)

`yes`일 때는 이슈와 같은 라벨·상태를 적용하며 다음 명령을 사용한다.

- **PR 읽기**: `gh pr view <number> --comments`, 변경 내용은 `gh pr diff <number>`.
- **외부 PR 목록**: `gh api --paginate 'repos/DomineYH/misconcept_platform_v2/pulls?state=open' --jq '.[] | select(.author_association | IN("OWNER","MEMBER","COLLABORATOR") | not) | {number, title, author: .user.login, author_association, labels: [.labels[].name]}'`.
- **댓글·라벨·닫기**: `gh pr comment`, `gh pr edit --add-label`/`--remove-label`, `gh pr close`.

GitHub 이슈와 PR은 번호를 공유한다. `#42`의 종류가 불분명하면 `gh pr view 42`로 확인하고, PR이 아니면 `gh issue view 42`로 읽는다.

## 스킬이 트래커에 게시하라고 할 때

GitHub 이슈를 생성한다.

## 스킬이 관련 티켓을 가져오라고 할 때

위의 **이슈 읽기** 명령으로 본문과 댓글을 읽는다.

## Wayfinding operations

`/wayfinder`의 맵은 하나의 이슈이며, 결정 티켓은 맵의 네이티브 하위 이슈다. 아래 명령은 저장소 안에서 실행한다.

- **맵**: `wayfinder:map` 라벨이 붙은 이슈. 생성은 `gh issue create --label wayfinder:map --title "..." --body-file <file>`, 조회는 `gh issue list --label wayfinder:map --state all --json number,title,labels`. 현재 맵은 #23이다.
- **결정 티켓**: `wayfinder:<type>` 라벨(`research`/`prototype`/`grilling`/`task`)을 붙이고 `gh api -X POST repos/DomineYH/misconcept_platform_v2/issues/<map>/sub_issues -F sub_issue_id=<child-id>`로 맵에 연결한다. `<child-id>`는 `gh api repos/DomineYH/misconcept_platform_v2/issues/<child> --jq .id`로 얻는다. 관계 조회는 `gh api --paginate repos/DomineYH/misconcept_platform_v2/issues/<map>/sub_issues`.
- **차단 관계**: 네이티브 의존성을 사용한다. `gh api -X POST repos/DomineYH/misconcept_platform_v2/issues/<n>/dependencies/blocked_by -F issue_id=<blocker-id>`. `<blocker-id>`는 차단 이슈의 `.id`이며 번호가 아니다. 조회는 `gh api --paginate repos/DomineYH/misconcept_platform_v2/issues/<n>/dependencies/blocked_by`. 모든 차단 이슈가 닫혀야 착수할 수 있다.
- **프런티어**: 맵의 열린 하위 이슈 중 담당자가 없고 열린 차단 이슈가 없는 티켓을 하위 이슈 순서로 조회한다. `issue_dependencies_summary.blocked_by`는 열린 차단 이슈 수다. 다음 한 줄에서 첫 결과를 선택한다.

  ```bash
  gh api --paginate repos/DomineYH/misconcept_platform_v2/issues/<map>/sub_issues --jq '.[] | select(.state == "open" and (.assignees | length) == 0 and .issue_dependencies_summary.blocked_by == 0) | {number, title}'
  ```

- **작업 확보**: 세션의 첫 쓰기는 `gh issue edit <n> --add-assignee DomineYH`. 현재 담당자는 `gh api repos/DomineYH/misconcept_platform_v2/issues/<n> --jq '.assignees[].login'`으로, 배정 가능 여부는 `gh api repos/DomineYH/misconcept_platform_v2/assignees/DomineYH`로 확인한다.
- **해결**: `gh issue comment <n> --body-file <file>`로 해결 내용을 남기고 `gh issue close <n>`로 닫은 뒤, 맵의 Decisions so far에 요약과 링크를 추가한다. 상태·댓글·맵 본문은 `gh issue view <n> --json state,comments,body`로 확인한다.
- **spec 인계**: spec grilling 티켓을 해결하는 동일 세션의 끊기지 않은 맥락에서 결정 grilling → `/to-spec` → `/to-tickets`를 실행한다. `/to-spec` 템플릿으로 spec 이슈를 게시하고 `ready-for-agent`를 붙인다. `/to-tickets`로 트레이서 불릿 구현 티켓과 네이티브 차단 관계를 초안으로 제시하고 사용자 승인 후 게시한다. spec 이슈와 구현 티켓은 맵의 하위 이슈로 연결하지 않는다. 결정 티켓의 해결 댓글은 spec 이슈를, spec 이슈는 구현 티켓을 링크한다. 라벨은 `docs/agents/triage-labels.md`를 따른다.
