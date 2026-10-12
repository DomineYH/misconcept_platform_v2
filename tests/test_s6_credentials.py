"""Offline credential evidence uses restored synthetic databases only."""

import base64
import json
import os
import sqlite3
import subprocess
import sys
from contextlib import closing
from pathlib import Path
from types import SimpleNamespace

import pytest
from pydantic import SecretStr

from src.config import config
from src.db.s2_verification import consistent_backup
from src.db.s6_verification import upgrade
from src.services.provider_secrets import encrypt_key

KEY = base64.b64encode(b"a" * 32).decode()
PLAINTEXT = "SYNTHETIC-CREDENTIAL-SENTINEL"


@pytest.fixture
async def restored(tmp_path, monkeypatch):
    monkeypatch.setattr(
        config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr(KEY)
    )
    monkeypatch.setattr(config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "v1")
    source = tmp_path / "synthetic.db"
    await upgrade(source)
    with closing(sqlite3.connect(source)) as db, db:
        for connection_id, provider in ((1, "openai"), (2, "anthropic")):
            ciphertext, nonce = encrypt_key(
                SimpleNamespace(id=connection_id, provider=provider),
                PLAINTEXT,
                7,
            )
            db.execute(
                "UPDATE provider_connection SET encrypted_key=?,nonce=?,"
                "encryption_key_version='v1',credential_revision=7,"
                "masked_hint='masked',enabled=? WHERE id=?",
                (ciphertext, nonce, connection_id == 1, connection_id),
            )
        db.commit()
    destination = tmp_path / "restored.db"
    consistent_backup(source, destination)
    return destination


async def test_matching_key_checks_all_credentials_and_preserves_restored_bytes(
    restored,
):
    from src.db.s6_credentials import verify_credentials

    before = restored.read_bytes()
    result = verify_credentials(restored)
    assert result["database_integrity"] == "passed"
    assert result["credential_restore"] == "passed"
    assert result["versions"] == [
        dict(version="v1", targets=2, verified=2, failed=0, unverified=0)
    ]
    assert result["release_blocked"] is True
    assert restored.read_bytes() == before
    assert KEY not in str(result) and PLAINTEXT not in str(result)


async def test_each_version_needs_its_own_evidence(restored, monkeypatch):
    from src.db.s6_credentials import verify_credentials

    monkeypatch.setattr(config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "v2")
    mismatch = verify_credentials(restored)
    assert mismatch["credential_restore"] == "incomplete"
    assert mismatch["versions"] == [
        dict(version="v1", targets=2, verified=0, failed=0, unverified=2)
    ]
    second_key = base64.b64encode(b"b" * 32).decode()
    monkeypatch.setattr(
        config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr(second_key)
    )
    ciphertext, nonce = encrypt_key(
        SimpleNamespace(id=2, provider="anthropic"), PLAINTEXT, 7
    )
    with closing(sqlite3.connect(restored)) as db, db:
        db.execute(
            "UPDATE provider_connection SET encrypted_key=?,nonce=?,"
            "encryption_key_version='v2' WHERE id=2",
            (ciphertext, nonce),
        )
    before = restored.read_bytes()
    for version in ("v1", "v2"):
        monkeypatch.setattr(
            config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", version
        )
        monkeypatch.setattr(
            config,
            "PROVIDER_SECRET_ENCRYPTION_KEY",
            SecretStr(KEY if version == "v1" else second_key),
        )
        result = verify_credentials(restored)
        assert result["credential_restore"] == "incomplete"
        assert result["versions"] == [
            dict(
                version=v,
                targets=1,
                verified=int(v == version),
                failed=0,
                unverified=int(v != version),
            )
            for v in ("v1", "v2")
        ]
        assert restored.read_bytes() == before


async def test_database_without_credentials_is_not_applicable(
    restored, monkeypatch
):
    from src.db.s6_credentials import verify_credentials

    with closing(sqlite3.connect(restored)) as db, db:
        db.execute(
            "UPDATE provider_connection SET encrypted_key=NULL,nonce=NULL,"
            "encryption_key_version=NULL,masked_hint=NULL,enabled=0"
        )
    monkeypatch.setattr(config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr(""))
    result = verify_credentials(restored)
    assert result["database_integrity"] == "passed"
    assert result["credential_restore"] == "not_applicable"
    assert result["versions"] == []


@pytest.mark.parametrize(
    "damage", ["missing", "invalid", "wrong", "ciphertext"]
)
async def test_credential_failure_is_separate_from_database_integrity(
    restored, monkeypatch, damage
):
    from src.db.s6_credentials import verify_credentials

    if damage == "ciphertext":
        with closing(sqlite3.connect(restored)) as db, db:
            db.execute(
                "UPDATE provider_connection SET encrypted_key=? WHERE id=1",
                (b"x" * 32,),
            )
    else:
        value = {
            "missing": "",
            "invalid": "invalid-base64",
            "wrong": base64.b64encode(b"b" * 32).decode(),
        }[damage]
        monkeypatch.setattr(
            config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr(value)
        )
    before = restored.read_bytes()
    result = verify_credentials(restored)
    failed = 1 if damage == "ciphertext" else 2
    assert result["database_integrity"] == "passed"
    assert result["credential_restore"] == "failed"
    assert result["versions"] == [
        dict(
            version="v1",
            targets=2,
            verified=2 - failed,
            failed=failed,
            unverified=0,
        )
    ]
    assert restored.read_bytes() == before
    assert KEY not in str(result) and PLAINTEXT not in str(result)


