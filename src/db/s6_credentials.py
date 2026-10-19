"""Verify restored credentials offline, one environment-supplied key version."""

# ruff: noqa: E402
import json
import os

# CLI preflight precedes imports that can initialize configuration or engines.
if __name__ == "__main__" and os.environ.get("APP_MODE", "normal") != "normal":
    mode = os.environ.get("APP_MODE")
    code = (
        "DEMO_ACTION_FORBIDDEN"
        if mode == "demo"
        else "DEMO_CONFIGURATION_INVALID"
    )
    print(json.dumps(dict(code=code, exit_status=1)))
    raise SystemExit(1)


import argparse
from pathlib import Path
from types import SimpleNamespace


def verify_credentials(restored_db):
    from src.config import config
    from src.db.s2_verification import ensure_integrity, readonly
    from src.db.s6_cutover import code_identity, digest
    from src.db.s6_verification import private_file
    from src.services.lesson_snapshots import canonical_hash
    from src.services.provider_secrets import (
        ProviderSecretUnavailableError,
        decrypt_key,
    )

    restored_db = Path(restored_db)
    if restored_db.name in {"dialogue_sim.db", ".env"} or any(
        part.is_symlink()
        for part in (restored_db.absolute(), *restored_db.absolute().parents)
    ):
        raise ValueError("isolated_restore_required")
    private_file(restored_db)
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(restored_db) + suffix)
        if sidecar.exists() or sidecar.is_symlink():
            private_file(sidecar)
            if suffix == "-wal" and sidecar.stat().st_size:
                raise ValueError("consistent_restored_copy_required")
    with readonly(restored_db) as db:
        ensure_integrity(db)
        rows = []
        if db.execute(
            "SELECT 1 FROM sqlite_master WHERE name='provider_connection'"
        ).fetchone():
            rows = db.execute(
                "SELECT provider,id,credential_revision,encrypted_key,nonce,"
                "encryption_key_version FROM provider_connection "
                "WHERE encrypted_key IS NOT NULL ORDER BY id"
            ).fetchall()
        versions = {}
        for (
            provider,
            connection_id,
            revision,
            ciphertext,
            nonce,
            version,
        ) in rows:
            counts = versions.setdefault(
                version,
                dict(
                    version=version,
                    targets=0,
                    verified=0,
                    failed=0,
                    unverified=0,
                ),
            )
            counts["targets"] += 1
            if version != config.PROVIDER_SECRET_ENCRYPTION_KEY_VERSION:
                counts["unverified"] += 1
                continue
            try:
                decrypt_key(
                    SimpleNamespace(
                        provider=provider,
                        id=connection_id,
                        credential_revision=revision,
                        encrypted_key=ciphertext,
                        nonce=nonce,
                        encryption_key_version=version,
                    )
                )
            except ProviderSecretUnavailableError:
                counts["failed"] += 1
            else:
                counts["verified"] += 1
    if not rows:
        status = "not_applicable"
    elif any(counts["failed"] for counts in versions.values()):
        status = "failed"
    elif any(counts["unverified"] for counts in versions.values()):
        status = "incomplete"
    else:
        status = "passed"
    return dict(
        **code_identity(),
        credential_tool_hash=canonical_hash(
            {
                name: digest(Path(__file__).resolve().parents[1] / name)
                for name in (
                    "db/s6_credentials.py",
                    "services/provider_secrets.py",
                    "config.py",
                )
            }
        ),
        release_blocked=True,
        restored_db_hash=digest(restored_db),
        database_integrity="passed",
        credential_restore=status,
        versions=[versions[version] for version in sorted(versions)],
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--restored-db", type=Path, required=True)
    parser.add_argument("--report", type=Path, required=True)
    args = parser.parse_args()
    # Offline CLI reads infrastructure environment only, never a dotenv file.
    os.environ["TESTING"] = "true"
    try:
        from src.db.convert_scenarios import private_artifact
        from src.db.s6_verification import private_file

        if args.report.name in {"dialogue_sim.db", ".env"} or (
            any(
                part.is_symlink()
                for part in (
                    args.report.absolute(),
                    *args.report.absolute().parents,
                )
            )
            or args.report.parent.stat().st_mode & 0o077
        ):
            raise ValueError("private_report_required")
        if args.report.exists():
            private_file(args.report)
        report = verify_credentials(args.restored_db)
        private_artifact(args.report, report)
    except (Exception, KeyboardInterrupt):
        print(
            json.dumps(
                dict(
                    release_blocked=True,
                    credential_restore="failed",
                    error_code="invalid_input_or_evidence",
                )
            )
        )
        parser.exit(2)
    print(json.dumps(report))
    if report["credential_restore"] not in {"passed", "not_applicable"}:
        parser.exit(2)


if __name__ == "__main__":
    main()
