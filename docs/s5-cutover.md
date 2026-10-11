# S5 cutover and rollback hand-off (#99)

**Production release status: `release_blocked`.** Product completion means the
offline integration and regression checks in the #99 report pass. It does not
authorize deployment, operating database access, retention installation, paid
role verification or educational approval. This extends [S4](s4-cutover.md) and
[S3](s3-cutover.md); the contract is
[spec #90](https://github.com/DomineYH/misconcept_platform_v2/issues/90).

## 1. Drain writers and preserve a consistent source

Obtain separate operating authorization. Record code revision, migration
history, configuration, encryption master-key version and backup access policy.
Stop traffic and **all writers**: student/mentor calls, detached analysis tasks,
comparison streams/retries, role probes, model/catalog edits, scenario saves and
retention jobs. Wait for in-flight requests and provider clients to close and
check that no admission permits or running calls remain. Preserve matching
encryption keys separately from restricted database backups.

Use SQLite's backup API or `.backup` with a connection that sees committed WAL
pages. Copying only a live `.db` file can omit committed writes. Rehearse on an
isolated copy, never the operating source. Record backup time, file hash and
restore verification before making the cutover decision.

S5 adds **035_comparison_run.sql**, following S4's 034. It creates
`comparison_run`, rebuilds `generation_run` with exactly one classroom/comparison
owner, preserves classroom uniqueness and child references, and adds nullable
`api_usage_log.usage_scope`. Existing usage is not guessed or reclassified.

```bash
DATABASE_URL="${S5_CANDIDATE_URL:?Set the approved isolated candidate URL}" \
  uv run --frozen python -m src.db.migrations.migrate --through 35
```

Run it twice for idempotence. Compare original IDs, row counts, bodies, source/
config/input hashes, analysis plans/outcomes, accepted reports, credentials, ACLs,
observed costs and unknown values. All original columns must be identical;
old runs' comparison/panel columns and old usage scopes must be NULL. Check
`PRAGMA integrity_check` = `ok`, `PRAGMA foreign_key_check` = no rows, preserved
classroom uniqueness and all four default teacher/admin single/bulk CSV variants.
Restore a post-migration backup and compare schema and every row again.

`test_s5_wal_migration_restore_preserves_records_csv_and_rollback_boundary`
rehearses S4 committed WAL → backup → isolated candidate → 035 twice → restored
S5 copy. It compares every original table and hash, actual S4 analysis/message/
usage references, nullable/observed costs, all four CSVs and the unchanged source.
`test_035_preserves_records_indexes_and_foreign_keys` compares upgrade/fresh
schemas and checks classroom/comparison constraints. Temporary tests do not
certify the production filesystem or the final drained-writer copy.

## 2. Verify operating keys, models and topology

Run **one app instance with one asynchronous worker**. Admission, execution
locks and task cancellation are process-local. Multiple workers/replicas and
external task queues are unsupported. Before traffic resumes, verify health,
login, historical classroom readers/exports and administrator ownership using
the intended operating configuration and retained encryption keys.

Confirm each intended provider connection is enabled, its encrypted credential
can be read with the matching key version, and each intended model is enabled
with current exact-model capabilities and current role evidence. Student is
`s1-v1`, mentor `s3-v1`, analysis `s4-v2`; shared capability definition is `v3`.
Changes to credentials, capabilities or role contracts require explicit
reverification. Never relabel stale evidence with SQL. Real model/probe calls
need their own approved budget; startup does not make paid verification calls.

Comparisons use the entire common history with no per-panel truncation,
summarization or analysis splitting. Any panel's invalid options, unknown limits
or input/output overflow blocks all calls and records before admission. Analysis
comparison is one call per panel even when classroom analysis could split.
Ready models can differ from actual available operating models: the mock checks
do not certify model availability, pricing or context-estimator calibration.

## 3. Review comparison consumers and classroom isolation

| Contract | Behavior |
| --- | --- |
| `POST /admin/comparisons` | Administrator + CSRF, 1–3 panels, selected role only, unsaved/saved source snapshot; SSE for new execution, JSON for replay |
| Same request ID/input | Replays stored state without calls; changed input gives 409; one active comparison per administrator |
| SSE identity | `comparison_run_id`, `panel_id`, `run_id`, `turn_id`, per-run `seq` starting at 0; heartbeat comments are not output |
| Student output | Provisional deltas, then persisted completion; only completed student output can be adopted |
| Mentor/analysis output | Validated final result only; no raw JSON, reasoning or mentor reason deltas; degraded analysis stays partial |
| `GET /admin/comparisons/{id}` | Current owning administrator only; unknown/deleted/expired/other-owner records give 404; teachers give 403 |
| `/panels/{panel_id}/retry` | Explicit new request ID and latest generation ID; whole current execution must be terminal; failed/cancelled/interrupted/degraded only |
| `/panels/{panel_id}/stop`, `/stop` | Explicit generation identity; stops selected unfinished calls; incurred costs are not refunded |
| Disconnect/reload | Interrupt unfinished children, close upstreams, release permits; GET recovery, bounded 1 s polling for at most 10 s; no automatic paid resume |
| Adoption/new comparison | Builds one candidate pair from frozen input + chosen student output; new comparison links parent and preserves original |
| Model application | Target role's frozen identity/options copied into dirty form; separate save/publication required, expected-version 409 preserved |
| List/delete/expiry | Newest-first 20-item pages; terminal-only deletion; UTC creation + 30 days, never extended by read/retry/copy |

Classroom messages, reports, mentor scheduling counters, participation/statistics
and all default research CSVs must stay unchanged. Comparisons are not classroom
sessions. Comparison calls have NULL session IDs and `usage_scope='comparison'`;
they contribute to the overall API cost total and appear as comparison usage.
Missing tokens/cost remain unknown, and a total containing unknown costs is a
partial sum. Retrying adds a separate attempt/cost entry. Deleting comparison
content detaches ledger run IDs in the same transaction, preserving scope,
provider/model/time/status/nullable usage/cost without duplicating bodies.

## 4. Schedule and monitor daily retention

The external operator installs the schedule only with operating authorization.
First inspect the approved database with `--dry-run`, then run `--execute`:

```bash
uv run --frozen python -m src.cli.cleanup_comparisons \
  --database "${S5_DATABASE_PATH:?Set the approved database file}" --dry-run
uv run --frozen python -m src.cli.cleanup_comparisons \
  --database "${S5_DATABASE_PATH:?Set the approved database file}" --execute
```

Example systemd oneshot (set `WorkingDirectory` to the installed checkout and
use its absolute `uv` executable; the paths below are deployment placeholders):

```ini
[Service]
Type=oneshot
WorkingDirectory=/srv/misconcept
Environment=S5_DATABASE_PATH=/srv/misconcept/data/app.db
ExecStart=/bin/sh -c 'exec /usr/local/bin/uv run --frozen python -m src.cli.cleanup_comparisons --database "$S5_DATABASE_PATH" --execute'
```

Pair it with a timer using `OnCalendar=daily` and `Persistent=true`. Supply the
same reviewed runtime environment and permissions as the app. Avoid overlapping
cleanup jobs; the command does not create or migrate missing databases.
No timer/service or operating cleanup was installed by #99.

Collect the JSON summary (`mode`, `targets`, `deleted`, `failed`,
`skipped_running`, `completed_at`, `exit_status`) and process exit status. Exit 0
and `failed=0` establish a successful execution; dry-run does not establish a
successful purge. Alert on nonzero exit, missing daily execution or last success
older than one day. Inspect service logs/JSON, database permissions, locks, free
disk space and migration state, correct the cause and rerun explicitly. Check
expired backlog and `skipped_running` after retry. Persistent running entries
require request/cancellation investigation or drained single-worker restart
orphan recovery; never delete active provider/ledger records manually.

Cleanup deletes expired or ownerless terminal comparisons in batches of at most
100. Live generations/usage are skipped. Expired content is already hidden from
read/retry/copy even before physical cleanup; repeated cleanup is safe. Ledger
cost survives deletion. Live content deletion does not instantly erase private
backups: apply the existing restricted backup retention/destruction policy.

If a child's terminal or ledger write fails, a fresh session records a minimal
interrupted state with `storage_error` and closes any unfinished ledger row.
Siblings continue; all latest attempts being terminal finishes the comparison,
allowing the administrator to retry, start another comparison or delete it
without restarting. No uncommitted result is adopted. If storage also rejects
the fallback write, restore storage availability and use drained restart orphan
recovery; a displayed error alone is not evidence of a successful DB write.

## 5. Keep the S4 educational gate and pre-release checklist

S5 model comparison and role-format success are not educational approval. Keep
the [S4 quality gate](s4-quality-gate.md) and S4 cutover's human gates open until
the actual fixed-corpus baseline/candidate evidence and education lead approval
exist for every intended model/options/prompt/schema/estimator/chunk-policy
configuration. Mock outputs and scores cannot close those gates.

Before production approval, record:

- Reviewed code revision and #99 check results; isolated 035 preservation/restore
  rehearsal and authorized final stopped-writer backup.
- Single-worker topology, matching encryption keys, intended model/role evidence
  and separately authorized actual operating model checks.
- Classroom readers, ACLs, statistics and every CSV consumer checked on the
  authorized copy; comparison ownership/CSRF/unknown cost verified.
- Daily retention installed separately, a successful execute summary and failure
  alert/retry procedure checked; backup retention policy acknowledged.
- Actual S4 educational approval records, paid budget boundaries and explicit
  production access/deployment/rollback decision by the authorized owner.

The implementation report distinguishes product acceptance from these pending
operating/human approvals. No production release approval is inferred from CI.

## 6. Rollback limits and known exclusions

Before new S5 writes, stop writers, retain the candidate and restore the consistent
S4 backup with SQLite backup API. Verify original schema/migration history,
rows/hashes, readers, ACLs and CSVs, then start the recorded S4 code/configuration
with matching keys. There is no supported 035 down migration; never drop new
columns or point old code at the S5 schema.

After any new write, first drain writers and preserve a consistent current S5
backup. Comparisons, ledger/probe calls, role reverification and administrator
saves all count as writes. Prefer forward repair. Restoring the original S4
backup discards newer comparisons, costs and any newer classroom/configuration
data; reconciliation needs explicit authorization and verified preservation.
The temporary rehearsal demonstrates this loss after a new comparison/ledger
write. A lossless downgrade is not promised.

AC-12 independent conversations are excluded. There is no automatic winner,
shared comparison link/CSV, session import, long-analysis splitting, persistent
draft workspace, indefinite idempotency tombstone or multi-worker support.
Saved-state recovery cannot reconstruct uncommitted tokens or guarantee exact
reproduction of model aliases/non-deterministic output. Real provider closure,
proxy buffering and estimator accuracy need the separately authorized operating
checks; local SDK mocks prove the app lifecycle only. No paid calls, deployment,
operating DB edits or production retention schedules are part of this ticket.
