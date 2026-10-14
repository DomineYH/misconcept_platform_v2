"""Existing administrator CRUD/permission/CSRF contracts via isolated HTTP."""

import httpx
import pytest
from starlette_csrf import CSRFMiddleware
from test_scenario_api import client as client_fixture
from test_scenario_api import login

from src.main import app

client = client_fixture


@pytest.mark.parametrize(
    "kind,payload,updated",
    [
        (
            "groups",
            {"name": '합성 "그룹"', "description": "두 줄\n설명"},
            {"name": "수정 그룹"},
        ),
        (
            "about",
            {
                "name": "합성 소개",
                "affiliation": "합성 소속",
                "bio": "합성 이력",
            },
            {"bio": "수정 이력"},
        ),
    ],
)
async def test_admin_crud_preserves_inputs_and_csrf(
    data, client, kind, payload, updated
):
    login(client, data.admin)
    secured = CSRFMiddleware(
        app,
        secret="uiux-test-csrf",
        header_name="x-csrf-token",
        cookie_name="csrftoken",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=secured),
        base_url="http://test",
        cookies=client.cookies,
    ) as browser:
        path = f"/admin/{kind}"
        assert (await browser.get(path)).status_code == 200
        assert (await browser.post(path, json=payload)).status_code == 403
        headers = {"x-csrf-token": browser.cookies.get("csrftoken")}
        response = await browser.post(path, json=payload, headers=headers)
        assert response.status_code == 201
        saved = response.json()
        assert all(saved[key] == value for key, value in payload.items())
        target = f"{path}/{saved['id']}"
        response = await browser.post(
            f"{target}/update", json=updated, headers=headers
        )
        assert response.status_code == 200
        assert all(
            response.json()[key] == value for key, value in updated.items()
        )
        assert (
            await browser.post(f"{target}/delete", headers=headers)
        ).status_code == 200
        assert (
            await browser.post(
                f"{target}/update", json=updated, headers=headers
            )
        ).status_code == 404
        assert (
            await browser.post(path, json={"name": ""}, headers=headers)
        ).status_code == 422


@pytest.mark.parametrize(
    "path",
    [
        "/admin/groups",
        "/admin/about",
        "/admin/sessions-page",
        "/admin/user-conversations",
        "/admin/analysis-page",
        "/admin/api-usage",
    ],
)
async def test_admin_supplementary_pages_reject_teacher(data, client, path):
    login(client, data.owner)
    assert (await client.get(path)).status_code == 403


async def test_public_about_is_readable_without_login(client):
    response = await client.get("/about/content")
    assert response.status_code == 200
    assert "등록된 정보가 없습니다." in response.text


async def test_group_with_members_cannot_be_deleted(data, client):
    login(client, data.admin)
    response = await client.post(f"/admin/groups/{data.owner.group_id}/delete")
    assert response.status_code == 400
    assert "멤버" in response.json()["detail"]
    page = await client.get("/admin/groups")
    assert page.status_code == 200
    assert 'data-group-id="' + str(data.owner.group_id) + '"' in page.text
