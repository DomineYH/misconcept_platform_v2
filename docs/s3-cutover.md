# S3 cutover and handoff

This is a maintenance procedure for an administrator's separately authorized
operating window. Ticket #76 rehearses temporary SQLite and mocked SDKs only;
it performs no deployment, operating data migration, key change or paid call.
For an installation older than the contracted S2 schema, first complete
[the S2 preservation and conversion procedure](s2-cutover.md). S3 does not
replace that procedure or modify migrations 029–031 or the baseline SQL.

## 1. Record the boundary and drain

Record the old/new code revisions, effective database destination, schema
history through `031_scenario_contract.sql`, single app instance/async worker,
configuration and provider encryption-key versions. Retain the secret material
for each referenced key version separately in the controlled secret store;
keep old signing/config secrets as well. Backups contain private messages,
password hashes and encrypted credentials; protect the maintenance directory
and backup files as in the S2 procedure. Never place those secrets or private
records in a public report or this repository.

Close new lesson/generation/probe traffic and ask teachers to pause. Let the
old runtime finish its running student, mentor, analysis and administrator
probe/catalog calls. Inspect these gates before stopping it:

```sql
SELECT id FROM generation_run WHERE status='running'
  OR (started_at IS NOT NULL AND finished_at IS NULL);
SELECT id FROM model_probe WHERE status='verifying'
  OR (started_at IS NOT NULL AND finished_at IS NULL);
SELECT id FROM api_usage_log WHERE finished_at IS NULL
  AND (status='running' OR started_at IS NOT NULL);
```

All three must be empty. For crashed/orphaned calls use the old runtime's
existing interruption/recovery procedure, then inspect again; do not fabricate
successful completion, usage or session end timestamps with SQL. Active native
S2 sessions need not be reconstructed or forcibly ended for these additive
migrations. Their snapshots/hash stay unchanged, and further calls require
current authorization and role verification. Historical reconstructed sessions
remain read-only, including no end, retry or reanalysis.

Stop **every writer**: app workers, cron/jobs, scripts, seed/admin processes,
SQLite shells and maintenance tools. Record write-stop time and keep traffic
closed. Old/new runtimes must never write the same database concurrently.

## 2. Back up and prove restoration

