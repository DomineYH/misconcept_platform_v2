"""One-time bulk credentials through service and authenticated HTTP."""

import httpx
import pytest
from sqlalchemy import select
from starlette_csrf import CSRFMiddleware
from test_scenario_api import client as client_fixture
from test_scenario_api import login

from src.api.schemas.user import BulkUserEntry
from src.main import app
from src.models import User
from src.services.admin_user_bulk import register_bulk_users

client = client_fixture


async def test_bulk_successes_receive_distinct_login_credentials(data, caplog):
    result = await register_bulk_users(
        [
            BulkUserEntry(username="teacher_a", nickname="Teacher A"),
            BulkUserEntry(username="teacher_b", nickname="Teacher B"),
            BulkUserEntry(username="owner", nickname="Duplicate"),
            BulkUserEntry(username="teacher_a", nickname="Duplicate"),
        ],
        data.db,
    )
    await data.db.commit()
    assert result.success_count == 2
    assert result.fail_count == 2
    assert [c.username for c in result.credentials] == [
        "teacher_a",
        "teacher_b",
    ]
    passwords = [c.initial_password for c in result.credentials]
    assert len(set(passwords)) == 2
    for credential in result.credentials:
        assert 8 <= len(credential.initial_password) <= 72
        assert credential.initial_password != "00000000"
        user = await data.db.scalar(
            select(User).where(User.username == credential.username)
        )
        assert user.nickname == credential.nickname
        assert user.verify_password(credential.initial_password)
        assert not user.verify_password("00000000")
        assert credential.initial_password not in user.password_hash
        assert credential.initial_password not in caplog.text
    assert all(
        "initial_password" not in f.model_dump() for f in result.failures
    )
    assert not (await register_bulk_users([], data.db)).credentials


async def test_failed_rows_do_not_leak_credentials_or_abort_successes(data):
    result = await register_bulk_users(
        [
            BulkUserEntry(username="first", nickname="First"),
            BulkUserEntry(username="bad_group", nickname="Bad", group_id=999),
            BulkUserEntry(username="last", nickname="Last"),
            BulkUserEntry(username="x", nickname="Invalid"),
            BulkUserEntry(username="bad_name", nickname="x"),
            BulkUserEntry(username="bad_role", nickname="Bad", role="student"),
        ],
        data.db,
    )
    await data.db.commit()
    assert result.success_count == 2
    assert result.fail_count == 4
    assert [c.username for c in result.credentials] == ["first", "last"]
    assert [f.username for f in result.failures] == [
        "bad_group",
        "x",
        "bad_name",
        "bad_role",
    ]
    assert all(
        "initial_password" not in f.model_dump() for f in result.failures
    )


async def test_bulk_http_credentials_work_once_and_are_not_exposed(
    data, client, caplog
):
    login(client, data.admin)
    payload = {
        "users": [
            {"username": "teacher_a", "nickname": "Teacher A"},
            {"username": "teacher_b", "nickname": "Teacher B"},
            {"username": "owner", "nickname": "Duplicate"},
        ]
    }
    response = await client.post("/admin/users/bulk/register", json=payload)
    assert response.status_code == 200
    assert response.headers.get("cache-control") == "no-store"
    body = response.json()
    assert (body["success_count"], body["fail_count"]) == (2, 1)
    assert len({c["initial_password"] for c in body["credentials"]}) == 2
    page = await client.get("/admin/users")
    repeated = await client.post("/admin/users/bulk/register", json=payload)
    assert repeated.json()["credentials"] == []
    assert repeated.json()["fail_count"] == 3
    for credential in body["credentials"]:
        password = credential["initial_password"]
        assert password not in page.text
        assert password not in repeated.text
        assert password not in caplog.text
        assert password not in response.headers.get("set-cookie", "")
        client.cookies.clear()
        logged_in = await client.post(
            "/login",
            data={"username": credential["username"], "password": password},
        )
        assert logged_in.status_code == 303
        old_password = await client.post(
            "/login",
            data={"username": credential["username"], "password": "00000000"},
        )
        assert old_password.status_code == 401
    # Credential fields have no storage column or ordinary user response.
    async with data.engine.connect() as conn:
        rows = (await conn.exec_driver_sql('SELECT * FROM "user"')).all()
    for credential in body["credentials"]:
        assert credential["initial_password"] not in str(rows)


@pytest.mark.parametrize("endpoint", ["preview", "register"])
async def test_bulk_http_requires_admin(data, client, endpoint):
    kwargs = (
        {"json": {"users": []}}
        if endpoint == "register"
        else {"files": {"file": ("users.csv", b"username,nickname\nnew,New")}}
    )
    url = f"/admin/users/bulk/{endpoint}"
    assert (await client.post(url, **kwargs)).status_code == 303
    login(client, data.owner)
    assert (await client.post(url, **kwargs)).status_code == 403


async def test_bulk_http_preserves_csrf_preview_and_row_failures(data, client):
    login(client, data.admin)
    secured = CSRFMiddleware(
        app,
        secret="test-csrf-secret",
        header_name="x-csrf-token",
        cookie_name="csrftoken",
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=secured),
        base_url="http://test",
        cookies=client.cookies,
    ) as browser:
        await browser.get("/admin/users")
        payload = {
            "users": [
                {"username": "new_teacher", "nickname": "New"},
                {"username": "bad_group", "nickname": "Bad", "group_id": 999},
                {"username": "owner", "nickname": "Existing"},
                {"username": "new_teacher", "nickname": "Repeated"},
            ]
        }
        assert (
            await browser.post("/admin/users/bulk/register", json=payload)
        ).status_code == 403
        file = {
            "file": (
                "users.csv",
                b"username,nickname\nnew_teacher,New\nowner,Existing",
            )
        }
        assert (
            await browser.post("/admin/users/bulk/preview", files=file)
        ).status_code == 403
        headers = {"x-csrf-token": browser.cookies.get("csrftoken")}
        preview = await browser.post(
            "/admin/users/bulk/preview", files=file, headers=headers
        )
        assert preview.status_code == 200
        assert preview.json()["summary"] == {"total": 2, "valid": 1, "error": 1}
        assert "initial_password" not in preview.text
        registered = await browser.post(
            "/admin/users/bulk/register", json=payload, headers=headers
        )
        assert registered.status_code == 200
        assert registered.json()["success_count"] == 1
        assert registered.json()["fail_count"] == 3
        assert len(registered.json()["credentials"]) == 1
        assert registered.json()["credentials"][0]["username"] == "new_teacher"
        assert all(
            "initial_password" not in f for f in registered.json()["failures"]
        )
