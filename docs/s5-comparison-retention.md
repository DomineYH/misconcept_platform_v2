# Comparison retention operations

Comparison input, snapshots and results expire at UTC creation + 30 days.
Reading, copying, retries and child comparisons do not extend a record's expiry.
Expired records disappear from owner-only access immediately; live calls have
an absolute deadline bounded by the remaining retention time. Account deletion
requests cancellation; startup orphan recovery interrupts abandoned calls.

Only terminal expired or ownerless comparisons are cleanup candidates. The
command skips running comparisons, generations and usage attempts. Each write
transaction deletes at most 100 comparisons and first detaches their usage run
references in the same transaction. Generation records and comparison contents
are deleted; actual nullable usage/cost, provider, model, status and the
`comparison` scope remain. Unknown measurements are never replaced with zero.
A failed batch rolls back completely; prior committed batches remain deleted.

From the application directory, with the deployed locked environment:

```sh
uv run --frozen python -m src.cli.cleanup_comparisons --database dialogue_sim.db --dry-run
uv run --frozen python -m src.cli.cleanup_comparisons --database dialogue_sim.db --execute
```

The database must already exist and be migrated through 035. The command never
creates or migrates it. Dry-run opens it read-only. Exit 0 means the requested
mode completed, 1 means database/batch failure, and 2 means invalid arguments.
Logs contain mode, target/deleted/failed/skipped-running counts and UTC completion
time, without comparison inputs, outputs, keys or raw database errors. Do not
run overlapping cleanup jobs. The application supports one async worker; keep
that deployment constraint during cleanup and orphan recovery.

Example external daily cron entry (illustration only; adapt paths and account):

```cron
15 3 * * * cd /srv/misconcept && /usr/local/bin/uv run --frozen python -m src.cli.cleanup_comparisons --database /srv/misconcept/dialogue_sim.db --execute >> /var/log/misconcept-comparison-cleanup.log 2>&1
```

Use the infrastructure scheduler to record the process exit code and alert on
nonzero exit or no successful `completed_at` within 26 hours. Check the most
recent successful UTC completion time, deleted/failed counts and skipped-running
count daily. After failure, fix the reported deployment/storage problem, rerun
dry-run, then execute again; repeat execution is safe. Investigate growing
expired or ownerless targets and unusually persistent running skips. After an
application crash, perform the normal single-worker restart (which interrupts
orphans) before rerunning cleanup; never mark healthy live calls as orphans.

Use the existing [SQLite backup and cutover procedure](../README.md) before
operating changes. Deleting live database rows does not immediately erase copies
from backups, WAL files or storage snapshots. Those copies remain subject to
access controls and the existing backup retention/rotation policy; do not claim
that cleanup destroys every backup copy. This document installs no schedule and
authorizes no operating database cleanup or paid provider calls.
