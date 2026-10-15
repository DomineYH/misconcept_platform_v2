"""Admin language follows the originating HTTP request without changing data."""

import httpx
import pytest
from fastapi import FastAPI

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes import (
    admin_about,
    admin_groups,
    admin_providers,
    admin_users,
)


@pytest.fixture
async def client(data):
    app = FastAPI()
    for router in (
        admin_about.router,
        admin_groups.router,
        admin_users.router,
        admin_providers.router,
    ):
        app.include_router(router)

    async def database():
        yield data.db

    app.dependency_overrides[get_db_session] = database
    app.dependency_overrides[get_admin_user] = lambda: data.admin
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        yield client


async def test_admin_role_error_uses_document_header_over_cookie(client):
    response = await client.post(
        "/admin/users",
        headers={"X-UI-Language": "en", "Cookie": "ui_lang=ko"},
        json={
            "username": "new_user",
            "nickname": "원문 이름",
            "password": "private-password",
            "role": "unknown",
        },
    )
    assert response.status_code == 400
    assert response.json() == {"detail": "Invalid role."}
    assert "private-password" not in response.text


async def test_bulk_preview_preserves_header_contract_and_localizes_row_errors(
    client,
):
    response = await client.post(
        "/admin/users/bulk/preview",
        headers={"X-UI-Language": "en"},
        files={
            "file": (
                "users.csv",
                "username,nickname,role,group\nvalid_user,원문 이름,unknown,missing\n".encode(),
                "text/csv",
            ),
        },
    )
    assert response.status_code == 200
    row = response.json()["rows"][0]
    assert row["nickname"] == "원문 이름"
    assert row["errors"] == [
        "Invalid role: unknown",
        "Group not found: missing",
    ]


async def test_ai_state_localizes_option_metadata_without_changing_identifiers(
    client, data, monkeypatch
):
    from lesson_fixtures import install_connection

    await install_connection(data, monkeypatch)
    response = await client.get(
        "/admin/ai/state", headers={"X-UI-Language": "en"}
    )
    assert response.status_code == 200
    model = response.json()["models"][0]
    assert model["model_id"] == "gpt-5-mini"
    assert (
        model["capabilities"]["fields"][0]["label"] == "Maximum output tokens"
    )
    assert (
        model["capabilities"]["option_notes"][0]
        == "Unspecified uses the provider default."
    )


async def test_admin_permission_denial_is_localized(data):
    from fastapi import Depends

    app = FastAPI()

    @app.middleware("http")
    async def teacher_session(request, call_next):
        request.scope["session"] = {"user_id": data.owner.id}
        return await call_next(request)

    async def database():
        yield data.db

    app.dependency_overrides[get_db_session] = database

    @app.get("/admin-check")
    async def protected(user=Depends(get_admin_user)):
        return {"id": user.id}

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as api:
        response = await api.get(
            "/admin-check", headers={"X-UI-Language": "ko"}
        )
        assert response.status_code == 403
        assert response.json() == {"detail": "관리자 권한이 필요합니다."}