async def test_credential_evidence_matches_sealed_s6_restore(
    restored, tmp_path
):
    from src.db.s6_credentials import verify_credentials
    from src.db.s6_cutover import rehearse

    workspace = tmp_path / "s6"
    report = await rehearse(restored, workspace, apply=True)
    sealed = {p.name: p.read_bytes() for p in workspace.iterdir()}
    receipt = json.loads((workspace / "receipt.json").read_text())
    for name in ("restore-source.db", "restore-candidate.db"):
        result = verify_credentials(workspace / name)
        assert result["restored_db_hash"] == receipt["artifacts"][name]
        assert result["code_revision"] == report["code_revision"]
        assert result["credential_restore"] == "passed"
    assert {name: (workspace / name).read_bytes() for name in sealed} == sealed


@pytest.mark.parametrize(
    "unsafe",
    ["default", "dotenv", "symlink", "hardlink", "public", "missing", "wal"],
)
async def test_unsafe_restore_input_is_refused_without_changes(
    restored, tmp_path, unsafe
):
    from src.db.s6_credentials import verify_credentials

    selected = restored
    writer = None
    if unsafe in {"default", "dotenv"}:
        selected = tmp_path / (
            "dialogue_sim.db" if unsafe == "default" else ".env"
        )
        restored.rename(selected)
    elif unsafe == "symlink":
        selected = tmp_path / "link.db"
        selected.symlink_to(restored)
    elif unsafe == "hardlink":
        selected = tmp_path / "link.db"
        os.link(restored, selected)
    elif unsafe == "public":
        restored.chmod(0o644)
    elif unsafe == "missing":
        selected = tmp_path / "absent.db"
    elif unsafe == "wal":
        writer = sqlite3.connect(restored)
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute("UPDATE provider_connection SET credential_revision=8")
        writer.commit()
    files = {p: p.read_bytes() for p in tmp_path.iterdir() if p.is_file()}
    try:
        with pytest.raises((ValueError, OSError)):
            verify_credentials(selected)
        assert {p: p.read_bytes() for p in files} == files
        assert selected.exists() == (unsafe != "missing")
    finally:
        if writer:
            writer.close()


async def test_cli_uses_environment_only_and_writes_safe_private_evidence(
    restored, tmp_path
):
    root = Path(__file__).resolve().parents[1]
    # A synthetic dotenv key must never rescue a missing environment key.
    (tmp_path / ".env").write_text(
        f"PROVIDER_SECRET_ENCRYPTION_KEY={KEY}\nPROVIDER_SECRET_ENCRYPTION_KEY_VERSION=v1\n"
    )
    evidence = tmp_path / "evidence"
    evidence.mkdir(mode=0o700)
    before = restored.read_bytes()
    with closing(sqlite3.connect(restored)) as db, db:
        material = db.execute(
            "SELECT encrypted_key,nonce FROM provider_connection WHERE encrypted_key IS NOT NULL"
        ).fetchall()
    for name, key, exit_code, status in (
        ("matching", KEY, 0, "passed"),
        ("missing", "", 2, "failed"),
    ):
        report = evidence / f"{name}.json"
        command = [
            sys.executable,
            "-c",
            "import socket,runpy; socket.socket.connect=lambda *a,**k: (_ for _ in ()).throw(AssertionError('network forbidden')); runpy.run_module('src.db.s6_credentials',run_name='__main__')",
            "--restored-db",
            str(restored),
            "--report",
            str(report),
        ]
        result = subprocess.run(
            command,
            cwd=tmp_path,
            env={
                **os.environ,
                "PYTHONPATH": str(root),
                "TESTING": "false",
                "PROVIDER_SECRET_ENCRYPTION_KEY": key,
                "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION": "v1",
            },
            capture_output=True,
            text=True,
            timeout=180,
        )
        assert result.returncode == exit_code, result.stderr
        value = json.loads(report.read_text())
        assert json.loads(result.stdout) == value
        assert value["credential_restore"] == status
        assert value["database_integrity"] == "passed"
        assert len(value["code_revision"]) == 40
        assert len(value["credential_tool_hash"]) == 64
        assert report.stat().st_mode & 0o777 == 0o600
        output = report.read_text() + result.stdout + result.stderr
        assert KEY not in output and PLAINTEXT not in output
        for ciphertext, nonce in material:
            for secret in (ciphertext, nonce):
                assert secret.hex() not in output
                assert base64.b64encode(secret).decode() not in output
                assert repr(secret) not in output
    assert restored.read_bytes() == before


@pytest.mark.parametrize(
    "unsafe", ["changed", "public_directory", "symlink", "hardlink", "dotenv"]
)
async def test_cli_refuses_unsafe_reports_without_overwriting_evidence(
    restored, tmp_path, monkeypatch, capsys, unsafe
):
    from src.db.s6_credentials import main

    directory = tmp_path / "evidence"
    directory.mkdir(mode=0o700)
    target = directory / "prior.json"
    target.write_text('{"private":"REPORT-SENTINEL"}')
    target.chmod(0o600)
    report = target
    if unsafe == "public_directory":
        directory.chmod(0o755)
    elif unsafe == "symlink":
        report = directory / "link.json"
        report.symlink_to(target)
    elif unsafe == "hardlink":
        report = directory / "link.json"
        os.link(target, report)
    elif unsafe == "dotenv":
        report = directory / ".env"
    before = restored.read_bytes(), target.read_bytes()
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "s6_credentials",
            "--restored-db",
            str(restored),
            "--report",
            str(report),
        ],
    )
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    output = capsys.readouterr()
    assert json.loads(output.out)["error_code"] == "invalid_input_or_evidence"
    assert "REPORT-SENTINEL" not in output.out + output.err
    assert (restored.read_bytes(), target.read_bytes()) == before
    if unsafe == "dotenv":
        assert not report.exists()
