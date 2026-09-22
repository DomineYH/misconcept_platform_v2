# Sequential refactoring verification

## #1: baseline

Ownership/group authorization, committed teacher messages after bot failure,
and preservation of existing analysis after regeneration failure are tested
using file-backed temporary SQLite and fake LLM responses. CI uses the lockfile.

Known reproductions to resolve in subsequent issues:

- #2: run the historical migration runner on an empty DB (missing user table),
  or after ORM create_all (duplicate scenario.chat_model).
- #3: admin end flushes ended_at, then waits for analysis; an independent SQLite
  writer in that wait observes a write lock. End state can be rolled back too.
- #4: an ended session with a fallback SessionSummary bypasses the pipeline on
  POST /sessions/{id}/analyze because existence is treated as success.
