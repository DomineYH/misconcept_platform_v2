# S6 current-format candidate rehearsal (#102)

This prepares evidence on an explicitly isolated copy. It does not install a
database, stop writers, approve educational quality, or authorize production.
Technical success always leaves `release_blocked: true`.

Run from a git checkout with git installed; the evidence requires the checked-out
code revision. Offline credential restoration has a separate
[version-by-version verification procedure](s6-credential-restore.md).

Use an empty SQLite source copy or an official 031–035 database. For 023–030,
the separate legacy path must first complete S2 conversion and its validated
031 receipt. Keep those original S2 artifacts unchanged. Arbitrary schemas,
unknown/gapped migration history and unfinished work are refused.

Stop every writer before obtaining the final source copy through SQLite's
backup API; copying only a main DB can omit committed WAL pages. See
[the S2 procedure](s2-cutover.md) for write-stop and backup details. Never use
the operating default database or secrets as rehearsal input. Source files
must be private (0600), without symlinks or hardlinks, outside the workspace.
The tool creates workspace directories as 0700 and artifacts as 0600.

```sh
uv run --frozen python -m src.db.s6_cutover \
  --source-copy /private/maintenance/source-copy.db \
  --workspace /private/maintenance/s6-rehearsal

uv run --frozen python -m src.db.s6_cutover \
  --source-copy /private/maintenance/source-copy.db \
  --workspace /private/maintenance/s6-rehearsal --apply
```

Default dry-run creates a WAL-consistent `backup.db` and unchanged
`candidate.db`, checks the source schema, integrity, foreign keys and idle
state, and records `dry-run.json`. Apply upgrades only the candidate through
the official runner, runs migrations twice, checks fresh-install schema
equivalence, and compares every original column/ID/value/hash. Added nullable
columns remain NULL; migration history may only append official entries.
Existing config, native/legacy snapshots, results and costs are preserved.

Separate `restore-source.db` and `restore-candidate.db` restore both versions
with the backup API and verify schema and all records. `receipt.json` binds
artifact hashes to the source and tool/migration fingerprint; `report.json`
records technical results and the reviewed code revision. Neither report
exports keys, ciphertext, password hashes, dialogue or raw exception text.
Authenticated reader/ACL/CSV regression uses synthetic users against a
disposable copy in `tests/test_s6_cutover_readers.py`; it never writes the immutable
candidate. Production reader validation and credential restoration (#104)
remain separate required operational gates.

Repeat the same completed command to verify evidence without migrating or
rewriting files. Changes to source, completed candidate, reports, receipt,
backup, restored files or tool/migration code cause refusal (exit 2).
Missing/partial artifacts and an `applying.json` without a completion report
mean interrupted work: retain that workspace for investigation and retry
from the unchanged source in a **new empty workspace**. Do not delete or edit
evidence to make a failed run succeed. Safe failures print a status code and
leave a private failure report when the workspace has already been validated.

Before actual installation, HG1–HG5, writer-stop evidence, credential restore,
reader/ACL/CSV checks, health/login smoke and separate operational approval
must all match the final candidate/code/config/secrets. No automatic deploy,
down migration or production fallback exists. Before any new write, an
approved recovery may restore the old backup with matching code/config/key.
After **any** new write (including login, startup recovery or settings), retain
both databases, compare new records and prefer forward repair; reverting to
an old backup requires a human-approved loss/reconciliation plan. If new
writes cannot be excluded, treat recovery as after-new-write. Database
rollback never reverses provider charges.
