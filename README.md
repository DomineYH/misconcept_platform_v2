# misconcept_platform_v2
misconcept_platform_v2

## Local development

Requires Python 3.11+ and uv. Install the locked runtime and test dependencies:

```sh
uv sync --frozen --extra dev --python 3.12
cp .env.example .env
# Set SESSION_SECRET (openssl rand -hex 32) and ADMIN_DEFAULT_PASSWORD
# in .env before seeding a new development DB. Provider keys are not required to boot.
uv run --frozen python -m src.db.seed
uv run --frozen uvicorn src.main:app --reload
```

## Provider connection setup and recovery

The administrator's **AI 연결·모델** screen stores provider keys encrypted in
SQLite. Login and reading this screen require no provider or encryption key.
For key storage, supply `PROVIDER_SECRET_ENCRYPTION_KEY` as **standard base64
encoding of exactly 32 random bytes** (generate with `openssl rand -base64 32`)
and `PROVIDER_SECRET_ENCRYPTION_KEY_VERSION` as a nonempty identifier, such as
`v1`, through infrastructure secrets. Keep this key separate from `SESSION_SECRET`.
A missing/invalid master key blocks storage and credential decryption, not startup.

Every key save/replacement, disable/reactivation and deletion requires the current
administrator password and CSRF. Five failed confirmations per administrator or
client address within five minutes temporarily block further confirmations.
Version conflicts require reloading the screen; requests are never silently replayed.
Deleting a key clears its ciphertext, nonce, master-key version and masked hint,
while preserving the connection row and audit history. Disable/delete work even
when the stored key cannot be decrypted. Restore the original master key and
version first; if they cannot be recovered, reauthenticate, delete the unusable
key, then register a replacement under the configured master key.

Back up SQLite with its consistent backup mechanism (for example,
`sqlite3 dialogue_sim.db ".backup '/secure/backups/dialogue.db'"`), rather than
copying only a live `.db` file while WAL writes are active. Securely preserve the
matching master key **and its version separately from that database backup**.
Restore both before using stored credentials; the database alone cannot recover
provider keys. No automatic master-key creation, web rotation or key ring exists.
Python memory clearing of plaintext is not guaranteed.

The S1 integration and recovery rehearsal is documented in the
[operations hand-off](docs/s1-operations.md). Production transition and paid
provider tests require separate approval. Explicit OpenAI,
Claude and Gemini catalog refresh uses the saved
DB key and the non-generating Models API, with no retries and a default 30-second
deadline. Successful lists are cached for 24 hours; failures preserve the list.
Models can be registered directly, start disabled/unverified, and have immutable
IDs. The initial authoring defaults are empty. Model options and singleton call
limits/timeouts are validated on save. See the
[screen/data contract](docs/agents/ai-connections-screen.md) for capability sources
and the APIs later tickets must reuse.

