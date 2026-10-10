# S2 conversion and cutover runbook (#68)

This is a **preparation procedure**, not evidence of an operating deployment.
Operating database access, maintenance, deployment and paid generation need
separate authorization. Rehearse on a consistent copy first. Never run this
work order against `.env` or `dialogue_sim.db`.

The coordinator confirmed: retain `030_mentor_policy.sql` unchanged and use
**031_scenario_contract.sql** for final cleanup, superseding the 030 number in
#57 D11 / #68. Baseline and previous SQL remain immutable.

## 1. Record the deployment facts and finish existing work

Record the old code revision, schema/migration history, destination paths,
configuration revision, file owner, SQLite version and maintenance contact.
Capture the **actual effective legacy settings** explicitly, without secrets,
using the structure in `tests/fixtures/s2_legacy_effective.json`. That file is
synthetic example data; never substitute it for deployment facts. Capture the
student BASE educational rules, each effective subcall's model/options,
context limit, intervention threshold, setting source and capture timestamp.
Unknown values must be null and require review, not development defaults.
Do not import environment API keys, passwords, bearer tokens, encryption keys
or ciphertext into the setting manifest or source archive. An ordinary DB
backup necessarily retains password hashes and encrypted credential records;
protect it as private operational data.

Announce maintenance and ask teachers to finish lessons. While the **old**
application still runs, use its existing teacher end/close or administrator
end-session actions; wait for analysis and provider probes to complete. Do not
fabricate `ended_at` or `finished_at` with SQL. Old active sessions cannot be
ended by the new runtime: they will be historical read-only sessions.

After additive expansion, the tool checks all of these gates before applying
conversion and again inside the final migration transaction:

```sql
SELECT id FROM session WHERE ended_at IS NULL;
SELECT id FROM generation_run WHERE status='running'
  OR (started_at IS NOT NULL AND finished_at IS NULL);
SELECT id FROM model_probe WHERE status='verifying'
  OR (started_at IS NOT NULL AND finished_at IS NULL);
SELECT id FROM api_usage_log WHERE finished_at IS NULL
  AND (status='running' OR started_at IS NOT NULL);
```

All must be empty, including soft-deleted sessions that were never ended. A
crashed old deployment must use its existing orphan interruption/recovery
procedure; S2 conversion does not mark runs completed or end sessions.

Stop **every writer** before the final source copy: all app workers, cron/jobs,
admin/seed scripts, SQLite shells and other maintenance processes. Close
traffic. Record the write-stop time. Old and new code must never write the same
DB concurrently. A rehearsal on yesterday's backup does not validate today's
final source: repeat the procedure with a new private workspace for that copy.

## 2. Make a WAL-consistent source copy and retain key versions separately

Use SQLite's backup API, not a filesystem copy of only the main DB. Committed
WAL pages must be included; never remove live WAL/SHM files. Under separately
approved operating access, supply the recorded paths, restrictive permissions
and a read-only source connection:

```python
import os
import sqlite3
from contextlib import closing
from pathlib import Path

source = Path('/approved/source.db')
copy = Path('/private/maintenance/source-copy.db')  # must not exist
os.close(os.open(copy, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
with closing(sqlite3.connect(source.resolve().as_uri() + '?mode=ro', uri=True)) as db:
    with closing(sqlite3.connect(copy)) as destination:
        db.backup(destination)
```

Store each encryption master-key **version** and its secret material in the
separately controlled secret store. Record which key versions are referenced
by `provider_connection.encryption_key_version`, and keep the old session
signing/config secrets needed to restart the old code. Do not put secret
material in the repo, manifest, archive or report. Restore of encrypted
credentials is incomplete without the matching separately retained keys.

## 3. Dry-run, convert, verify, restore and contract the isolated copy

Choose an empty private directory, outside the source/settings files. The tool
creates it with mode 0700; artifacts are 0600. Existing shared directories,
symlinks and hard-linked artifacts are refused. Run from the reviewed code
revision; paths below are examples:

```bash
uv run --frozen python -m src.db.s2_cutover \
  --source-copy /private/maintenance/source-copy.db \
  --workspace /private/maintenance/s2-rehearsal \
  --settings /private/maintenance/effective-settings.json
```

Default mode changes no source rows and does not apply conversion or cleanup.
It retains `backup.db`, expands `rehearsal.db` through 029 and the existing 030,
then writes immutable `effective.json`, `archive.json`, and `manifest.json`.
The source archive captures every legacy scenario and all templates/frameworks,
including unreferenced ones. It excludes users, credentials and app settings.
Video originals stay only in this private archive and the backup. Inspect the
source/target checksums, missing-reference/model reasons, text differences,
privacy change, BASE rules, subcall options and bucket-to-rolling warnings.

After inspecting the dry-run, apply to the **same rehearsal copy only**:

```bash
uv run --frozen python -m src.db.s2_cutover \
  --source-copy /private/maintenance/source-copy.db \
  --workspace /private/maintenance/s2-rehearsal \
  --settings /private/maintenance/effective-settings.json --apply
```

`--apply` does not deploy or write the source. The sequence is:

