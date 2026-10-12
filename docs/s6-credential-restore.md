# S6 offline credential restoration (#104)

Database preservation and credential restoration are separate gates. A valid
SQLite restore does not prove that stored provider credentials can be decrypted.
Login and historical readers remain available with an unavailable master key;
new AI calls must still refuse admission. This tool never calls providers,
replaces keys, re-encrypts credentials, or approves release.

Use a git checkout with the git executable available (the S6 tools bind evidence
to the checked-out revision). Stop all writers and select an isolated restored
copy explicitly, such as `restore-candidate.db` or `restore-source.db` from the
[S6 rehearsal](s6-cutover.md). Keep the original sealed workspace unchanged.
Files must be private (0600), without symlinks or hardlinks. Create a separate
private directory (0700) for credential evidence.
An input with a nonempty WAL is refused: obtain a separate consistent restored
copy through the SQLite backup API, without deleting or discarding WAL pages.

Supply the matching `PROVIDER_SECRET_ENCRYPTION_KEY` and
`PROVIDER_SECRET_ENCRYPTION_KEY_VERSION` through the existing infrastructure
environment, from the separately approved secret store. Never put key material
in command arguments, reports, manifests, archives, repository files or logs.
The standalone CLI suppresses dotenv loading before importing application
configuration; it uses `TESTING=true` only within its own offline process for
that purpose. It does not boot an application or select `DATABASE_URL`.

```sh
uv run --frozen python -m src.db.s6_credentials \
  --restored-db /private/maintenance/s6-rehearsal/restore-candidate.db \
  --report /private/maintenance/credential-evidence/candidate-v1.json
```

The immutable private JSON report and stdout contain only fingerprints, key
versions, target counts and verification results. All encrypted connections,
including disabled ones, are checked through the existing AES-GCM boundary.
Provider, connection ID, credential revision, nonce and ciphertext stay unchanged.
Plaintext is discarded rather than exported; Python memory clearing is not
guaranteed. The report records `database_integrity` independently of
`credential_restore`:

- `passed`: every stored credential was decrypted with the configured version.
- `failed`: at least one matching-version credential failed (missing/invalid/wrong
  master key or damaged authenticated material). Exit 2.
- `incomplete`: another version remains unverified. Exit 2, even if the selected
  version passed. A version mismatch never counts as success.
- `not_applicable`: the database contains no credentials. Exit 0 without a key.

Missing/unsafe input, invalid database, unsafe output or changed existing evidence
returns exit 2 with a generic safe error, without raw exception text. Use separate
report filenames for separate versions or changed configuration; never edit an
earlier report to make a failed verification pass.

For multiple versions, run a separate isolated process with each matching key
and version. There is no key ring. Each run retains the full required-version
inventory and explicitly marks other versions unverified. Operators must check
that, across reports for the **same restored DB and code/tool fingerprints**,
every required version has `verified == targets`, `failed == 0` and
`unverified == 0` in its own run. No individual incomplete report proves complete
credential recovery.

Attach these reports to the S6 acceptance record separately from its sealed
`report.json`/`receipt.json`. Match `restored_db_hash` against the corresponding
`restore-source.db` or `restore-candidate.db` SHA-256 in `receipt.json`, check
the unchanged DB again after verification, and retain the code/tool fingerprints.
Repeat for both restored versions where credentials exist. Database preservation
evidence stays valid even when credential verification fails, but AI recovery and
release remain blocked. Do not resume writers before all required gates match.

Automated tests use synthetic temporary databases and synthetic keys only.
Actual operational key restoration requires a separately approved human rehearsal;
these tests do not substitute for it, deployment approval, current role evidence,
health/login smoke, reader/ACL/CSV checks or educational quality approval.
