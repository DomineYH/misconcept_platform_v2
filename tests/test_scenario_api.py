"""Text scenario contracts through authenticated HTTP and real SQLite."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from itsdangerous import TimestampSigner
from legacy_models import PromptTemplate

from src.api.dependencies import get_db_session
from src.config import config
from src.main import app


@pytest.fixture
async def client(data):
    async def database():
        async with data.factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_db_session] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            from analysis_test_helpers import AnalysisApi

            yield AnalysisApi(client)
    finally:
        app.dependency_overrides.clear()


def login(client, user):
    payload = base64.b64encode(json.dumps({"user_id": user.id}).encode())
    client.cookies.clear()
    client.cookies.set(
        "session_id",
        TimestampSigner(config.SESSION_SECRET).sign(payload).decode(),
    )


@pytest.mark.parametrize(
    "path",
    [
        "/fixtures/s2/editor",
        "/fixtures/s2/lesson",
        "/fixtures/s2/lesson-controller.js",
    ],
)
async def test_s2_browser_fixtures_are_not_application_routes(
    data, client, path
):
    login(client, data.admin)
    response = await client.get(path)
    assert response.status_code == 404


async def test_mock_query_flags_do_not_replace_production_screen(data, client):
    login(client, data.admin)
    response = await client.get("/admin/scenarios?variant=C&mock=1")
    assert response.status_code == 200
    assert 'href="/admin/scenarios/new"' in response.text
    assert 'id="scenario-form"' not in response.text
    assert "INTERNAL_STUDENT_SENTINEL" not in response.text


@pytest.fixture
async def scenario_payload(data):
    template = PromptTemplate(
        bot_type="student",
        template_name="Text student",
        template_text="{prompt} {student_profile}",
    )
    data.db.add(template)
    await data.db.flush()
    data.scenario.student_template_id = template.id
    data.scenario.student_profile = "Student profile"
    data.scenario.video_url = "https://example.com/legacy-secret"
    data.scenario.video_transcript = "PRIVATE LEGACY TRANSCRIPT"
    await data.db.commit()
    return {
        "title": "Text scenario",
        "prompt": "Internal misconception context",
        "student_profile": "Student profile",
        "problem_situation": "Public problem <script>unsafe()</script>",
        "greeting_message": "Mentor greeting",
        "framework_id": data.framework.id,
        "student_template_id": template.id,
        "group_ids": [data.owner.group_id],
        "unrelated_extra": "still ignored",
    }


@pytest.mark.parametrize("field", ["video_url", "video_transcript"])
@pytest.mark.parametrize("value", [None, "", "https://example.com/video"])
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_retired_video_fields_rejected(
    data, client, scenario_payload, field, value, operation
):
    login(client, data.admin)
    path = "/admin/scenarios"
    payload = dict(scenario_payload)
    if operation == "update":
        path += f"/{data.scenario.id}/update"
        payload = {"title": "Edited scenario"}
    payload[field] = value
    response = await client.post(path, json=payload)
    assert response.status_code == 422
    assert field in response.text


@pytest.mark.parametrize("problem", [None, "", " \n\t "])
@pytest.mark.parametrize("entry", ["api", "detail"])
async def test_unconverted_scenario_blocks_new_session_without_legacy_fallback(
    data, client, scenario_payload, problem, entry
):
    data.scenario.problem_situation = problem
    await data.db.commit()
    login(client, data.owner)
    if entry == "api":
        response = await client.post(
            "/sessions", json={"scenario_id": data.scenario.id}
        )
    else:
        response = await client.get(f"/scenarios/{data.scenario.id}")
    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "configuration_unavailable"}


@pytest.fixture
async def provider(data, scenario_payload, monkeypatch):
    import httpx2
    from lesson_fixtures import LESSON_KEY, install_connection
    from openai import AsyncOpenAI
    from test_student_probe import response_body, sse

    from src.services import openai_generation

    connection, model = await install_connection(data, monkeypatch)
    fake = SimpleNamespace(responses=SimpleNamespace(create=AsyncMock()))
    fake.connection, fake.model = connection, model

    async def upstream(request):
        assert request.headers["authorization"] == f"Bearer {LESSON_KEY}"
        await fake.responses.create(**json.loads(request.content))
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                "response.completed",
                response=response_body("Student answer"),
                sequence_number=1,
            ),
        )

    def factory(**kwargs):
        assert kwargs["max_retries"] == 0
        return AsyncOpenAI(
            **kwargs,
            http_client=httpx2.AsyncClient(
                transport=httpx2.MockTransport(upstream)
            ),
        )

    monkeypatch.setattr(openai_generation, "AsyncOpenAI", factory)
    return fake


@pytest.mark.parametrize("problem", [None, "", " \n\t "])
async def test_missing_public_problem_blocks_generation_in_existing_session(
    data, client, scenario_payload, provider, problem
):
    data.scenario.problem_situation = problem
    data.session.ended_at = None
    await data.db.commit()
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Why?"},
    )
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "legacy_read_only"
    provider.responses.create.assert_not_awaited()
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.status_code == 204


def assert_no_video(text):
    for retired in (
        "video_url",
        "video_transcript",
        "https://example.com/legacy-secret",
        "PRIVATE LEGACY TRANSCRIPT",
    ):
        assert retired not in text


def test_public_scenario_schemas_do_not_include_video():
    schemas = app.openapi()["components"]["schemas"]
    for name in ("DraftCreate", "DraftUpdate", "DraftSaved"):
        assert_no_video(json.dumps(schemas[name]))


async def test_legacy_crud_is_retired_and_source_data_preserved(
    data, client, scenario_payload
):
    login(client, data.admin)
    assert (
        await client.post("/admin/scenarios", json=scenario_payload)
    ).status_code == 422
    updated = await client.post(
        f"/admin/scenarios/{data.scenario.id}/update",
        json={"problem_situation": "Edited public problem"},
    )
    assert updated.status_code == 422
    admin_html = await client.get("/admin/scenarios")
    assert admin_html.status_code == 200
    assert "변환 필요" in admin_html.text
    assert_no_video(admin_html.text)
    await data.db.refresh(data.scenario)
    assert data.scenario.video_url == "https://example.com/legacy-secret"
    assert data.scenario.video_transcript == "PRIVATE LEGACY TRANSCRIPT"
    assert data.scenario.problem_situation is None
    login(client, data.owner)
    for path, method in [
        (f"/scenarios/{data.scenario.id}", "GET"),
        ("/sessions", "POST"),
    ]:
        response = (
            await client.get(path)
            if method == "GET"
            else await client.post(path, json={"scenario_id": data.scenario.id})
        )
        assert response.status_code == 400
        assert response.json()["detail"] == {
            "code": "configuration_unavailable"
        }
        assert scenario_payload["prompt"] not in response.text
        assert_no_video(response.text)


async def test_legacy_history_is_preserved_without_reconstructing_lesson(
    data, client, scenario_payload
):
    from sqlalchemy import func, select

    from src.models import Message, Session

    data.session.ended_at = None
    data.db.add(
        Message(
            session_id=data.session.id,
            role="student",
            content="Historical answer",
        )
    )
    await data.db.commit()
    login(client, data.owner)
    chat = await client.get(f"/scenarios/{data.scenario.id}")
    assert chat.status_code == 400
    assert chat.json()["detail"] == {"code": "configuration_unavailable"}
    assert data.scenario.prompt not in chat.text
    assert_no_video(chat.text)
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.status_code == 200
    assert "Historical answer" in updates.text
    login(client, data.admin)
    history = await client.get("/admin/sessions")
    assert history.status_code == 200
    assert [row["id"] for row in history.json()["sessions"]] == [
        data.session.id
    ]
    html = await client.get("/admin/scenarios")
    assert html.status_code == 200
    assert_no_video(html.text)
    repaired = await client.post(
        f"/admin/scenarios/{data.scenario.id}/update",
        json={"problem_situation": "Administrator-completed public problem"},
    )
    assert repaired.status_code == 422
    login(client, data.owner)
    session = await client.post(
        "/sessions", json={"scenario_id": data.scenario.id}
    )
    assert session.status_code == 400
    assert session.json()["detail"] == {"code": "configuration_unavailable"}
    assert await data.db.scalar(select(func.count(Session.id))) == 1
    # Read-only history cannot change its original ending timestamp.
    closed = await client.post(f"/sessions/{data.session.id}/close")
    assert closed.status_code == 409
    assert closed.json()["detail"]["code"] == "legacy_read_only"
    updates = await client.get(f"/sessions/{data.session.id}/messages/updates")
    assert updates.status_code == 200
    assert "Historical answer" in updates.text


async def test_text_scenario_permissions_preserved(
    data, client, scenario_payload
):
    sid = data.scenario.id
    assert (await client.get("/admin/scenarios")).status_code == 303
    assert (
        await client.post("/sessions", json={"scenario_id": sid})
    ).status_code == 303
    login(client, data.owner)
    assert (
        await client.post("/admin/scenarios", json=scenario_payload)
    ).status_code == 403
    assert (
        await client.post(f"/admin/scenarios/{sid}/update", json={})
    ).status_code == 403
    login(client, data.other)
    assert (await client.get(f"/scenarios/{sid}")).status_code == 403
    assert (
        await client.post("/sessions", json={"scenario_id": sid})
    ).status_code == 403
    assert (
        await client.get(f"/sessions/{data.session.id}/messages/updates")
    ).status_code == 403
    login(client, data.admin)
    assert (
        await client.post("/sessions", json={"scenario_id": sid})
    ).status_code == 400
    data.scenario.problem_situation = "Public problem"
    data.scenario.is_active = 0
    await data.db.commit()
    login(client, data.owner)
    assert (
        await client.post("/sessions", json={"scenario_id": sid})
    ).status_code == 404
    data.scenario.mark_deleted()
    await data.db.commit()
    login(client, data.admin)
    assert (
        await client.post("/sessions", json={"scenario_id": sid})
    ).status_code == 404


async def test_generation_does_not_send_legacy_video_to_provider(
    data, client, scenario_payload, provider
):
    from lesson_fixtures import install_snapshot

    await install_snapshot(data, provider.connection, provider.model)
    data.scenario.problem_situation = "Public problem"
    data.session.ended_at = None
    await data.db.commit()
    login(client, data.owner)
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "Why?"},
    )
    assert response.status_code == 200
    assert "Student answer" in response.text
    assert_no_video(response.text)
    assert provider.responses.create.await_count == 1
    for call in provider.responses.create.call_args_list:
        assert_no_video(json.dumps(call.kwargs))
