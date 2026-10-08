"""The AI screen uses real administrator authentication without provider calls."""

import base64
import json

import httpx
import pytest
from itsdangerous import TimestampSigner

from src.api.dependencies import get_db_session
from src.config import config
from src.main import app


@pytest.mark.parametrize(
    ("account", "expected"), [(None, 303), ("owner", 403), ("admin", 200)]
)
async def test_ai_page_requires_admin_and_renders_no_secret(
    data, account, expected
):
    async def database():
        async with data.factory() as db:
            yield db

    app.dependency_overrides[get_db_session] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            if account:
                payload = base64.b64encode(
                    json.dumps({"user_id": getattr(data, account).id}).encode()
                )
                client.cookies.set(
                    "session_id",
                    TimestampSigner(config.SESSION_SECRET)
                    .sign(payload)
                    .decode(),
                )
            response = await client.get("/admin/ai")
            assert response.status_code == expected
            if expected == 200:
                assert "AI 연결·모델" in response.text
                assert 'src="/static/js/ai-connections.js"' in response.text
                assert "test-only" not in response.text
                dashboard = await client.get("/admin")
                assert 'href="/admin/ai"' in dashboard.text
                state = await client.get("/admin/ai/state")
                assert state.status_code == 200
                assert len(state.json()["providers"]) == 3
                assert state.json()["models_available"] is False
                assert state.json()["settings"] is None
    finally:
        app.dependency_overrides.clear()
