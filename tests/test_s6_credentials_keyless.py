"""Pre-credential restores, including the empty S6 source, need no key."""

import sqlite3
from contextlib import closing

import pytest
from pydantic import SecretStr

from src.config import config
from src.db.s2_verification import consistent_backup
from src.db.s6_credentials import verify_credentials
from src.db.s6_verification import upgrade


@pytest.mark.parametrize("revision", [0, 23, 24])
async def test_restore_before_provider_storage_is_not_applicable(
    tmp_path, monkeypatch, revision
):
    source = tmp_path / "source.db"
    with closing(sqlite3.connect(source)):
        pass
    if revision:
        await upgrade(source, revision)
    restored = tmp_path / "restored.db"
    consistent_backup(source, restored)
    monkeypatch.setattr(config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr(""))
    monkeypatch.setattr(config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "")
    before = restored.read_bytes()
    result = verify_credentials(restored)
    assert result["database_integrity"] == "passed"
    assert result["credential_restore"] == "not_applicable"
    assert result["versions"] == []
    assert restored.read_bytes() == before
