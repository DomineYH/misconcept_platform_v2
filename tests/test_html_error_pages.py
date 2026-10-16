"""Full-document errors and API compatibility through the real ASGI app."""

from datetime import datetime

import httpx
import pytest
from test_scenario_api import login

from src.api.dependencies import get_db_session
from src.main import app


@pytest.fixture
async def client(data, monkeypatch):
    async def database():
        async with data.factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    monkeypatch.setattr("src.main.AsyncSessionLocal", data.factory)
    app.dependency_overrides[get_db_session] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as api:
            yield api
    finally:
        app.dependency_overrides.clear()


@pytest.mark.parametrize(
    "path",
    [
        "deleted_scenario",
        "/admin/scenarios/999999/edit",
        "/sessions/999999",
        "/nope",
    ],
)
async def test_html_not_found_has_navigation_and_return_link(
    data, client, path
):
    login(client, data.admin)
    if path == "deleted_scenario":
        data.scenario.deleted_at = datetime(2026, 1, 1)
        await data.db.commit()
        path = f"/scenarios/{data.scenario.id}"
    response = await client.get(path, headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert '<html lang="ko">' in response.text
    assert '<main id="main-content">' in response.text
    assert 'class="skip-link"' in response.text
    assert 'class="nav-username" title="Admin"' in response.text
    assert (
        '<h2 class="empty-state-title" id="error-title">페이지를 찾을 수 없습니다</h2>'
        in response.text
    )
    assert (
        'href="/scenarios" class="btn-primary">시나리오 목록으로</a>'
        in response.text
    )
    assert "Traceback" not in response.text
    assert path not in response.text


@pytest.mark.parametrize(
    "language,title,description",
    [
        (
            "ko",
            "접근 권한이 없습니다",
            "이 페이지나 자료에 접근할 권한이 없습니다.",
        ),
        (
            "en",
            "Access denied",
            "You do not have permission to access this page or resource.",
        ),
    ],
)
async def test_forbidden_page_uses_language_and_teacher_navigation(
    data, client, language, title, description
):
    login(client, data.owner)
    client.cookies.set("ui_lang", "en" if language == "ko" else "ko")
    response = await client.get(
        "/admin/scenarios/999999/edit",
        headers={"Accept": "text/html", "X-UI-Language": language},
    )
    assert response.status_code == 403
    assert response.headers["content-type"].startswith("text/html")
    assert f'<html lang="{language}">' in response.text
    assert title in response.text
    assert description in response.text
    assert 'title="Owner"' in response.text
    assert 'href="/admin"' not in response.text
    assert "999999" not in response.text
    assert "Admin access required" not in response.text
    assert "관리자 권한이 필요합니다." not in response.text


@pytest.mark.parametrize(
    "accept", ["application/json", "*/*", None, "text/html"]
)
async def test_same_failures_keep_exact_json_for_api_fetch_and_htmx(
    data, client, accept
):
    data.scenario.deleted_at = datetime(2026, 1, 1)
    await data.db.commit()
    headers = {"X-UI-Language": "en"}
    client.headers.pop("accept", None)
    if accept is not None:
        headers["Accept"] = accept
    if accept == "text/html":
        headers["HX-Request"] = "true"
    for path, user, status_code, body in (
        (
            f"/scenarios/{data.scenario.id}",
            data.admin,
            404,
            b'{"detail":"Scenario not found"}',
        ),
        (
            "/admin/scenarios/999999/edit",
            data.admin,
            404,
            b'{"detail":"Scenario not found."}',
        ),
        (
            "/sessions/999999",
            data.admin,
            404,
            b'{"detail":"Session not found"}',
        ),
        ("/nope", data.admin, 404, b'{"detail":"Not Found"}'),
        ("/admin", data.owner, 403, b'{"detail":"Admin role required"}'),
    ):
        login(client, user)
        response = await client.get(path, headers=headers)
        assert response.status_code == status_code
        assert response.headers["content-type"] == "application/json"
        assert response.content == body
        if accept is None:
            assert "accept" not in response.request.headers


@pytest.mark.parametrize("user_id", [None, -1])
async def test_unregistered_page_renders_with_optional_user_and_cookie_language(
    data, client, user_id
):
    if user_id is not None:
        from types import SimpleNamespace

        login(client, SimpleNamespace(id=user_id))
    client.cookies.set("ui_lang", "en")
    response = await client.get("/nope", headers={"Accept": "text/html"})
    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert '<html lang="en">' in response.text
    assert "Page not found" in response.text
    assert "could not be found or may have been deleted" in response.text
    assert (
        'href="/scenarios" class="btn-primary">Back to scenarios</a>'
        in response.text
    )
    assert 'class="nav-username"' not in response.text
    assert 'href="/admin"' not in response.text


async def test_authentication_redirect_and_htmx_expiry_stay_unchanged(client):
    for path in (
        "/scenarios/999999",
        "/sessions/999999",
        "/admin/scenarios/999999/edit",
    ):
        response = await client.get(path, headers={"Accept": "text/html"})
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    headers = {"Accept": "text/html", "HX-Request": "true"}
    response = await client.get("/admin", headers=headers)
    assert response.status_code == 200
    assert response.headers["HX-Redirect"] == "/login"
    assert response.content == b""
    response = await client.get("/sessions/1/analysis_modal", headers=headers)
    assert response.status_code == 401
    assert response.json()["code"] == "AUTH_EXPIRED"
    assert "auth-expired" in response.headers["HX-Trigger"]
    assert response.headers["Cache-Control"] == "no-store"


@pytest.fixture
def failing_route():
    from fastapi import HTTPException
    from starlette.routing import Route

    async def fail(request):
        details = {
            "string": "PRIVATE DETAIL <script>secret()</script>",
            "object": {"code": "private_code", "resource_id": 123456},
            "list": [{"code": "private_code", "resource_id": 123456}],
        }
        raise HTTPException(
            int(request.path_params["status"]),
            detail=details[request.query_params.get("detail", "object")],
            headers={"Retry-After": "7", "X-Error-Check": "preserved"},
        )

    route = Route(
        "/__error_test/{status}",
        fail,
        methods=["GET", "HEAD", "POST", "PUT", "DELETE"],
    )
    app.router.routes.append(route)
    try:
        yield
    finally:
        app.router.routes.remove(route)


@pytest.mark.parametrize("detail", ["string", "object", "list"])
async def test_error_headers_and_structured_detail_contract(
    client, failing_route, detail
):
    expected = {
        "string": b'{"detail":"PRIVATE DETAIL <script>secret()</script>"}',
        "object": b'{"detail":{"code":"private_code","resource_id":123456}}',
        "list": b'{"detail":[{"code":"private_code","resource_id":123456}]}',
    }
    for status_code in (403, 404):
        path = f"/__error_test/{status_code}?detail={detail}"
        for accept in ("application/json", "text/html"):
            response = await client.get(path, headers={"Accept": accept})
            assert response.status_code == status_code
            assert response.headers["Retry-After"] == "7"
            assert response.headers["X-Error-Check"] == "preserved"
            if accept == "application/json":
                assert response.content == expected[detail]
            else:
                assert response.headers["content-type"].startswith("text/html")
                for private in (
                    "PRIVATE DETAIL",
                    "private_code",
                    "resource_id",
                    "123456",
                    "secret()",
                ):
                    assert private not in response.text


@pytest.mark.parametrize(
    "method,accept,hx,status_code,is_html",
    [
        ("HEAD", "text/html", None, 404, True),
        ("GET", "application/json, text/html;q=0.9", None, 404, True),
        ("GET", "TEXT/HTML; charset=utf-8", None, 403, True),
        ("GET", "text/html-extra", None, 404, False),
        ("GET", "application/xhtml+xml", None, 404, False),
        ("GET", "text/html", "false", 404, False),
        ("GET", "text/html", "", 404, False),
        ("POST", "text/html", None, 404, False),
        ("PUT", "text/html", None, 403, False),
        ("DELETE", "text/html", None, 404, False),
        ("GET", "text/html", None, 401, False),
        ("GET", "text/html", None, 429, False),
    ],
)
async def test_only_full_document_403_and_404_use_html(
    client, failing_route, method, accept, hx, status_code, is_html
):
    headers = {"Accept": accept}
    if hx is not None:
        headers["HX-Request"] = hx
    response = await client.request(
        method, f"/__error_test/{status_code}", headers=headers
    )
    assert response.status_code == status_code
    assert response.headers["Retry-After"] == "7"
    assert response.headers["content-type"].startswith(
        "text/html" if is_html else "application/json"
    )
    if method == "HEAD":
        assert response.content == b""
        assert int(response.headers["content-length"]) > 0
    elif not is_html:
        assert (
            response.content
            == b'{"detail":{"code":"private_code","resource_id":123456}}'
        )


async def test_router_method_error_keeps_allow_header(client):
    response = await client.post("/scenarios", headers={"Accept": "text/html"})
    assert response.status_code == 405
    assert "GET" in response.headers["Allow"]
    assert response.content == b'{"detail":"Method Not Allowed"}'
