"""Text scenario contracts through authenticated HTTP and real SQLite."""

import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import httpx
import pytest
from itsdangerous import TimestampSigner

from src.api.dependencies import get_db_session
from src.config import config
from src.main import app
from src.models.prompt_template import PromptTemplate
from src.services import base


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
            yield client
    finally:
        app.dependency_overrides.clear()


def login(client, user):
    payload = base64.b64encode(json.dumps({"user_id": user.id}).encode())
    client.cookies.clear()
    client.cookies.set(
        "session_id",
        TimestampSigner(config.SESSION_SECRET).sign(payload).decode(),
    )


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
async def test_missing_public_problem_blocks_new_session(
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
    assert "문제 상황 보완 필요" in response.json()["detail"]
    assert "관리자" in response.json()["detail"]


@pytest.fixture
def provider(monkeypatch):
    from test_student_generation import FakeStream, event

    stream = FakeStream(
        [
            event(
                "response.completed",
                response=SimpleNamespace(
                    status="completed",
                    output_text="Student answer",
                    output=[],
                    usage=None,
                ),
            )
        ]
    )
    fake = SimpleNamespace(
        responses=SimpleNamespace(create=AsyncMock(return_value=stream)),
        close=AsyncMock(),
        max_retries=0,
    )
    monkeypatch.setattr(base, "AsyncOpenAI", lambda **kwargs: fake)
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
    assert response.status_code == 400
    assert "문제 상황 보완 필요" in response.json()["detail"]
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
    for name in ("ScenarioCreate", "ScenarioUpdate", "AdminScenarioResponse"):
        assert_no_video(json.dumps(schemas[name]))


async def test_text_scenario_crud_and_rendering_preserve_legacy_data(
    data, client, scenario_payload
):
    login(client, data.admin)
    created = await client.post("/admin/scenarios", json=scenario_payload)
    assert created.status_code == 201
    assert_no_video(created.text)
    sid = created.json()["id"]
    assert (
        created.json()["problem_situation"]
        == scenario_payload["problem_situation"]
    )
    assert created.json()["greeting_message"] == "Mentor greeting"
    updated = await client.post(
        f"/admin/scenarios/{data.scenario.id}/update",
        json={
            "problem_situation": " Edited public problem ",
            "greeting_message": " Edited mentor greeting ",
            "unrelated_extra": "still ignored",
        },
    )
    assert updated.status_code == 200
    assert_no_video(updated.text)
    assert updated.json()["problem_situation"] == "Edited public problem"
    assert updated.json()["greeting_message"] == "Edited mentor greeting"
    admin_html = await client.get("/admin/scenarios")
    assert admin_html.status_code == 200
    assert "Edited public problem" in admin_html.text
    assert "Public problem" in admin_html.text
    assert_no_video(admin_html.text)
    # These retained columns intentionally have no public API reader.
    await data.db.refresh(data.scenario)
    assert data.scenario.video_url == "https://example.com/legacy-secret"
    assert data.scenario.video_transcript == "PRIVATE LEGACY TRANSCRIPT"
    login(client, data.owner)
    listing = await client.get("/scenarios")
    assert listing.status_code == 200
    assert_no_video(listing.text)
    chat = await client.get(f"/scenarios/{sid}")
    assert chat.status_code == 200
    assert "Public problem &lt;script&gt;unsafe()&lt;/script&gt;" in chat.text
    assert "Mentor greeting" in chat.text
    assert scenario_payload["prompt"] not in chat.text
    assert_no_video(chat.text)
    session = await client.post("/sessions", json={"scenario_id": sid})
    assert session.status_code == 201


async def test_missing_problem_allows_existing_dialogue_and_admin_completion(
    data, client, scenario_payload
):
    from src.models import Message

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
    assert chat.status_code == 200
    assert "Historical answer" in chat.text
    assert "문제 상황 보완 필요" in chat.text
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
    assert repaired.status_code == 200
    login(client, data.owner)
    session = await client.post(
        "/sessions", json={"scenario_id": data.scenario.id}
    )
    assert session.status_code == 201
    assert session.json()["id"] != data.session.id
    # Ending a historical session does not make its saved messages unreadable.
    closed = await client.post(f"/sessions/{data.session.id}/close")
    assert closed.status_code == 200
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
