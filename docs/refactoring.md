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

## #2: database installation and upgrades

The frozen `src/db/migrations/baseline.sql` (revision 023) defines the application
schema, matching the ORM's columns, nullability, keys, checks and indexes.
`uv run --frozen python -m src.db.migrations.migrate` is the official command.
`src.db.init_schema` and development startup delegate to it; production startup
never changes schema. Historical 001–022 files and recorded filenames remain
unchanged; 023 adopts their final schema without claiming they ran. Future SQL
upgrades start at 024. Down scripts are never applied automatically.

Supported inputs: empty databases, the captured ORM/SQL initializer schemas,
and revision 018–022 schemas (the optional report/UI tables and nullable
operation/category/grade columns may be absent). Older or unknown columns are
rejected atomically; upgrade those on a backup to 018 first. Legacy chatbot
configuration/audit tables remain untouched for data preservation; they are
not part of the application schema or created in new installs. Existing label
JSON is retained because both old and new formats are supported. Constraint
violations abort the upgrade; they must be repaired explicitly on a copy.

With the app stopped, back up using SQLite's backup API (it includes WAL data):

```sh
python - <<'PY'
import sqlite3
with sqlite3.connect('dialogue_sim.db') as source:
    with sqlite3.connect('dialogue_sim.backup.db') as backup:
        source.backup(backup)
PY
# Copy backup to upgrade-check.db, then validate the copy:
DATABASE_URL=sqlite+aiosqlite:///./upgrade-check.db uv run --frozen python -m src.db.migrations.migrate
```

Check `PRAGMA integrity_check`, `PRAGMA foreign_key_check`, row counts, and a
login/session on the copy before scheduling the real upgrade. Stop all app
workers for the real command. On failure, explicit BEGIN/rollback keeps DDL,
rows and history together. Retain the untouched backup; to restore, stop all
writers, use the backup API from backup into the destination (do not replace
just a live DB file while WAL/SHM sidecars exist), then restart the old code.
The refactoring run does not upgrade the real database.
