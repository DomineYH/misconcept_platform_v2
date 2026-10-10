"""Published and converted scenarios execute their native student snapshot."""

import copy
from uuid import uuid4

import httpx2
import pytest
from sqlalchemy import select
from test_lesson_snapshots import start
from test_scenario_api import login
from test_scenario_conversion import effective, legacy
from test_scenario_drafts import post
from test_scenario_publication import api, draft, publishable
from test_student_generation import frames
from test_student_probe import response_body, sdk_transport, sse

from src.models import GenerationRun, Session
from src.services.scenario_conversion import (
    apply_manifest,
    capture_archive,
    conversion_manifest,
)

__all__ = ["api", "draft", "publishable", "effective", "legacy"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def upstream_student(monkeypatch):
    async def upstream(request, body):
        content = sse(
            "response.output_text.delta",
            delta="Answer",
            sequence_number=0,
            item_id="m",
            output_index=0,
            content_index=0,
        )
        content += sse(
            "response.completed",
            response=response_body("Answer"),
            sequence_number=1,
        )
        return httpx2.Response(
            200, headers={"content-type": "text/event-stream"}, content=content
        )

    return sdk_transport(monkeypatch, upstream, budget=900)


@pytest.mark.parametrize("entry", ["api", "detail"])
async def test_new_lesson_executes_frozen_student_after_scenario_edit(
    data, api, publishable, monkeypatch, entry
):
    publishable["config"]["student"][
        "behavior_instruction"
    ] = 'Ask {x}, use {"answer":1} literally'
    created = await post(api, "/admin/scenarios", publishable)
    assert created.status_code == 201
    scenario_id = created.json()["id"]
    login(api, data.owner)
    await api.get("/scenarios")
    response, session_id = await start(api, scenario_id, entry)
    assert response.status_code == (201 if entry == "api" else 200)
    login(api, data.admin)
    await api.get("/admin/scenarios")
    updated = copy.deepcopy(publishable)
    updated.update(expected_version=1, action="save_draft")
    updated["config"]["student"]["behavior_instruction"] = "CHANGED INSTRUCTION"
    assert (
        await post(api, f"/admin/scenarios/{scenario_id}/update", updated)
    ).status_code == 200
    clients, calls = upstream_student(monkeypatch)
    login(api, data.owner)
    await api.get("/scenarios")
    result = await post(
        api,
        f"/sessions/{session_id}/turns/stream",
        dict(request_id=str(uuid4()), content="Why?"),
    )
    assert frames(result)[-1][0] == "output.completed", result.text
    assert 'Ask {x}, use {"answer":1} literally' in calls[0]["instructions"]
    assert "CHANGED INSTRUCTION" not in calls[0]["instructions"]
    assert "존댓말" not in calls[0]["instructions"]
    assert all(sdk.is_closed() for sdk in clients)
    async with data.factory() as db:
        session = await db.get(Session, session_id)
        run = (
            await db.scalars(
                select(GenerationRun).where(
                    GenerationRun.session_id == session_id
                )
            )
        ).one()
        assert run.config_hash == session.config_hash
        assert session.source_scenario_version == 1


async def test_converted_student_executes_copied_base_and_literal_template_text(
    data, api, publishable, legacy, effective, monkeypatch
):
    async with data.factory() as db:
        archive = await capture_archive(db)
        manifest = await conversion_manifest(
            db, archive, effective, "private/archive.json"
        )
        await apply_manifest(db, archive, manifest)
        await db.commit()
    path = f"/admin/scenarios/{data.scenario.id}"
    saved = (await api.get(path)).json()
    instruction = "BASE: 존댓말, 되묻기 금지\n\nTest: PRIVATE profile {prompt}; PRIVATE misconception; {literal}"
    assert saved["config"]["student"]["behavior_instruction"] == instruction
    body = {
        key: saved[key]
        for key in (
            "title",
            "subject",
            "target_grade",
            "groups",
            "config_schema_version",
            "config",
        )
    }
    body.update(expected_version=saved["config_version"], action="save_draft")
    body["config"]["problem"]["learning_objective"] = "Compare fractions"
    body["config"]["mentor"]["mode"] = "off"
    for field in ("context", "expected_understanding", "instruction"):
        body["config"]["analysis"][field] = publishable["config"]["analysis"][
            field
        ]
    body["config"]["analysis"]["rubric"][1][
        "criteria"
    ] = "Recall the fraction relationship"
    repaired = await post(api, path + "/update", body)
    assert repaired.status_code == 200, repaired.text
    body.update(
        expected_version=repaired.json()["version"],
        action="publish",
        acknowledge_review=True,
    )
    published = await post(api, path + "/update", body)
    assert published.status_code == 200, published.text
    clients, calls = upstream_student(monkeypatch)
    login(api, data.owner)
    await api.get("/scenarios")
    response, session_id = await start(api, data.scenario.id, "api")
    assert response.status_code == 201, response.text
    result = await post(
        api,
        f"/sessions/{session_id}/turns/stream",
        dict(request_id=str(uuid4()), content="Why?"),
    )
    assert frames(result)[-1][0] == "output.completed", result.text
    assert instruction in calls[0]["instructions"]
    assert calls[0]["reasoning"] == {"effort": "medium"}
    assert len(calls) == 1 and all(sdk.is_closed() for sdk in clients)