With writers stopped, use SQLite's backup API with a read-only source
connection to create a new private consistent source copy. Follow the concrete
Python example and restrictive artifact permissions in
[S2 step 2](s2-cutover.md#2-make-a-wal-consistent-source-copy-and-retain-key-versions-separately).
Committed WAL pages must be included; copying only the main `.db` file or
removing live WAL/SHM sidecars is unsafe.

Retain this original S2 copy unchanged. Use the backup API again to make an
isolated candidate and a separate restore rehearsal. Compare complete schema,
all tables' original columns, row counts and content/checksums, including
migration history, IDs, ownership/groups, messages/turn links, scenarios,
snapshots/hash, results/labels/reasoning, usage/cost and unknown NULLs. Check
`PRAGMA integrity_check` returns `ok` and `PRAGMA foreign_key_check` is empty.
Also validate owner/admin history and CSV access, foreign-user denial and
historical read-only behavior through the application against the candidate.
A successful synthetic test does not certify an operating filesystem or the
latest production copy; repeat on the final stopped-writer source.

## 3. Apply only the additive S3 expansion to the candidate

From the reviewed S3 code revision, point the existing official runner at the
isolated candidate with an explicitly supplied `DATABASE_URL`:

```bash
DATABASE_URL="${S3_CANDIDATE_URL:?Set the approved isolated candidate URL}" \
  uv run --frozen python -m src.db.migrations.migrate --through 33
```

`S3_CANDIDATE_URL` must refer to the approved isolated SQLite candidate, never
the running source. The runner applies 032 then 033 and records each once:

| Migration | Nullable field | Historical value |
| --- | --- | --- |
| 032 | `generation_run.mentor_reason_summary` TEXT | NULL |
| 033 | `api_usage_log.context_budget_json` TEXT | NULL |

Rerun against the same candidate to verify idempotence. Compare all original
column values again and check both new fields are NULL for old rows. Preserve
old `mentor_judgment` operations, usage/cost, results, native/reconstructed
snapshots and hashes exactly. Do not backfill reasons, input estimates, actual
tokens or costs, replace selected models/options, or rewrite historical SQL.
New estimates are numeric/version metadata on future student/mentor attempts,
separate from actual provider usage and pricing. Reasons and budget metadata
are excluded from teacher HTML, SSE, status/replay, history and CSV.

Repeat integrity/FK checks and history/CSV/access checks. Retain revision,
schema history, preservation comparisons, backup/restore evidence and check
results privately for the administrator's review. No S3 destructive conversion
or down migration is provided. Coordinate migration numbering with concurrent
S4 work before installation; never silently reuse a conflicting number.

## 4. Install and explicitly reverify

With writers and traffic still stopped, install the verified candidate using
SQLite's backup API into the approved destination while no process holds that
database. Replace code/schema within this closed-traffic window. Retain the
original backup, failed/candidate copies and old revision/configuration.
Start the reviewed S3 runtime with **one app instance and one async worker**,
`ENV=production TESTING=false`, and the retained secret configuration. Admission,
execution/cancellation registries and slots are process-local; multiple workers
are outside this supported deployment.

Check health/login, old result/replay/CSV preservation and live ACLs. Read-only
checks do not generate provider calls. Inspect `/admin/ai` verification states:

- The mentor output contract is now `s3-v1`; previous `s1-v1` mentor success is
  stale. Student and analysis output contracts remain `s1-v1`.
- Capacity definition v2 is a separate change: old capability evidence for an
  affected model makes **all** its roles stale, including student and analysis.
  A mentor-only retest cannot restore stale student or analysis evidence.
- Old results remain readable. New calls, publication and new lessons require
  current successful evidence for the roles they use. A model's selected ID,
  options and old snapshot are never automatically substituted.

The administrator must explicitly choose each needed role's test and accept
its displayed cost possibility. There is no startup/background paid retest.
The existing registration/edit/probe flow safely refreshes the stored capability
definition; never promote old evidence to a new version with SQL. Student
tests use two synthetic text/stream calls. Mentor tests use at most two
sequential structured calls (manual positive, auto negative), stop on first
failure, and reserve at most 1,500 output tokens per synthetic call. No automatic
retry or third repair call is made. Analysis uses its existing role test.

Actual paid reverification, account/model availability and educational quality
require operating approval and remain outside #76. After that approval and
successful role tests, verify an authorized new lesson, student-only execution,
off/manual/auto behavior, live timeout/slot configuration and teacher privacy.
AC-15's actual multi-model comparison panel belongs to S5; S3 verifies the
student-only boundary. Record the **first new S3 write** and reopening time;
role probe/ledger writes and administrator edits already count as new writes.

## 5. Rollback and new-write preservation

**Before any new S3 write:** close traffic, stop all writers, retain the failed
candidate and restore the original S2 backup with SQLite's backup API. Compare
schema, migration history, all original rows/checksums, IDs, snapshots/results,
usage/cost and access; restart the recorded S2 code/configuration with matching
key versions. Do not run old code on the S3 candidate or manually drop columns.

**After any new S3 write:** stop writers and first make a consistent backup of
the current S3 database/WAL. Retain both versions, new IDs/messages/runs/results,
coaching/usage, role tests, model/config edits and secret key versions. Prefer
forward repair. Any adjusted restore requires explicit reconciliation and
verification of newer records and permissions. Restoring the original S2 copy
alone discards those writes; a lossless downgrade is not promised. Do not
discard research records because the older runtime cannot read new fields.

`tests/test_s3_migration.py` rehearses S2-final → 032 → 033, idempotent fresh
installation, committed WAL backup/restore, original-column preservation and
historical HTTP/CSV/ACL behavior. `tests/test_s3_integration.py` and
`tests/test_s3_load.py` reuse existing SDK fixtures and S1 load prior art. Their
injected delay/failure results establish regression behavior only, not real
provider p95, billing accuracy, educational quality or production readiness.

## S4 analysis capacity addendum (#82)

S4 keeps the S3 student/mentor `utf8-v1` policy unchanged. Capacity definition
v3 adds limit-specific official sources and a 2026-10-11 check date for the
same exact models; it also rejects a smaller explicit catalog capacity.
As with v2, changing the shared definition stales **all** roles for affected
models, even though only analysis gains a planner. Administrators must refresh
the definition through the existing registration flow and explicitly reverify
each needed role. Historical reports remain readable; no background paid
reverification or snapshot/model/option substitution is performed.

Analysis now uses the `s4-v2` role contract and a separate
`utf8-v1-s4-20pct` planner. A low frozen output cap can block analysis before any
provider call. A chunked plan requires explicit confirmation; execution remains
unavailable until #83 supplies the chunk executor. No additional migration is
needed for #82: migration 034 already provides `generation_run.plan_json`.
