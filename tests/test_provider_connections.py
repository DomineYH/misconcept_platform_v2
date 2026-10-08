"""Provider credentials through authenticated HTTP, CSRF and real SQLite."""

import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import text
from starlette_csrf import CSRFMiddleware
from test_scenario_api import login

from src.api.dependencies import get_db_session
from src.config import config
from src.db.migrations import migrate
from src.main import app

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
PASSWORD = "PASSWORD-SENTINEL"
KEY = "PROVIDER-KEY-SENTINEL-1234"
MASTER = bytes(range(32))


@pytest.fixture
async def api(data, monkeypatch):
    from src.services.provider_reauthentication import reauth_limiter

    reauth_limiter.storage.reset()
    monkeypatch.setattr(migrate, "engine", data.engine)
    await migrate.run_all_migrations()
    data.admin.set_password(PASSWORD)
    await data.db.commit()
    monkeypatch.setattr(
        config,
        "PROVIDER_SECRET_ENCRYPTION_KEY",
        SecretStr(base64.b64encode(MASTER).decode()),
        raising=False,
    )
    monkeypatch.setattr(
        config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "v1", raising=False
    )

    async def database():
        async with data.factory() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    app.dependency_overrides[get_db_session] = database
    try:
        protected = CSRFMiddleware(
            app,
            secret=config.SESSION_SECRET,
            cookie_name="csrftoken",
            header_name="x-csrf-token",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=protected), base_url="http://test"
        ) as client:
            login(client, data.admin)
            await client.get("/admin/ai")
            yield client
    finally:
        app.dependency_overrides.clear()


async def post(api, operation, version, **extra):
    return await api.post(
        f"/admin/ai/providers/openai/{operation}",
        json={
            "current_password": PASSWORD,
            "expected_version": version,
            **extra,
        },
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )


async def row(data):
    async with data.factory() as db:
        return (
            (
                await db.execute(
                    text(
                        "SELECT * FROM provider_connection WHERE provider='openai'"
                    )
                )
            )
            .mappings()
            .one()
        )


async def test_key_save_replace_and_safe_state(data, api, caplog):
    response = await api.get("/admin/ai/state")
    assert response.status_code == 200
    initial = response.json()
    assert initial["master_key_available"] is True
    assert [p["provider"] for p in initial["providers"]] == [
        "openai",
        "anthropic",
        "google",
    ]
    assert all(p["status"] == "unconfigured" for p in initial["providers"])
    assert initial["models"] == [] and initial["settings"] is None
    response = await post(api, "key", 1, api_key=KEY)
    assert response.status_code == 200
    first = await row(data)
    assert (
        first["enabled"]
        and first["credential_revision"] == 1
        and first["connection_version"] == 2
    )
    assert len(first["nonce"]) == 12
    assert (
        AESGCM(MASTER)
        .decrypt(first["nonce"], first["encrypted_key"], b'["openai",1,1]')
        .decode()
        == KEY
    )
    assert first["encryption_key_version"] == "v1"
    assert (await post(api, "key", 2, api_key=KEY)).status_code == 200
    second = await row(data)
    assert (
        second["nonce"] != first["nonce"]
        and second["encrypted_key"] != first["encrypted_key"]
    )
    assert (
        AESGCM(MASTER)
        .decrypt(second["nonce"], second["encrypted_key"], b'["openai",1,2]')
        .decode()
        == KEY
    )
    snapshot = await api.get("/admin/ai/state")
    provider = snapshot.json()["providers"][0]
    assert provider["masked_hint"] == "••••1234"
    assert provider["verified_at"] is None
    assert provider["credential_revision"] == 2
    assert provider["status"] == "ready"
    async with data.factory() as db:
        audit = (
            (
                await db.execute(
                    text("SELECT * FROM provider_audit_log ORDER BY id")
                )
            )
            .mappings()
            .all()
        )
    assert [a["change_kind"] for a in audit] == ["key_saved", "key_replaced"]
    assert [a["actor_id"] for a in audit] == [data.admin.id, data.admin.id]
    public = (
        snapshot.text
        + response.text
        + (await api.get("/admin/ai")).text
        + str(audit)
        + caplog.text
    )
    for secret in (KEY, PASSWORD, first["encrypted_key"].hex(), MASTER.hex()):
        assert secret not in public


