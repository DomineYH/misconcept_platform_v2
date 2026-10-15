"""Display language at the shared request/render boundary."""

import asyncio
import json

import pytest
from starlette.requests import Request

from src.api.dependencies import templates
from src.api.routes.auth import get_login


def request(header=None, cookie=None):
    headers = []
    if header is not None:
        headers.append((b"x-ui-language", header.encode()))
    if cookie is not None:
        headers.append((b"cookie", f"ui_lang={cookie}".encode()))
    return Request(
        {
            "type": "http",
            "path": "/login",
            "headers": headers,
            "scheme": "http",
            "server": ("test", 80),
            "query_string": b"",
        }
    )


@pytest.mark.parametrize(
    "header,cookie,language",
    [
        ("ko", "en", "ko"),
        ("en", "ko", "en"),
        ("invalid", "en", "en"),
        (None, "en", "en"),
        ("invalid", "invalid", "ko"),
        (None, None, "ko"),
    ],
)
async def test_login_resolves_display_language_per_request(
    header, cookie, language
):
    response = await get_login(request(header, cookie))
    html = response.body.decode()
    assert f'<html lang="{language}">' in html
    assert (
        "Teacher training platform" if language == "en" else "교사 연수 플랫폼"
    ) in html


async def test_concurrent_login_requests_do_not_mix_display_language():
    responses = await asyncio.gather(
        *(get_login(request(language)) for language in ["ko", "en"] * 20)
    )
    for response, language in zip(responses, ["ko", "en"] * 20):
        assert f'<html lang="{language}">' in response.body.decode()


def test_direct_render_and_js_strings_share_safe_fallback_and_interpolation():
    from src.i18n import CATALOG

    # Simulate a Korean-only entry supplied by a later page owner.
    CATALOG["teacher"]["greeting"] = {"ko": "안녕하세요 {name}"}
    try:
        html = templates.env.from_string(
            "{{ t('teacher.greeting', name=name) }}|{{ t('unknown.key') }}"
        ).render(request=request("en"), name="<script>alert(1)</script>")
        assert html == (
            "안녕하세요 &lt;script&gt;alert(1)&lt;/script&gt;|표시할 수 없는 안내입니다."
        )
        from src.i18n import ui_strings

        strings = ui_strings("en", ["teacher"])
        assert strings["teacher.greeting"] == "안녕하세요 {name}"
        assert "login.title" not in strings
    finally:
        del CATALOG["teacher"]["greeting"]


async def test_login_injects_page_strings_once_in_document_language():
    response = await get_login(request("en"))
    html = response.body.decode()
    assert html.count('id="ui-strings"') == 1
    strings = json.loads(
        html.split('type="application/json">')[1].split("</script>")[0]
    )
    assert strings["login.title"] == "Log in"
    assert (
        strings["common.guard_busy"]
        == "Wait until the task finishes before switching languages."
    )


async def test_failed_login_retains_identifier_and_localizes_auth_errors(data):
    import httpx
    from fastapi import FastAPI
    from starlette.middleware.sessions import SessionMiddleware

    from src.api.dependencies import get_db_session
    from src.api.routes.auth import router

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="display-language-test")
    app.include_router(router)

    async def database():
        yield data.db

    app.dependency_overrides[get_db_session] = database
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        response = await client.post(
            "/login",
            headers={"X-UI-Language": "en"},
            data={"username": "unknown_user", "password": "private-password"},
        )
        assert response.status_code == 401
        assert "The user ID or password is incorrect." in response.text
        assert 'value="unknown_user"' in response.text
        assert "private-password" not in response.text
        invalid = await client.post(
            "/login",
            headers={"X-UI-Language": "en"},
            data={"username": "invalid!", "password": "private-password"},
        )
        assert invalid.status_code == 400
        assert invalid.json() == {
            "detail": "User IDs may contain only letters, numbers and underscores."
        }


def test_overlapping_direct_renders_keep_each_requests_display_language():
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    rendezvous = Barrier(2)
    template = templates.env.from_string(
        "{{ t('login.title') }}{% set _ = rendezvous() %}|{{ t('login.title') }}"
    )
    with ThreadPoolExecutor(max_workers=2) as executor:
        renders = [
            executor.submit(
                template.render,
                request=request(language),
                rendezvous=lambda: rendezvous.wait(timeout=10),
            )
            for language in ("ko", "en")
        ]
        assert [render.result() for render in renders] == [
            "로그인|로그인",
            "Log in|Log in",
        ]