Saving keys or models does not call a provider. Explicit OpenAI, Claude and Gemini role probes
reserve a durable request and make at most two sequential synthetic calls:
student text/stream, mentor judgment JSON/coaching text, or analysis
v2 unified JSON/evidence-subset merge JSON. Each stops on the first failure with no retries.
Strict structured transport is followed by server type and semantic validation;
invalid JSON, references, refusals, output limits and empty results fail the role.
Reopening the page reads
the existing result; cancellation closes the local HTTP request but cannot undo
provider processing or incurred charges. Probe and catalog attempts are recorded
before calling upstream; unknown tokens and unpriced costs stay NULL. Common admission limits catalog and probe calls together, preserving one slot
for ordinary calls globally and per provider. Capacity refusals return 429 without
a queue. Each administrator can run one probe bundle. Disable/delete immediately
block admission and request cancellation of every active credential revision.
Limits and absolute deadlines are read for new calls; reducing limits preserves
existing calls. Ordinary classification/synthesis and analysis_unified/analysis_merge calls may retry once;
backoff returns capacity and reacquires it under the current limits. This service
requires one asynchronous worker in one app instance. Student lesson calls now
require an enabled, student-verified DB registration
of the exact existing OpenAI model ID. They preserve scenario/config model IDs,
per-call reasoning/output options, prompts, completed-turn context and stored
greetings; authoring defaults never replace existing selections. Missing keys,
unreadable credentials or unverified models block new turns with a safe setup
message while saved history remains readable. Both stream and nonstream student
calls use common admission, deadlines (default first body 60/total 180 seconds),
zero retries and one attempt-ledger row per call. Mentor coaching and its separate
semantic judgment also use DB credentials, exact mentor-verified model IDs and
shared admission with zero retries. Their existing sensitivity, counters and local
fallback remain unchanged; local no-intervention decisions make no provider call.
Judgment attempts are recorded separately as `mentor_judgment`, including failures
without usage. Post-session analysis also uses DB registrations and common
invocations while preserving configured model IDs and options. One structured
v2 call uses the frozen lesson and completed teacher/student dialogue; the server
validates exact evidence and computes statistics and rubric grades. No dialogue
makes no provider call. Existing v1 reports remain readable; analysis models need
explicit s4-v2 role revalidation, while student and mentor evidence stays current.
Analysis requests send a UUID `request_id` and receive 202 with a durable run ID
and status/cancel paths. Replaying that request makes no new call; changed inputs
or another active request return 409. Accepted reports and the latest execution
status are separate. Leaving the page keeps the execution; explicit cancellation,
the 900-second run cap, permission revocation or failed storage prevent adoption.
Startup marks unfinished runs interrupted without rerunning them. Migration 034
preserves existing records and adds analysis execution constraints; the supported
single worker owns the page-independent tasks.
Do not treat this stage as the final production cutover.

