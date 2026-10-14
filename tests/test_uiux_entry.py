"""Login and navigation through HTTP; no production data or provider calls."""

import httpx
import pytest
from starlette_csrf import CSRFMiddleware
from test_scenario_api import client as client_fixture
from test_scenario_api import login

from src.main import app

client = client_fixture


async def test_login_error_and_logout_keep_authentication_and_csrf(
    data, client
):
    response = await client.get("/login")
    assert response.status_code == 200
    assert 'href="/admin"' not in response.text
    response = await client.post(
        "/login",
        data={
            "username": "missing_account",
            "password": "wrong-password",
        },
    )
    assert response.status_code == 401
    assert 'role="alert"' in response.text
    assert "사용자 ID 또는 비밀번호가 올바르지 않습니다." in response.text
    assert "wrong-password" not in response.text

    data.owner.set_password("uiux-test-password")
    await data.db.commit()
    secured = CSRFMiddleware(
        app,
        secret="test-csrf-secret",
        header_name="x-csrf-token",
        cookie_name="csrftoken",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=secured),
        base_url="http://test",
    ) as browser:
        await browser.get("/login")
        authenticated = await browser.post(
            "/login",
            data={
                "username": data.owner.username,
                "password": "uiux-test-password",
            },
            headers={"x-csrf-token": browser.cookies["csrftoken"]},
        )
        assert authenticated.status_code == 303
        assert authenticated.headers["location"] == "/scenarios"
        assert (await browser.post("/logout")).status_code == 403
        response = await browser.post(
            "/logout", headers={"x-csrf-token": browser.cookies["csrftoken"]}
        )
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
        assert (await browser.get("/scenarios")).status_code == 303


@pytest.mark.parametrize("role", ["owner", "admin"])
async def test_navigation_reflects_authenticated_role_and_current_destination(
    data, client, role
):
    login(client, getattr(data, role))
    response = await client.get("/scenarios")
    assert response.status_code == 200
    assert 'href="/scenarios" aria-current="page"' in response.text
    assert ('href="/admin"' in response.text) == (role == "admin")
    assert 'href="#main-content"' in response.text
    admin_response = await client.get("/admin")
    assert admin_response.status_code == (200 if role == "admin" else 403)
    if role == "admin":
        assert 'href="/admin" aria-current="page"' in admin_response.text
