# Herdr 워커 실행

`herdr_orchestrator.md`의 절차를 이 저장소에서 실제로 돌릴 때 필요한 세부 사항이다.

## 작업 트리

쓰기 워커마다 `../misconcept_platform_v2-wt/<이름>`에 작업 트리와 브랜치를 하나씩 만든다. 리뷰어는 고정 커밋을 `--detach`로 체크아웃한 별도 작업 트리를 쓴다.

```bash
git worktree add -b <branch> ../misconcept_platform_v2-wt/<name> <base>
```

`/mnt/d`에서는 작업 트리마다 `.venv`가 생겨 삭제가 느리다. `git worktree remove --force`는 timeout을 넉넉히 주고 실행한다.

## 실행 플래그

| 하네스 | 플래그 | 이유 |
| --- | --- | --- |
| codex (구현) | `-m <model> -c model_reasoning_effort=<effort> -s danger-full-access -a never` | `workspace-write`는 작업 트리의 `.git/worktrees/<name>`을 읽기 전용으로 마운트해 commit·merge가 실패한다. 금지 동작은 작업 지시서로 막는다. |
| codex (계획·grilling) | `-m gpt-6-astra -c model_reasoning_effort=medium` | 기본 샌드박스로 충분하다. GitHub 쓰기는 승인 프롬프트가 뜬다. |
| opencode (task-verifier·tester) | pane에서 `export OPENCODE_CONFIG_CONTENT='{"model":"<provider/model>#<variant>"}'` 후 `herdr agent start <name> --kind opencode --pane <pane> -- --standalone`. 예: `opencode/muse-spark-1.3-contributor-free#xhigh`, `zai-coding-plan/glm-5.3-flash#max` | TUI는 `-m`을 받지 않으므로 모델과 노력(variant)을 config로 준다. variant를 빼면 기본값으로 돈다. 하단 상태줄 `Build · <모델> · <variant>`로 확인한다. 지원하지 않는 variant는 `Variant unavailable` 오류가 난다(`opencode run --standalone -m <id>#<variant> 'Reply with only: ok'`로 미리 확인). 백그라운드 서비스 대신 `--standalone`이어야 응답한다. 보고서 디렉터리 접근 권한 프롬프트는 `herdr agent send-keys <name> right enter`(Always allow)로 넘긴다. |
| agy (inspector·researcher) | `--model gemini-3.8-flash-high --effort high --dangerously-skip-permissions --add-dir <보고서 디렉터리>` | 모델 ID에 노력 접미사가 붙는다(`agy models`). 첫 실행 때 폴더 신뢰 화면이 뜨므로 `herdr agent send-keys <name> enter`로 넘긴다. |

```bash
herdr agent start <name> --kind codex --pane <pane> --timeout 60000 -- <flags>
herdr agent prompt <name> "Read and execute the work order at <path>." 
```

## 작업 지시서와 보고

- `docs/agents/work-order-template.md`를 복사해 채운다. 결과 하나, 작업 트리, 맥락 포인터(이슈·spec·이전 보고서), 허용·금지, 검증 명령, 보고서 경로를 적는다.
- 워커는 보고서를 파일로 쓰고 `REPORT READY <path>`만 답한다. 완료 메시지는 검증이 아니다. 코디네이터가 diff를 읽고 테스트를 다시 돌린다.
- 대기는 `docs/agents/watch_workers.sh`를 백그라운드로 돌린다. 보고서가 생기거나 워커가 보고 없이 멈추면 종료한다.
- opencode는 권한 프롬프트에서 `blocked`가 되어 `watch_workers.sh`가 멈춘다. 화면을 읽고 승인한 뒤 감시를 다시 시작한다.
- agy는 백그라운드 작업이나 하위 에이전트를 기다리는 중에 턴을 끝낼 수 있다. 지시서에 "테스트는 foreground로 실행하고 보고서를 쓰기 전에 턴을 끝내지 않는다"를 넣고, 멈추면 한 번 재촉한다.

## 검증 명령

명령은 `README.md`의 테스트 절(`AGENTS.md`의 Checks가 가리킨다). `npm run test:browser`는 빈 포트를 골라 자기 fixture 서버만 띄우고 끈다.
