# D2-01 keyless schema, model mode and projection evidence (#195, G2)

Contract: `d0-03-v1-r3` (G0 approved 2026-10-19). The wire and manifest
`contract_version` stays `d0-03-v1-r3`. This page is G2 evidence only. It does
not mark G3 (real storage, zero external calls, final browser run) or G4 done.
Local role evidence means "output format checked", not a real provider test or
an educational quality certification.

## Delivery

| PR | Branch / head | Scope |
| --- | --- | --- |
| #223 (merged, main `3b967f19be0cdbc361877addbf46ec9f7582b889`) | `feat/195-migration`, `e116df7` | migration 037, `demo_metadata`, ORM, guarded installer |
| #224 (open) | `feat/195-mode`, `ba539225e952cc6c3c65e23b2ec37b8cff9d3b4a` (review repair on `f4afe15`) | explicit `app_mode`, demo model/options/limits, role evidence, caller pass-through |
| PR3 | `feat/195-projection` (on top of #224) | §16.1 helper, this page |

## Migration handoff for #197 (§12, §17)

- `db_schema_version`: `037_demo_provider.sql`. This is the last `_migrations`
  filename after a fresh install.
- `src/db/migrations/037_demo_provider.sql` SHA-256:
  `cc16dfd90cbc2d71eedb919daa4920da050f433839bf2a5a0255ac0f4f1eacf9`
- `migration_set_sha256`:
  `d245be48d9b05c13264a43b146343388d85d81a43ad199490054ee45f79beec1`
  (computed from main `3b967f1`; unchanged on #224 and PR3, which add no
  migration).

Recipe (owner: #197, `migration_set_sha256()` in
`tests/test_demo_d2_03_fixtures.py`; coordinator-confirmed canonical). The set
is exactly what `run_all_migrations()` installs on a fresh database:
`src/db/migrations/baseline.sql` plus every `src/db/migrations/[0-9]*.sql` with
revision > 23 that does not end in `_down.sql`. Sort by repo-relative POSIX
path; for each file feed `path + NUL + file bytes + NUL` into one SHA-256:

```python
import hashlib, subprocess
rev, root = "3b967f1", "src/db/migrations/"
names = subprocess.run(["git", "ls-tree", "--name-only", rev, root],
                       check=True, capture_output=True, text=True).stdout.split()
def applied(n):
    base = n.rsplit("/", 1)[1]
    return base == "baseline.sql" or (base[:1].isdigit() and base.endswith(".sql")
        and int(base.split("_")[0]) > 23 and not base.endswith("_down.sql"))
digest = hashlib.sha256()
for n in sorted(filter(applied, names)):
    data = subprocess.run(["git", "show", f"{rev}:{n}"],
                          check=True, capture_output=True).stdout
    digest.update(n.encode() + b"\0" + data + b"\0")
print(digest.hexdigest())
```

The set has 15 files: 024–037 and `baseline.sql`. A change to any of their
bytes, or a new migration, changes the hash. The manifest must then be
regenerated; it is never patched by hand.

## Interfaces for #196/#197/#198

- §2: `connection_ready`, `effective_roles`, `model_available`,
  `validate_model_and_options`, `public_model`, `configuration_state` and
  `resolve_frozen_model` take a required keyword-only
  `app_mode: Literal["normal","demo"]`. Normal rejects `demo` rows. Demo
  rejects external providers and never calls `decrypt_key` for the keyless
  connection.
- §13: `src/services/model_capabilities.py` exposes `DEMO_MODEL_ID`
  (`demo-classroom-v1`) and `DEMO_MODEL_TITLE` (`준비된 수업 예시 v1`).
  Capability `demo-cap-v1`: text, stream and structured true; tools, image and
  audio false; input 65536 (estimate); output 8192. Options are exactly
  `{"max_output_tokens": int 4096..8192}`. `ResolvedModelConfig` stores 4096
  when the value is omitted at authoring.
- §13 evidence tuple: `RoleVerification` status `succeeded`, `verified_at`
  `2026-10-18T00:00:00Z`, revision 0, version 1, `demo-cap-v1`, roles
  `s1-v1`/`s3-v1`/`s4-v2`, no `probe_request_id`. A mismatch gives `stale`
  with `error_code: VERSION_MISMATCH`; re-enabling the connection does not
  repair it.
- §8: `CallLimits.demo` is a strict int ≥ 2 and reads as 2 when absent.
- §16.1: `src/services/model_configuration.py` provides
  `supported_config_projection(config_schema_version, config)` and
  `supported_config_sha256(config_schema_version, config)`. They validate with
  `ScenarioConfig` and its defaults first, project only the contract fields,
  never trim or normalize, and raise `ValueError("INVALID_REQUEST")` for a bad
  version, type, null, extra field or invalid UTF-8. Consumers import these
  helpers and never copy them. Excluded PKs and options still need their own
  validation. The unit vector digest is
  `84c3988b5ab0c5ad9aa47e1c7c66edb755909e8b65a820eac9456bcf56dfaadb`. It is
  not the product D0-02 hash, which #197 computes from the real seed config.

## Checks

All pytest runs were serial with one worker, on Python 3.12.3 and frozen
`uv.lock`. Logs are under `_coord/reports/logs-w195/`.

| PR | Tested revision | Command / scope | Result |
| --- | --- | --- | --- |
| #223 | `dcd7c5e`, then integration `e116df7` | schema/migration/startup/D1/S4/comparison-migration targeted and affected; full `pytest -q` | affected 521 passed; full 2640 passed (local, e116df7); CI on PR |
| #224 | `f4afe15`; repair `ba53922` | f4afe15: `tests/test_demo_d2_01_mode.py` + schema/migration/provider (105 passed); model/options/connections/limits/admission/snapshots/drafts/publication (235 passed); 85 files importing edited callers (all passed after rerunning 6 files that hit `/tmp` ENOSPC, 87/87). ba53922: mode tests + the same model/admission suites (295 passed) | full pytest, browser and live: **CI on PR head** |
| PR3 | see worker report | `tests/test_demo_d2_01_projection.py` + `tests/test_demo_d2_01_*.py`, `tests/test_scenario_*.py`, `tests/test_model_management.py`, `tests/test_lesson_snapshots.py` | full regression: **CI on PR** |

`ruff check .` and `black --check .` pass on each head. `conftest` uses
`TESTING=true` and ORM `create_all`, so the migration preservation evidence
comes only from the official runner on temporary databases
(`tests/test_demo_d2_01_schema.py`). It is not evidence of a non-test startup.

Not run locally: full `npm run test:browser` and `tests/check_student_live.py`
for #224 and PR3 (Addendum H1: CI). No real provider, network, production DB or
secret was used.
