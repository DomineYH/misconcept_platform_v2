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
classification JSON/synthesis JSON. Each stops on the first failure with no retries.
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
existing calls. Ordinary classification/synthesis calls may retry once;
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
invocations while preserving its configured OpenAI model IDs and existing
normalization. Greeting detection has no retries.
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
model defaults never replace saved options. Mentor/analysis execution wiring
remains for following S2 tickets; this is not a production cutover.

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
This command does not back up the operating DB, reconstruct past sessions,
perform an operating cutover or call a paid provider.

S2-01 screen fixtures (#58): `npm run test:browser -- s2_` exercises the five-step
editor, public preview, model options, synthetic save errors/conflicts, conversion
review and teacher help. To inspect the screens, run
`uv run --frozen python tests/browser_server.py` and open
`http://127.0.0.1:8765/fixtures/s2/editor` (also `?new=1`, `?published=1`,
`?legacy=blocked` or `?legacy=review`) or `/fixtures/s2/lesson?mode=off|manual|auto`.
Teacher fixtures also accept `help=running|failed|rate_limited|limit`,
`classification=off` and `legacy=1`. Use one value per parameter.
The isolated server never writes a database or calls a provider; save/help
responses are intercepted only by browser tests. These fixture routes and query
switches do not exist in the application. Runtime wiring follows in #59–#66.

The original GitHub commit remains an ancestor. The application baseline is the
commit titled `chore: preserve local application baseline for issue #1`; it
contains the original product code, without credentials, databases or caches.
See [refactoring checks](docs/refactoring.md) for subsequent verification.
