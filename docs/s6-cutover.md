# S6 candidate rehearsal (#102, #103)

This prepares evidence on an explicitly isolated copy. It does not install a
database, stop writers, approve educational quality, or authorize production.
Technical success always leaves `release_blocked: true`.

Use an empty SQLite source copy or an official 023–035 database. For 023–030,
provide the captured effective settings and a separate S2 workspace as below.
The legacy path completes S2 conversion and its validated 031 receipt before
backing up that result into the separate S6 candidate. Arbitrary schemas,
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

Current-format dry-run creates a WAL-consistent `backup.db` and unchanged
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

For an official 023–030 copy, capture the actual effective deployment settings
using the [S2 manifest procedure](s2-cutover.md). Missing settings are not
inferred from current defaults. Settings must be a private 0600 file outside
both workspaces; all three paths must be isolated, without symlinks or hardlinks.
The two workspace directories must be disjoint, initially empty and private.

```sh
uv run --frozen python -m src.db.s6_cutover \
  --source-copy /private/maintenance/legacy-copy.db \
  --settings /private/maintenance/effective.json \
  --legacy-workspace /private/maintenance/s2-evidence \
  --workspace /private/maintenance/s6-latest

uv run --frozen python -m src.db.s6_cutover \
  --source-copy /private/maintenance/legacy-copy.db \
  --settings /private/maintenance/effective.json \
  --legacy-workspace /private/maintenance/s2-evidence \
  --workspace /private/maintenance/s6-latest --apply
```

The two legacy arguments are required together and only apply to 023–030.
Legacy dry-run checks the official source schema, creates the S2 backup and
030 expansion/conversion plan, and refuses unfinished work. It does not
produce a latest candidate until S2 is ready. Apply reuses S2's conversion,
preservation/restore checks, receipt and guarded 031 migration. An existing
completed S2 workspace is supported and revalidated through the S2 entry point.
Its `rehearsal.db`, archive, manifest, receipt and report stay unchanged.
S6 `backup.db` is the separate 031 backup; the original legacy backup and its
restore remain in the S2 workspace. The S6 report binds the original revision
and full source-state hash, effective settings hash, S2 receipt/report hashes
and preservation evidence to the latest candidate and tool/code fingerprint.
Repeating either completed entry point validates the same evidence. Changed
settings, source, conversion plan, receipt, S2 result or S6 candidate are refused.
Once S6 evidence is sealed, missing S2 artifacts are refused before invoking
S2's interruption/resume flow; completed evidence is not silently recreated.

Synthetic tests cover full 023/027/030 rehearsals and all official 023–030
schema boundaries. Authenticated HTTP/CSV checks run on disposable copies of
the latest legacy candidate and preserve the sealed evidence. A separate
synthetic new-write/old-backup restore check demonstrates that restoring an
old backup loses later writes; it is evidence of the recovery boundary, not
an automatic rollback facility.

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
