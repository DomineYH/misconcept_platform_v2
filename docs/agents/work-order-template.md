# Work order: <ticket or task> — coordinator → <worker name>

Role: <implementer | task-verifier | planner>. Workflow: <minor | standard | high> (see docs/agents/workflow-selection.md). Testing mode: <TDD via the `tdd` skill | none>.

## Where
- Worktree: <path> (branch `<branch>`, based on `<base>`). First confirm `git merge-base --is-ancestor <base> HEAD`; if not, `git reset --hard <base>` (only while you have no commits).
- Setup if needed: `uv sync --frozen --extra dev --python 3.12` inside the worktree. Write only inside this worktree and the report directory.

## What (one result)
<One deliverable.> Contract: `gh issue view <n>` (and the parent spec, if any). Context pointers: GLOSSARY.md, docs/adr/, AGENTS.md, <prior reports or commits>.

## Allowed
- Edit only what the ticket requires. Commit on `<branch>` with conventional messages ending with
  `Co-Authored-By: <worker model> <noreply@...>`
- Targeted tests while working; before reporting, run the checks listed in AGENTS.md (Checks).

## Forbidden
- New dependencies, unless the order allows them.
- git push, PR, merges into `<base>`, issue edits, deploys, real paid LLM calls, edits to .env or dialogue_sim.db.
- Scope beyond the ticket; weakening existing assertions.
- If a contract is ambiguous or blocks you, stop and report BLOCKED with the question.

## Before reporting
`git merge <base>` into your branch; resolve conflicts and re-run affected checks.

## Report (mandatory)
Write <report dir>/report-<worker name>.md: status DONE/BLOCKED, commits, files changed, each acceptance criterion → evidence, exact commands + pass/fail counts, skipped checks with reasons, open issues/risks. Then reply in the pane only: `REPORT READY <path>`.