1. Verify the retained backup still matches the source; validate immutable
   effective settings/archive/manifest and refuse active sessions or unfinished
   generation/probe/usage runs.
2. Reuse the existing deterministic conversion. Preserve IDs, groups,
   activation/deletion and timestamps; make legacy scenarios typed reviewable
   drafts. Existing converted data is skipped; an edited config or changed
   legacy source reports a conflict and blocks cleanup. The converter may
   already have committed other candidates; originals remain in the backup.
3. Restore `backup.db` into separate `restore.db` with the backup API. Compare
   the complete schema, every table's columns/row counts/content hashes and FK /
   integrity checks. This proves restoration before destructive cleanup.
4. Compare historical messages, results/labels/grades/reasoning, usage rows,
   users/roles/groups and assignments by original column values. Validate
   reconstructed snapshots/hashes and preserve existing snapshot values.
   These data checks protect ownership/group access; the HTTP regression tests
   separately exercise actual owner/foreign/admin access and exports.
5. Write `receipt.json`, binding the source artifacts, restore drill, preserved
   projections and every pre-cleanup row. The official runner checks this
   evidence again within `BEGIN IMMEDIATE`, before any DROP.
6. Apply 031 atomically: retain canonical scenario/config/review/provenance
   fields, remove legacy scenario columns/FKs and template/framework tables,
   require an object config, and retain historical session/result tables.
7. Repeat FK / integrity, historical content and snapshot checks. Write private
   `report.json` with `status=ready`, counts/hashes, conversion outcomes and
   `restore_verified=true`. This means the **copy** is ready for administrator
   review and the operating decision, not that production was deployed or
   that any scenario has been automatically published.

A fresh empty install can apply 031 without preservation evidence because it
has no legacy source. A populated install using the ordinary migration command
is refused without a matching `--cutover-receipt`; automatic development startup
cannot silently delete unpreserved legacy material. `--through 30` is only the
explicit additive preparation boundary; never serve it as the final runtime.

Rerun the same commands against unchanged inputs to verify idempotence. A
rolled-back 031 retries using the original receipt; interruption after its
commit but before report creation resumes by validating the retained evidence
and final row hashes, without legacy readers. Changed settings, backup,
archive, manifest, historical rows or completed copy cause refusal. An edited
config remains intact. Preserve artifacts and investigate; do not truncate
files or reset native configs. A partially written artifact/backup from a
process or filesystem failure is intentionally not replaced: start a new empty
workspace from the untouched source copy. Incomplete or conflicting attempts
are not ready reports.

## 4. Operating switch, only after authorization

With all writers still stopped, repeat the rehearsal on the final approved
consistent source copy and compare it to the final operating source before
installation. Review the report, administrator conversion diffs and research
sample exports. Keep the backup/archive/manifest/receipt/report and old code
revision through acceptance.

Install the **verified contracted copy** into the approved destination with
SQLite's backup API, while no process holds the destination DB. Alternatively,
apply the same reviewed migration/conversion procedure to the separately
approved maintenance copy destined for installation. Do not copy a live main
DB file around sidecars, run old code on the new schema, or run 031 before
preservation verification. Schema installation and code replacement occur in
one closed-traffic maintenance window.

Start the reviewed S2 code as one app instance / one async worker, with
`ENV=production TESTING=false` and the separately retained secret settings.
Check health/login, historical owner/admin readers, foreign-user denial,
existing result/CSV preservation, and conversion review. The runtime reads
only config_json and frozen snapshots; there is no JSON-or-legacy fallback.
Past reconstructed sessions are read-only (including no reanalysis or end).
An administrator must complete missing essentials, resolve blocking reasons,
acknowledge that revision's warnings and publish through the real editor.
Only then may teachers start new native lessons. Confirm snapshot immutability,
current model/provider authorization and live ACLs without paid generation.
Paid provider smoke/quality tests need their own approval. Record the first new
write and reopening time; preparation success is not deployment completion.

## 5. Rollback boundary and recovery

**Before any new S2 write:** close traffic, stop all S2 writers, preserve the
failed candidate and restore the original retained backup into the approved
destination using SQLite's backup API. Verify complete schema, counts/hashes,
IDs, dialogue, results, groups/FKs and old migration history; restart the
recorded old code/config with the matching key versions. No automatic down
migration is provided.

**After any new S2 write** (including an administrator edit/publication): stop
writers and back up the current S2 DB/WAL first. Retain both versions and the
new sessions/messages/coaching/results/usage/config changes. Compare all new
IDs/content against the pre-cutover backup. Prefer forward repair; if adjusted
restoration is necessary, reconcile the newer records explicitly and verify
ownership/results again. Simply restoring the old backup loses those newer
writes; a lossless downgrade is not promised. Never discard new research data
because the old runtime cannot read the new schema. Encryption key versions
must remain separately available for both retained datasets.

`tests/test_s2_cutover.py`, the historical migration/cutover tests and the live
student check rehearse synthetic copies with mocked provider boundaries. They
verify preparation and restoration, not operating filesystem permissions,
reverse-proxy behavior or real provider billing/latency.
