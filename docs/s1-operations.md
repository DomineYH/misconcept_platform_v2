# S1 operations and recovery hand-off (#55)

This procedure extends the [S0 cutover](refactoring.md#s0-phase-a-integration-and-pilot-cutover-33)
and follows [S1 spec #40](https://github.com/DomineYH/misconcept_platform_v2/issues/40).
The rehearsal uses temporary SQLite, mock SDK transports and localhost only.
Passing it does not authorize production deployment, operating database changes
or paid provider tests. Each remains a separate approval with actual paths,
maintenance timing and, for paid tests, an agreed budget.

## Before an approved transition

1. Record the current code revision, frozen dependencies, database path, safe
   session configuration and exact role model IDs/options. Retain the old code
   and configuration for recovery. Review blank public problems and the S0
   completed-pair context setting before new sessions resume.
2. Close lesson traffic, announce the maintenance window and end active sessions
   using the existing teacher/admin close paths. Stop **every writer**: all app
   instances/workers, analysis/probe tasks, scheduled jobs, seed/admin scripts
   and database-writing shells. Old and new code must never write the same
   SQLite database together.
3. Make a consistent SQLite backup using its backup API or the SQLite CLI
   `.backup` command. This includes committed WAL pages; copying the main live
   `.db` alone does not. Do not remove live WAL/SHM sidecars. Keep an untouched
   backup, and create the rehearsal copy from it using the backup API as in S0.
4. Securely preserve `PROVIDER_SECRET_ENCRYPTION_KEY` **and its version identifier**
   separately from the database backup. The master key is standard base64 for
   exactly 32 random bytes, separate from `SESSION_SECRET`. Do not place keys,
   passwords, plaintext provider errors or database copies in the repository,
   URLs, browser storage, command transcripts or hand-off reports.
5. With an explicitly selected copy path, run the official installer twice:

   ```sh
   DATABASE_URL=sqlite+aiosqlite:////maintenance/s1-copy.db uv run --frozen python -m src.db.migrations.migrate
   DATABASE_URL=sqlite+aiosqlite:////maintenance/s1-copy.db uv run --frozen python -m src.db.migrations.migrate
   ```

   These are example paths for separately approved maintenance. The S1 history
   is 024 → 025 provider connections → 026 models/settings → 027 probes/ledger
   → 028 generation providers. S1 A15 adds no migration. Compare with a fresh
   final installation, `PRAGMA integrity_check = ok`, an empty
   `PRAGMA foreign_key_check`, migration history and preserved IDs, messages,
   metadata, run links, question analyses, summaries, feedback and stored costs.
   Read both teacher/admin history and CSVs; verify ownership/group denials.
   Historic attempt/revision/price dates stay unknown, and stored zero costs
   remain zero. Reading the dashboard must not reprice old rows.
6. If the rehearsal passes, apply the same official command to the approved
   destination with all writers still stopped, then repeat data and integrity
   checks. Production startup does not perform schema upgrades. Never edit the
   historical SQL or infer missing old turns/attempts.

## Key registration and resuming lessons

Start **one app instance with one asynchronous worker**, `TESTING=false`, safe
session/DB configuration and the master key/version supplied by infrastructure:

```sh
ENV=production TESTING=false uv run --frozen uvicorn src.main:app --workers 1
```

Do not use reload, additional replicas or workers. Slots, connection revocation,
probe tasks and execution locks are process-local. Another worker would bypass
limits, miss cancellations and could interrupt another worker's active records.
Startup marks orphaned running attempts/probes/runs interrupted without calling
the provider again. SQLite remains the durable history, not a distributed queue.

Check health, actual administrator login, **AI 연결·모델**, historical lesson
readers, analyses and CSVs before reopening lessons. These work without provider
or master keys; AI calls alone give configuration guidance. A missing or invalid
master key blocks key storage and AI, while an unreadable stored credential
isolates its connection. Other healthy providers remain manageable/usable.

Use the administrator screen to enter provider keys again; no environment key
import or fallback exists. Every save/replace/disable/enable/delete needs the
current administrator password and CSRF. Reload after a version conflict;
never replay a secret mutation silently. Saving keys/models and opening the page
do not call a provider. Explicit catalog refresh is non-generating, though it
uses an administrator slot and a recorded `model_list` attempt.

Register the **exact existing OpenAI model IDs** from scenario/config settings
and preserve their options. The code definitions cover `gpt-5-mini`, `gpt-5.2`
and their listed snapshots, `claude-sonnet-4-6`, and `gemini-2.5-flash`; account
access is not guaranteed. Unknown models need a sourced capability definition
before generation. Catalog metadata conflicts block admission and the recheck
immediately before execution, including a refresh after activation.

Role probes can incur charges and need the separately approved test budget.
Explicitly verify each required student/mentor/analysis role, then activate the
model. At most two sequential synthetic calls occur per selected role, stopping
on failure with no automatic retry. A successful role proves its output contract,
not educational quality. Replacement/deletion invalidate credential evidence;
disable/reactivate requires revalidation. Another role's success grants no access.

Role authoring defaults start empty and only assist new scenario writing. They
do not replace existing scenario/session models. Until S2, lesson calls still
resolve exact current OpenAI IDs; Claude/Gemini are available for explicit
catalogs/probes, not automatic lesson-provider selection. S3/S4 educational
redesign and S6 deployment work are separate.

After approved role tests, review limits/timeouts and reopen new lessons.
Defaults are total 8, each provider 4, admin 3; admin work leaves one slot globally
and per provider for ordinary calls. Capacity refusal is immediate 429 with
guidance, without a queue. Reducing limits preserves active calls. Student,
mentor/judgment, greeting, probes and catalogs do not retry automatically;
classification/synthesis may retry one transient failure before any response.
Backoff releases capacity and stays within the same absolute deadline.

## Recovery and data-loss boundary

For missing/malformed master configuration, restore the original master key and
matching version first. A version mismatch or damaged ciphertext/nonce cannot
be repaired by reading the DB. If recovery is impossible, reauthenticate, disable
or delete the unusable provider key and enter a replacement under the configured
master key; deletion works without decryption. It removes only key material and
masked hint, preserving the connection, models, references, audit and usage.
Reverify roles before activating lessons. No web key rotation or key ring exists.

Key replacement permits already admitted old-key calls to finish, but late
old-revision catalog/probe results cannot authorize the new revision. Disable or
delete immediately blocks new admission and requests cancellation of **all active
revisions**. Local HTTP responses/SDK clients/slots close; provider generation and
billing may continue. Treat uncertain usage and cost as unknown, never as zero.
Python plaintext memory erasure is not guaranteed.

If code/schema rollback is needed, close traffic and stop every new-version
writer. Preserve a separate consistent backup of the current state before
restoring when feasible, then restore the retained pre-S1 backup with SQLite's
backup API into the stopped destination. Restore its matching code/configuration
and master key/version, check schema/history, integrity/FKs and preserved readers,
then restart one worker. Do not replace a live database underneath WAL/SHM files
or try an improvised down migration.

**All data written after the retained backup is lost on restore**: messages,
sessions, coaching, analysis, configuration, provider key changes, probe/audit and
usage records. Agree that loss boundary before recovery. Losing ledger rows does
not reverse provider charges. Preserve the post-cutover copy for an explicitly
planned recovery; there is no automatic reconciliation into the old schema.

## Evidence and remaining limits

`tests/test_phase_a_cutover.py` rehearses both 023/024 → final twice, compares
fresh schema, retains committed WAL data and old costs, exercises authenticated
readers/exports, and verifies restoration removes a new write. Existing migration
tests retain run/child identities and uniqueness constraints. `tests/test_s1_load.py`
uses 20 teacher sessions and three blocked admin jobs to check ordinary headroom,
429/no extra attempts, independent SQLite writes, duplicate replay, old-key
completion, replacement invalidation and cancellation across both revisions.

Required validation is in [README](../README.md): full pytest, ruff, black, all
Chromium browser fixtures and localhost incremental streaming against the pinned
SDK. No paid API calls, operating DB edits or deployments are part of those checks.
Mock timing is not actual provider/server performance; actual latency and total
paid cost remain **unknown**. No p95 target, billing cessation, real account access
or educational quality is established.

The dashboard shows stored known estimates and separate unknown generation
attempt/list counts. Missing Gemini thoughts means unknown output/cost even with
thinking budget zero. Claude's observed zero cache writes need no TTL split, but
unreported tier/geography or nonzero writes without TTL remain unpriced. See
[pricing scope](ai-usage-pricing.md); partial list-rate sums are not invoices.
The [screen/data contract](agents/ai-connections-screen.md) and the common
admission/execution/ledger boundary remain the contracts for later tickets.
