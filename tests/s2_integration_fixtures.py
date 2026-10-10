"""Final-schema installations and pinned SDK transports for offline S2 flows."""

import base64
import json
from pathlib import Path
from types import SimpleNamespace

import httpx
import httpx2
import pytest
from pydantic import SecretStr
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette_csrf import CSRFMiddleware
from test_provider_connections import KEY, MASTER, PASSWORD

from src.api.dependencies import get_db_session
from src.config import config
from src.db import seed
from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate
from src.db.s2_cutover import rehearse
from src.main import app
from src.models import User, UserGroup


@pytest.fixture(params=["fresh", "converted"])
async def installation(request, data, source, tmp_path, monkeypatch):
    path = tmp_path / "fresh.db"
    if request.param == "converted":
        workspace = tmp_path / "private"
        report = await rehearse(
            source,
            workspace,
            Path("tests/fixtures/s2_legacy_effective.json"),
            apply=True,
        )
        assert report["status"] == "ready" and report["restore_verified"]
        path = workspace / "rehearsal.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{path}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(migrate, "engine", engine)
    monkeypatch.setattr(seed, "AsyncSessionLocal", factory)
    monkeypatch.setattr(config, "ADMIN_DEFAULT_PASSWORD", PASSWORD)
    monkeypatch.setattr(
        config,
        "PROVIDER_SECRET_ENCRYPTION_KEY",
        SecretStr(base64.b64encode(MASTER).decode()),
    )
    monkeypatch.setattr(config, "PROVIDER_SECRET_ENCRYPTION_KEY_VERSION", "v1")
    try:
        if request.param == "fresh":
            await seed.seed_database()
        else:
            await migrate.run_all_migrations()
        async with factory() as db:
            group = await db.scalar(select(UserGroup).order_by(UserGroup.id))
            for username in ("admin", "owner", "other"):
                user = await db.scalar(
                    select(User).where(User.username == username)
                )
                if user is None:
                    user = User(
                        username=username,
                        nickname=username,
                        group_id=group.id if username == "owner" else None,
                    )
                    db.add(user)
                user.set_password(PASSWORD)
            await db.commit()
        yield SimpleNamespace(
            factory=factory,
            group_id=group.id,
            converted=request.param == "converted",
        )
    finally:
        await engine.dispose()


@pytest.fixture
async def workflow_api(installation):
    async def database():
        async with installation.factory() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    app.dependency_overrides[get_db_session] = database
    try:
        protected = CSRFMiddleware(
            app, secret=config.SESSION_SECRET, header_name="x-csrf-token"
        )
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=protected), base_url="http://test"
        ) as api:
            yield api
    finally:
        app.dependency_overrides.clear()


async def authenticate(api, username):
    api.cookies.clear()
    await api.get("/login")
    response = await api.post(
        "/login",
        data=dict(username=username, password=PASSWORD),
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )
    assert response.status_code == 303, response.text
    await api.get("/scenarios")


def workflow_transport(monkeypatch, provider, *, budget=1024):
    """Only the provider's HTTP boundary is replaced; SDK parsing stays real."""
    from test_student_probe import response_body, sdk_transport, sse

    payloads, schemas = [], []
    state = SimpleNamespace(
        fail_analysis=False,
        structured_results=[],
        student_answer="Student answer",
    )

    def answer(body, schema):
        payloads.append(body)
        properties = (schema or {}).get("properties", {})
        schemas.append(properties)
        if properties and state.structured_results:
            return json.dumps(state.structured_results.pop(0))
        if state.fail_analysis and "brief_feedback" in properties:
            return "invalid JSON"
        if "results" in properties:
            return json.dumps({"results": [{"index": 0, "is_greeting": False}]})
        if "label" in properties:
            return json.dumps(
                dict(label="A", confidence=0.9, reasoning="Explore")
            )
        if "brief_feedback" in properties:
            return json.dumps(
                dict(
                    brief_feedback=["Good question"],
                    strengths=[],
                    improvements=[],
                    dialogue_coaching=[],
                )
            )
        if "should_intervene" in properties:
            return json.dumps(
                dict(
                    should_intervene=True,
                    feedback="Mentor coaching",
                    reason_summary="Authored condition",
                )
            )
        return state.student_answer if body.get("stream") else "Mentor coaching"

    if provider == "openai":

        async def upstream(request, body):
            text = answer(
                body, body.get("text", {}).get("format", {}).get("schema")
            )
            if body.get("stream"):
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=sse(
                        "response.completed",
                        response=response_body(text),
                        sequence_number=1,
                    ),
                )
            return httpx2.Response(200, json=response_body(text))

        clients, calls = sdk_transport(
            monkeypatch, upstream, budget=budget, key=KEY
        )
    elif provider == "anthropic":
        from test_anthropic_catalog import sdk_transport
        from test_anthropic_probes import response_body, stream_body

        async def upstream(request):
            body = json.loads(request.content)
            text = answer(
                body,
                body.get("output_config", {}).get("format", {}).get("schema"),
            )
            if body.get("stream"):
                return httpx2.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=stream_body(text),
                )
            return httpx2.Response(200, json=response_body(text))

        clients, calls = sdk_transport(
            monkeypatch, upstream, module="anthropic_generation"
        )
    else:
        from test_google_catalog import install
        from test_google_invocations import response, sse

        async def upstream(request):
            body = json.loads(request.content)
            streaming = request.url.path.endswith(":streamGenerateContent")
            text = answer(
                {**body, "stream": streaming},
                body.get("generationConfig", {}).get("responseJsonSchema"),
            )
            if streaming:
                return httpx.Response(
                    200,
                    headers={"content-type": "text/event-stream"},
                    content=sse(response(text)),
                )
            return httpx.Response(200, json=response(text))

        clients, calls = install(monkeypatch, upstream)
    return SimpleNamespace(
        clients=clients,
        calls=calls,
        payloads=payloads,
        schemas=schemas,
        state=state,
    )
