"""Authoring defaults through authenticated HTTP, SQLite and SDK transport."""

import asyncio
import json
from datetime import datetime
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import event, text
from test_model_management import write
from test_provider_connections import KEY, post
from test_role_probes import (
    CLASSIFICATION,
    MENTOR_NEGATIVE,
    MENTOR_POSITIVE,
    SYNTHESIS,
)
from test_scenario_api import login
from test_student_probe import api as probe_api
from test_student_probe import cleanup_probes as cleanup_probes
from test_student_probe import (
    completed,
    prepare,
    response_body,
    sdk_transport,
    sse,
)

from src.models import Session
from src.services.model_verification import ROLE_CONTRACT_VERSIONS

api = probe_api
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def settings_body(api, **defaults):
    settings = (await api.get("/admin/ai/state")).json()["settings"]
    return dict(
        expected_version=settings["settings_version"],
        defaults={
            role: value["model_config_id"] if value else None
            for role, value in settings["defaults"].items()
        }
        | defaults,
        limits=settings["limits"],
        timeouts=settings["timeouts"],
    )


@pytest.fixture
async def verified_student(api, monkeypatch):
    body = await prepare(api)

    async def upstream(request, payload):
        if not payload.get("stream"):
            return httpx2.Response(200, json=response_body())
        return httpx2.Response(
            200,
            headers={"content-type": "text/event-stream"},
            content=sse(
                "response.output_text.delta",
                delta="Synthetic student answer",
                sequence_number=1,
                item_id="m",
                output_index=0,
                content_index=0,
            )
            + sse(
                "response.completed",
                sequence_number=2,
                response=response_body(),
            ),
        )

    sdk_transport(monkeypatch, upstream)
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    assert (
        await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name="Student",
            enabled=True,
            default_options={"max_output_tokens": 2048},
        )
    ).status_code == 200


@pytest.mark.parametrize(
    "invalidation",
    [
        "UPDATE provider_connection SET enabled=0, connection_version=connection_version+1 WHERE provider='openai'",
        "UPDATE model_config SET enabled=0 WHERE id=1",
        "UPDATE model_config SET verification_state=json_set(verification_state,'$.student.status','failed') WHERE id=1",
    ],
    ids=["connection", "model", "role"],
)
async def test_default_selection_checks_references_in_the_write_transaction(
    data, api, verified_student, invalidation
):
    body = await settings_body(api, student=1)
    writing = asyncio.Event()

    def attempted_write(conn, cursor, statement, parameters, context, many):
        if statement == "BEGIN IMMEDIATE" or statement.startswith(
            "UPDATE app_setting"
        ):
            writing.set()

    async with data.factory() as db:
        await db.execute(text("BEGIN IMMEDIATE"))
        await db.execute(text(invalidation))
        event.listen(
            data.engine.sync_engine, "before_cursor_execute", attempted_write
        )
        pending = asyncio.create_task(write(api, "settings/update", **body))
        try:
            async with asyncio.timeout(5):
                await writing.wait()
                await db.commit()
                response = await pending
        finally:
            event.remove(
                data.engine.sync_engine,
                "before_cursor_execute",
                attempted_write,
            )
            if not pending.done():
                pending.cancel()
                await asyncio.gather(pending, return_exceptions=True)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "default_model_unavailable"
    settings = (await api.get("/admin/ai/state")).json()["settings"]
    assert settings["defaults"]["student"] is None
    assert settings["settings_version"] == 1


@pytest.mark.parametrize("change", ["key", "enabled", "delete"])
async def test_defaults_preserve_existing_data_and_recover_after_reverification(
    data, api, verified_student, change
):
    data.scenario.chat_model = "existing-scenario-model"
    data.scenario.chat_temperature = 0
    data.db.add(
        Session(
            scenario_id=data.scenario.id,
            teacher_id=data.owner.id,
            started_at=datetime(2026, 1, 3),
        )
    )
    await data.db.commit()

    async def existing_data():
        async with data.engine.connect() as db:
            return [
                (await db.execute(text(query))).all()
                for query in (
                    "SELECT * FROM scenario ORDER BY id",
                    "SELECT * FROM session ORDER BY id",
                    "SELECT id,model_id,default_options_json FROM model_config ORDER BY id",
                )
            ]

    before = await existing_data()
    assert len(before[1]) == 2
    assert (
        await write(
            api, "settings/update", **await settings_body(api, student=1)
        )
    ).status_code == 200
    assert await existing_data() == before
    extra = (
        {"api_key": KEY}
        if change == "key"
        else {"enabled": False} if change == "enabled" else {}
    )
    assert (await post(api, change, 2, **extra)).status_code == 200
    defaults = (await api.get("/admin/ai/state")).json()["settings"]["defaults"]
    assert defaults == dict(
        student=dict(model_config_id=1, available=False),
        mentor=None,
        analysis=None,
    )
    assert (
        await write(api, "settings/update", **await settings_body(api))
    ).status_code == 200
    if change == "enabled":
        assert (await post(api, "enabled", 3, enabled=True)).status_code == 200
    elif change == "delete":
        assert (await post(api, "key", 3, api_key=KEY)).status_code == 200
    assert (await api.get("/admin/ai/state")).json()["settings"][
        "defaults"
    ] == defaults
    probe = dict(expected_version=2, role="student", request_id=str(uuid4()))
    assert (await write(api, "models/1/probes", **probe)).status_code == 202
    assert (await completed(api, probe["request_id"]))["status"] == "succeeded"
    state = (await api.get("/admin/ai/state")).json()
    assert state["settings"]["defaults"]["student"] == dict(
        model_config_id=1, available=True
    )
    assert state["settings"]["settings_version"] == 3
    assert await existing_data() == before