Encryption uses the exactly pinned [cryptography 50.0.2](https://pypi.org/project/cryptography/50.0.2/)
[AES-GCM API](https://cryptography.io/en/stable/hazmat/primitives/aead/), a fresh
12-byte nonce per encryption, and authenticated provider/connection ID/credential revision.

`CONTEXT_WINDOW_TURNS` defaults to 10 completed teacher–student pairs.
Student input includes those prior pairs and the current question once; mentor
input includes the prior pairs and its target pair, excluding later turns.
On upgrading to Phase A, review any explicitly configured value: it previously
counted individual messages (including mentor rows), and now counts complete
pairs. Existing values are preserved, so administrators should adjust them
before starting new sessions. Failed attempts and greetings consume no slots.

Student stream/nonstream and single structured mentor calls fit the frozen prior-turn limit into a local
input budget by dropping the oldest complete pairs. The role instruction,
problem, current question or completed mentor target pair, intervention condition,
provider-facing output schema and frozen output options are preserved. Mentor
input contains at most N prior pairs plus the target pair (N+1 pairs), excluding
later turns and other mentor messages. Required input that still exceeds the
budget returns HTTP 422 `context_limit` before a new run or provider attempt is
stored and consumes no mentor interval, rolling capacity or counter; shorten the question or ask an
administrator to review the configuration. Provider context failures are recorded
as failed attempts and are never automatically retried.

Migration 033 adds nullable `api_usage_log.context_budget_json`, separate from
actual tokens and costs. Capacity definition v2 makes old student, mentor and
analysis evidence stale; administrators must explicitly reverify each required
role. Saved lesson selections and snapshot hashes are preserved.

Tests need no API credentials or network. They override configuration before
imports, reject outbound sockets, and create databases only under pytest's
temporary directory. The repository's dialogue_sim.db and .env are never used:

```sh
uv run --frozen python -m pytest -q
```

Lint and format: `uv run --frozen ruff check .` and `uv run --frozen black --check .`.
Browser setup (Node 24): `npm ci` and `npx playwright install --with-deps chromium`.
Browser checks: `npm run test:browser` (optional filename filters: `npm run test:browser -- student_stream`).
Live streaming check (localhost, no paid calls): `uv run --frozen python tests/check_student_live.py`.

S2-02 draft authoring (#59): open `/admin/scenarios/new` as an administrator.
The existing create/update URLs store typed v1 drafts, and `/admin/scenarios/{id}`
returns the saved administrator envelope; `/admin/scenarios/{id}/edit` reopens it.
Drafts preserve hidden mentor/rubric values and unavailable model selections;
updates and deletions require `expected_version`. Drafts cannot start lessons.
`npm run test:browser -- s2_draft_live` rehearses the real form, login, CSRF,
save and reopen against migrated temporary SQLite, with external sockets blocked.
S2-03 publication (#60) uses the same form and save URLs with `action=publish`.
Active roles require current S1 authorization and supported saved options;
publication never probes a model or merges its current defaults. Converted
drafts keep server-managed review reasons: repair blocking fields, save the
draft, then acknowledge the remaining warnings for that exact revision.
Failed publication and stale writes preserve the stored revision and browser
input. Every successful edit, activation, group change or deletion advances the
revision once. Migration 029 preserves legacy rows and adds snapshot storage
only. S2-05 (#62) connects both lesson-start paths to current publication,
assignment and role-model checks, atomically storing a native configuration
snapshot and canonical hash. Reopening a lesson preserves its public problem,
student introduction and title; current activation and assignment still apply.
Missing/corrupt native snapshots fail closed. Unconverted scenarios cannot start
new lessons. S2-06 (#63) executes student streaming, nonstreaming and explicit
failed-turn retries from that snapshot's literal student instructions, exact
provider/model/options and completed-turn context limit. Runs use the session's
configuration hash. Current access and S1 role authorization still gate calls;
model defaults never replace saved options. S2-07 (#64) executes mentor help from
the same snapshot: off blocks requests and welcome, manual sends one coaching
call, and auto checks the authored condition before optional coaching. Both auto
subcalls share the saved provider/model/options and the first overall deadline.
Automatic admission uses completed-pair start/interval positions and a rolling
coaching cap; negative checks and failures remain replayable without preventing
explicit manual help. Migration 030 adjusts run uniqueness and records explicit
manual/auto triggers. The teacher help button and automatic events follow snapshot
mode; slow or failed coaching leaves the next student turn available.
S2-08 (#65) also executes native
post-session analysis from frozen evaluation text and rubric IDs using the same
saved provider/model/options for every subcall and the full teacher–student
dialogue. Classification off skips greeting/classification and keeps narrative
feedback; reports and CSV explicitly show its disabled state. CSV appends a
`classification_status` column and maps native IDs to frozen display names.
This is not a production cutover.

S2-09 (#66) extends the same explicit-copy conversion command below to store
`legacy_reconstructed` session candidates, reconstruction time, a null source
revision and unknown historical fields. These candidates and their hashes do
not prove the model, instructions or rubric used at the original start time.
Original dialogue, results, usage and timestamps remain unchanged. Legacy and
unconverted sessions are read-only: resume, new messages, mentor requests,
ending and analysis writes return `409 legacy_read_only`. Start a separate
native session from a published scenario for new practice. Owners can read
`/sessions/{id}`, existing reports and CSV; administrators use the session
detail/results screens. History and CSV use frozen native or explicitly
reconstructed display data, with no current-framework lookup. CSV appends the
public student name and provenance columns; it never exports internal criteria
or raw configuration. Unconverted display metadata remains unknown until the
explicit conversion. The browser rehearsal includes mixed native/legacy
history on desktop and mobile.

S2-10 (#67) administration uses only `/admin/scenarios/new` and the unified editor.
Template/framework management routes and legacy scenario writes are retired;
retired video and template fields are rejected by the native API. The seed installs
a template-free draft with public problem text. Select verified student and
analysis models in the editor before publishing; no model is selected implicitly.
The scenario list shows publication/activation/review state and grouped session
counts. AI connection impact includes saved scenario roles and distinguishes
mentor off and drafts/inactive scenarios from new-lesson execution references.
Legacy tables/columns remain private conversion inputs until the verified S2
cutover; they do not supply runtime settings or public video resources.

S2-04 legacy review (#61) runs only on an explicitly selected consistent DB copy
with migration 029 already applied. Capture actual deployment inputs in a private
JSON file; `tests/fixtures/s2_legacy_effective.json` documents its shape with
synthetic values, not deployment defaults. Supply the old effective BASE text,
student options (the scenario's exact chat_model override still applies), mentor
coaching/judgment and analysis synthesis/greeting/classification model/options,
threshold N and context turn limit. Unknown inputs are explicit nulls; no keys,
passwords or master keys are accepted. No environment model defaults are read.

```sh
uv run --frozen python -m src.db.convert_scenarios \
  --database-copy /private/s2-copy.sqlite3 --settings /private/effective.json \
  --archive /private/s2-source.json --manifest /private/s2-manifest.json
```

The default is a deterministic dry run. It creates private (0600) source and
manifest files without writing the DB; existing files must match. Keep the same
artifacts and append `--apply` to convert the selected copy to review drafts.
Each row reports converted/skipped/conflict; conflicts cause a nonzero exit and
never overwrite native edits. Original IDs, groups, activation, deletion and
historical sessions/results stay intact. Video originals exist only in the
private archive, never in conversion provenance or the form. Oversized or
damaged sources remain archived with blocking reasons and bounded drafts.
The administrator editor shows source/target evidence, archive references and
hashes. Repair blocking fields, save, acknowledge the new revision's warnings,
then publish. The live browser rehearsal includes this actual conversion flow.
This command does not back up the operating DB, perform an operating cutover
or call a paid provider. Its reconstructed session candidates cannot establish
the settings used at the original lesson start.

S2-01 screen fixtures (#58): `npm run test:browser -- s2_` exercises the five-step
editor, public preview, model options, synthetic save errors/conflicts, conversion
review. To inspect the screens, run
`uv run --frozen python tests/browser_server.py` and open
`http://127.0.0.1:8765/fixtures/s2/editor` (also `?new=1`, `?published=1`,
`?legacy=blocked` or `?legacy=review`).
The isolated server never writes a database or calls a provider; save
responses are intercepted only by browser tests. These fixture routes and query
switches do not exist in the application. Production uses the native routes
described above.

The original GitHub commit remains an ancestor. The application baseline is the
commit titled `chore: preserve local application baseline for issue #1`; it
contains the original product code, without credentials, databases or caches.
See [refactoring checks](docs/refactoring.md) for subsequent verification.

S2-11 (#68) preparation: see [the S2 cutover runbook](docs/s2-cutover.md).
`python -m src.db.s2_cutover --source-copy COPY --workspace PRIVATE_DIR
--settings EFFECTIVE_JSON` defaults to dry-run; `--apply` converts, verifies,
restores and contracts only the rehearsal copy. Populated installations require
validated preservation evidence before final migration 031. Existing 030 mentor
policy SQL stays unchanged. No operating deployment is performed by this tool.

S2-12 (#69) offline integration: `uv run --frozen python -m pytest -q
tests/test_s2_integration.py` exercises final-schema fresh seed and verified
legacy-copy conversion/restore, real login/CSRF, draft repair/review/publication,
both lesson starts, frozen student/mentor/analysis execution and CSV with all
three pinned SDKs over mocked HTTP. It includes concurrent version conflicts,
durable replay, failed reanalysis preservation and private-role separation.
Run the full pytest, lint/format, browser and localhost streaming checks above
before integration review. Mock success verifies the execution contract;
real model educational quality, provider latency and operating deployment
remain separate work.

S3-05 (#76) offline integration: `uv run --frozen python -m pytest -q
tests/test_s3_integration.py tests/test_s3_migration.py tests/test_s3_load.py`
reuses the S2 installation/SDK fixtures and S1 load checks for explicit role
probes, student-only execution, single mentor events, zero-call refusals,
S2-final WAL backup/restore and additive 032/033 preservation. Injected provider
delay/failure verifies student independence and slot cleanup, without claiming
real p95 or educational quality. [S3 cutover and handoff](docs/s3-cutover.md)
documents drain, backup/restore, stale roles, explicit reverification and the
rollback boundary after new writes. Actual comparison panels remain S5 work.

S4-08 (#85) quality preparation: [the fixed corpus and release gate](docs/s4-quality-gate.md)
provides 12 synthetic Korean dialogues, agent-draft expected evidence and a
comparison record template. `uv run --frozen python -m pytest -q
tests/test_s4_quality_corpus.py tests/test_s4_quality_comparison.py` rehearses
the existing analysis execution with mocked SDK HTTP only. S4 candidate analysis
and actual educational approval remain separate work; release stays blocked
until the education lead approves actual comparisons for every intended pilot
model/config after separately authorized paid execution.