async def test_disable_replace_reactivate_delete_keep_identity_history(
    data, api
):
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    for operation, version, extra in [
        ("enabled", 2, {"enabled": False}),
        ("key", 3, {"api_key": KEY}),
        ("enabled", 4, {"enabled": True}),
        ("delete", 5, {}),
    ]:
        rejected = await post(
            api, operation, version, current_password="WRONG-PASSWORD", **extra
        )
        assert rejected.status_code == 422
        assert (await row(data))["connection_version"] == version
        response = await post(api, operation, version, **extra)
        assert response.status_code == 200
        current = await row(data)
        if version in (2, 3):
            assert current["enabled"] == 0
        if version == 4:
            assert current["enabled"] == 1 and current["verified_at"] is None
    deleted = await row(data)
    assert deleted["id"] == 1
    assert (
        deleted["credential_revision"] == 3
        and deleted["connection_version"] == 6
    )
    assert not deleted["enabled"]
    assert all(
        deleted[field] is None
        for field in [
            "encrypted_key",
            "nonce",
            "encryption_key_version",
            "masked_hint",
        ]
    )
    async with data.factory() as db:
        audit = (
            (
                await db.execute(
                    text(
                        "SELECT change_kind FROM provider_audit_log ORDER BY id"
                    )
                )
            )
            .scalars()
            .all()
        )
        assert (
            await db.execute(text("SELECT teacher_id FROM session"))
        ).scalar() == data.owner.id
        assert (
            await db.execute(text("SELECT count(*) FROM provider_connection"))
        ).scalar() == 3
    assert audit == [
        "key_saved",
        "disabled",
        "key_replaced",
        "enabled",
        "deleted",
    ]
    assert (await api.get("/admin/ai/state")).json()["providers"][0][
        "status"
    ] == "unconfigured"


async def test_enable_disable_clear_connection_check_metadata(data, api):
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    for version, enabled in [(2, False), (3, True)]:
        async with data.engine.begin() as conn:
            await conn.exec_driver_sql(
                "UPDATE provider_connection SET verified_at='2026-01-01', error_code='check_failed' WHERE provider='openai'"
            )
        response = await post(api, "enabled", version, enabled=enabled)
        assert response.status_code == 200
        current = await row(data)
        assert current["verified_at"] is None
        assert current["error_code"] is None
        assert current["enabled"] == enabled
        assert current["connection_version"] == version + 1
        snapshot = (await api.get("/admin/ai/state")).json()["providers"][0]
        assert snapshot["verified_at"] is None


async def test_reauthentication_limits_admin_and_address_failures_only(
    data, api, monkeypatch
):
    for version in range(1, 7):
        assert (await post(api, "key", version, api_key=KEY)).status_code == 200
    for _ in range(5):
        assert (
            await post(api, "key", 7, api_key=KEY, current_password="wrong")
        ).status_code == 422
    assert (await post(api, "key", 7, api_key=KEY)).status_code == 429
    data.other.role = "admin"
    data.other.set_password(PASSWORD)
    await data.db.commit()
    for user, address, expected in [
        (data.admin, "127.0.0.2", 429),
        (data.other, "127.0.0.1", 429),
        (data.other, "127.0.0.2", 200),
    ]:
        protected = CSRFMiddleware(
            app,
            secret=config.SESSION_SECRET,
            cookie_name="csrftoken",
            header_name="x-csrf-token",
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(
                app=protected, client=(address, 1234)
            ),
            base_url="http://test",
        ) as other:
            login(other, user)
            await other.get("/admin/ai")
            assert (
                await post(other, "key", 7, api_key=KEY)
            ).status_code == expected
    import time

    current_time = time.time()
    monkeypatch.setattr(time, "time", lambda: current_time + 301)
    assert (await post(api, "key", 8, api_key=KEY)).status_code == 200


async def test_validation_permission_csrf_conflicts_and_audit_atomicity(
    data, api, caplog
):
    import asyncio
    import logging

    caplog.set_level(logging.DEBUG)
    for extra in [
        dict(expected_version=-1),
        dict(expected_version=True),
        dict(api_key=None),
        dict(api_key={"secret": KEY}),
        dict(current_password={"secret": PASSWORD}),
        dict(unknown_secret=KEY),
        dict(api_key="   "),
        dict(api_key="\ud800"),
    ]:
        response = await api.post(
            "/admin/ai/providers/openai/key",
            content=json.dumps(
                {
                    "expected_version": 1,
                    "current_password": PASSWORD,
                    "api_key": KEY,
                    **extra,
                }
            ),
            headers={
                "Content-Type": "application/json",
                "x-csrf-token": api.cookies["csrftoken"],
            },
        )
        assert response.status_code == 422
        assert KEY not in response.text and PASSWORD not in response.text
    missing_csrf = await api.post(
        "/admin/ai/providers/openai/key",
        json={
            "expected_version": 1,
            "current_password": PASSWORD,
            "api_key": KEY,
        },
    )
    assert missing_csrf.status_code == 403
    login(api, data.owner)
    await api.get("/admin/ai")
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 403
    assert (await api.get("/admin/ai/state")).status_code == 403
    login(api, data.admin)
    await api.get("/admin/ai")
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    race = await asyncio.gather(
        post(api, "key", 2, api_key=KEY), post(api, "key", 2, api_key=KEY)
    )
    assert sorted(r.status_code for r in race) == [200, 409]
    assert all(KEY not in r.text and PASSWORD not in r.text for r in race)
    async with data.engine.begin() as conn:
        await conn.exec_driver_sql(
            "CREATE TRIGGER reject_provider_audit BEFORE INSERT ON provider_audit_log BEGIN SELECT RAISE(ABORT,'audit unavailable'); END;"
        )
    before = await row(data)
    failed = await post(api, "key", 3, api_key=KEY)
    assert failed.status_code == 503
    after = await row(data)
    assert after == before
    async with data.factory() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM provider_audit_log"))
        ).scalar() == 2
    assert KEY not in caplog.text and PASSWORD not in caplog.text
    assert before["encrypted_key"].hex() not in caplog.text
    api.cookies.clear()
    assert (await api.get("/admin/ai/state")).status_code == 401