async def test_settings_reject_unauthorized_invalid_and_conflicting_selections(
    data, api, verified_student
):
    body = await settings_body(api, student=1)
    for student in (True, "1", 0, -1, 9999):
        invalid = {**body, "defaults": {**body["defaults"], "student": student}}
        assert (
            await write(api, "settings/update", **invalid)
        ).status_code == 422
    for role in ("mentor", "analysis"):
        invalid = {
            **body,
            "defaults": dict(student=None, mentor=None, analysis=None)
            | {role: 1},
        }
        assert (
            await write(api, "settings/update", **invalid)
        ).status_code == 422
    assert (
        await api.post("/admin/ai/settings/update", json=body)
    ).status_code == 403
    login(api, data.owner)
    await api.get("/admin/ai")
    assert (await write(api, "settings/update", **body)).status_code == 403
    api.cookies.delete("session_id")
    assert (await write(api, "settings/update", **body)).status_code == 401
    login(api, data.admin)
    await api.get("/admin/ai")
    responses = await asyncio.gather(
        write(api, "settings/update", **body),
        write(api, "settings/update", **body),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    state = (await api.get("/admin/ai/state")).json()
    assert state["settings"]["settings_version"] == 2
    assert state["settings"]["defaults"]["student"] == dict(
        model_config_id=1, available=True
    )


async def test_role_contract_invalidation_keeps_reference_and_requires_new_success(
    data, api, verified_student, monkeypatch
):
    assert (
        await write(
            api, "settings/update", **await settings_body(api, student=1)
        )
    ).status_code == 200

    monkeypatch.setitem(ROLE_CONTRACT_VERSIONS, "student", "s1-v2")
    state = (await api.get("/admin/ai/state")).json()
    assert (
        state["models"][0]["verification_state"]["student"]["status"] == "stale"
    )
    assert state["settings"]["defaults"]["student"] == dict(
        model_config_id=1, available=False
    )
    assert (
        await write(
            api, "settings/update", **await settings_body(api, student=None)
        )
    ).status_code == 200
    assert (
        await write(
            api, "settings/update", **await settings_body(api, student=1)
        )
    ).status_code == 422
    probe = dict(expected_version=2, role="student", request_id=str(uuid4()))
    assert (await write(api, "models/1/probes", **probe)).status_code == 202
    assert (await completed(api, probe["request_id"]))["status"] == "succeeded"
    assert (
        await write(
            api, "settings/update", **await settings_body(api, student=1)
        )
    ).status_code == 200


@pytest.mark.parametrize("role", ["mentor", "analysis"])
async def test_each_role_can_select_its_own_verified_model(
    api, monkeypatch, role
):
    body = {**await prepare(api), "role": role}

    async def upstream(request, payload):
        if role == "mentor":
            content = json.dumps(
                MENTOR_POSITIVE if len(calls) == 1 else MENTOR_NEGATIVE
            )
        else:
            content = json.dumps(
                CLASSIFICATION if len(calls) == 1 else SYNTHESIS
            )
        return httpx2.Response(200, json=response_body(content))

    _, calls = sdk_transport(
        monkeypatch, upstream, budget=1500 if role == "mentor" else 2500
    )
    assert (await write(api, "models/1/probes", **body)).status_code == 202
    assert (await completed(api, body["request_id"]))["status"] == "succeeded"
    assert (
        await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name=role,
            enabled=True,
            default_options={},
        )
    ).status_code == 200
    assert (
        await write(
            api, "settings/update", **await settings_body(api, **{role: 1})
        )
    ).status_code == 200
    defaults = (await api.get("/admin/ai/state")).json()["settings"]["defaults"]
    assert defaults == dict(student=None, mentor=None, analysis=None) | {
        role: dict(model_config_id=1, available=True)
    }


async def test_role_references_can_belong_to_different_providers(
    data, api, verified_student
):
    for provider in ("anthropic", "google"):
        assert (
            await write(
                api,
                "models",
                provider=provider,
                model_id="manual-model",
                display_name=provider,
            )
        ).status_code == 200
    # Unavailable references from different providers must still round-trip.
    async with data.engine.begin() as db:
        await db.execute(
            text(
                "UPDATE app_setting SET student_model_config_id=1, mentor_model_config_id=2, analysis_model_config_id=3 WHERE id=1"
            )
        )
    state = (await api.get("/admin/ai/state")).json()
    assert [model["provider"] for model in state["models"]] == [
        "openai",
        "anthropic",
        "google",
    ]
    assert state["settings"]["defaults"] == dict(
        student=dict(model_config_id=1, available=True),
        mentor=dict(model_config_id=2, available=False),
        analysis=dict(model_config_id=3, available=False),
    )
    body = await settings_body(api)
    body["timeouts"]["connect"] = 7
    assert (await write(api, "settings/update", **body)).status_code == 200
    saved = (await api.get("/admin/ai/state")).json()["settings"]
    assert saved["defaults"] == state["settings"]["defaults"]
    assert saved["timeouts"]["connect"] == 7
    assert (
        await write(
            api,
            "settings/update",
            **await settings_body(api, mentor=None, analysis=None),
        )
    ).status_code == 200
    assert (
        await write(
            api, "settings/update", **await settings_body(api, mentor=2)
        )
    ).status_code == 422
