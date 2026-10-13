"""Restored history/login survive unavailable credentials; AI stays blocked."""

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest
from lesson_fixtures import install_connection, install_snapshot
from pydantic import SecretStr
from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_s6_cutover import current_source, legacy_source

from src.api.dependencies import get_db_session
from src.config import config
from src.db.connection import set_sqlite_pragma
from src.db.s2_verification import consistent_backup
from src.db.s6_credentials import verify_credentials
from src.main import app
from src.models import Scenario, Session, User

__all__ = ["current_source", "legacy_source"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
PASSWORD = "SYNTHETIC-RESTORE-PASSWORD"


@pytest.mark.parametrize("damage", ["missing", "wrong_version", "ciphertext"])
async def test_restored_login_and_history_work_but_ai_admission_is_denied(
    current_source, tmp_path, monkeypatch, damage
):
    engine = create_async_engine(f"sqlite+aiosqlite:///{current_source}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            owner = await db.get(User, 1)
            owner.set_password(PASSWORD)
            session = Session(
                scenario_id=1, teacher_id=1, started_at=datetime(2026, 1, 3)
            )
            db.add(session)
            await db.flush()
            native = SimpleNamespace(
                db=db,
                engine=engine,
                session=session,
                scenario=await db.get(Scenario, 1),
            )
            connection, model = await install_connection(native, monkeypatch)
            await install_snapshot(native, connection, model)
            session_id = session.id
    finally:
        await engine.dispose()

    restored = tmp_path / "restored-readers.db"
    consistent_backup(current_source, restored)
    assert verify_credentials(restored)["credential_restore"] == "passed"
    engine = create_async_engine(f"sqlite+aiosqlite:///{restored}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        if damage == "missing":
            monkeypatch.setattr(
                config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr("")
            )
        elif damage == "wrong_version":
            monkeypatch.setattr(
                config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "other"
            )
        else:
            async with engine.begin() as conn:
                await conn.execute(
                    text(
                        "UPDATE provider_connection SET encrypted_key=:key WHERE id=1"
                    ),
                    {"key": b"x" * 32},
                )
            await engine.dispose()
        result = verify_credentials(restored)
        assert result["database_integrity"] == "passed"
        assert result["credential_restore"] == (
            "incomplete" if damage == "wrong_version" else "failed"
        )

        async def database():
            async with factory() as db:
                yield db
                await db.commit()

        app.dependency_overrides[get_db_session] = database
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            assert (await client.get("/login")).status_code == 200
            login = await client.post(
                "/login", data=dict(username="owner", password=PASSWORD)
            )
            assert login.status_code == 303
            history = await client.get("/sessions/1/analysis")
            assert history.status_code == 200
            assert history.json()["messages"][0]["content"] == "Original {text}"
            denied = await client.post(
                f"/sessions/{session_id}/turns/stream",
                json=dict(
                    request_id=str(uuid4()), content="Synthetic question"
                ),
            )
            assert denied.status_code == 503
            assert (
                denied.json()["detail"]["code"] == "configuration_unavailable"
            )
            assert (
                await client.get(f"/sessions/{session_id}/messages/updates")
            ).status_code == 204
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