@pytest.mark.parametrize(
    "damage",
    [
        "missing_master",
        "malformed_master",
        "wrong_master",
        "wrong_version",
        "revision",
        "ciphertext",
        "nonce",
    ],
)
async def test_recovery_isolated_and_delete_without_decryption(
    data, api, monkeypatch, damage
):
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    other = await api.post(
        "/admin/ai/providers/anthropic/key",
        json={
            "current_password": PASSWORD,
            "expected_version": 1,
            "api_key": KEY,
        },
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert other.status_code == 200
    async with data.engine.begin() as conn:
        await conn.exec_driver_sql(
            "UPDATE provider_connection SET verified_at='2026-01-01' WHERE provider='anthropic'"
        )
        if damage == "revision":
            await conn.exec_driver_sql(
                "UPDATE provider_connection SET credential_revision=9 WHERE provider='openai'"
            )
        if damage in ("ciphertext", "nonce"):
            await conn.execute(
                text(
                    f"UPDATE provider_connection SET {damage if damage == 'nonce' else 'encrypted_key'}=:value WHERE provider='openai'"
                ),
                {"value": b"x" * (12 if damage == "nonce" else 32)},
            )
    if damage in ("missing_master", "malformed_master", "wrong_master"):
        value = {
            "missing_master": "",
            "malformed_master": "invalid",
            "wrong_master": base64.b64encode(bytes([7]) * 32).decode(),
        }[damage]
        monkeypatch.setattr(
            config, "PROVIDER_SECRET_ENCRYPTION_KEY", SecretStr(value)
        )
    if damage == "wrong_version":
        monkeypatch.setattr(
            config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "v2"
        )
    snapshot = await api.get("/admin/ai/state")
    assert snapshot.status_code == 200
    assert snapshot.json()["providers"][0]["status"] == "decryption_failed"
    if damage in ("revision", "ciphertext", "nonce"):
        assert snapshot.json()["providers"][1]["status"] == "ready"
    before = await row(data)
    blocked = await post(api, "key", 2, api_key=KEY)
    assert blocked.status_code == 503 and (await row(data)) == before
    assert (await post(api, "enabled", 2, enabled=False)).status_code == 200
    disabled = await row(data)
    async with data.factory() as db:
        audit_count = (
            await db.execute(text("SELECT count(*) FROM provider_audit_log"))
        ).scalar()
    rejected = await post(api, "enabled", 3, enabled=True)
    assert rejected.status_code == 503
    assert rejected.json() == {"detail": {"code": "configuration_unavailable"}}
    assert KEY not in rejected.text and PASSWORD not in rejected.text
    assert (await row(data)) == disabled
    async with data.factory() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM provider_audit_log"))
        ).scalar() == audit_count
    assert (await post(api, "delete", 3)).status_code == 200
    deleted = await row(data)
    assert deleted["encrypted_key"] is None and deleted["nonce"] is None
    monkeypatch.setattr(
        config,
        "PROVIDER_SECRET_ENCRYPTION_KEY",
        SecretStr(base64.b64encode(MASTER).decode()),
    )
    monkeypatch.setattr(config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "v1")
    assert (await post(api, "key", 4, api_key="tiny")).status_code == 200
    final = (await api.get("/admin/ai/state")).json()["providers"][0]
    assert final["masked_hint"] == "••••" and final["status"] == "ready"


async def test_concurrent_reauthentication_failures_respect_limit(data, api):
    import asyncio

    results = await asyncio.gather(
        *(
            post(api, "key", 1, api_key=KEY, current_password="wrong")
            for _ in range(6)
        )
    )
    assert [r.status_code for r in results].count(422) == 5
    assert [r.status_code for r in results].count(429) == 1
    assert (await row(data))["connection_version"] == 1
    async with data.factory() as db:
        assert (
            await db.execute(text("SELECT count(*) FROM provider_audit_log"))
        ).scalar() == 0
