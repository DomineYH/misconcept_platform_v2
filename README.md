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

This is the A8 management and A12 student transition stage. Explicit OpenAI
and Claude catalog refresh uses the saved
DB key and the non-generating Models API, with no retries and a default 30-second
deadline. Successful lists are cached for 24 hours; failures preserve the list.
Models can be registered directly, start disabled/unverified, and have immutable
IDs. The initial authoring defaults are empty. Model options and singleton call
limits/timeouts are validated on save. See the
[screen/data contract](docs/agents/ai-connections-screen.md) for capability sources
and the APIs later tickets must reuse.

Saving keys or models does not call a provider. Explicit OpenAI and Claude role probes
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
existing calls. Only future ordinary classification/synthesis calls may retry once;
backoff returns capacity and reacquires it under the current limits. This service
requires one asynchronous worker in one app instance. Student lesson calls now
require an enabled, student-verified DB registration
of the exact existing OpenAI model ID. They preserve scenario/config model IDs,
per-call reasoning/output options, prompts, completed-turn context and stored
greetings; authoring defaults never replace existing selections. Missing keys,
unreadable credentials or unverified models block new turns with a safe setup
message while saved history remains readable. Both stream and nonstream student
calls use common admission, deadlines (default first body 60/total 180 seconds),
zero retries and one attempt-ledger row per call. Mentor and analysis lesson
calls continue using their previous configuration until A13/A14.
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

The original GitHub commit remains an ancestor. The application baseline is the
commit titled `chore: preserve local application baseline for issue #1`; it
contains the original product code, without credentials, databases or caches.
See [refactoring checks](docs/refactoring.md) for subsequent verification.
