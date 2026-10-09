"""Retired administration is unreachable through authenticated HTTP."""

import pytest
from test_scenario_drafts import api as draft_api
from test_scenario_drafts import draft as draft_fixture
from test_scenario_drafts import post

api = draft_api
draft = draft_fixture

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.mark.parametrize(
    "path,method",
    [
        ("/admin/frameworks", "GET"),
        ("/admin/frameworks", "POST"),
        ("/admin/frameworks/1/update", "POST"),
        ("/admin/frameworks/1/delete", "POST"),
        ("/admin/prompts-page", "GET"),
        ("/admin/prompts", "GET"),
        ("/admin/prompts", "POST"),
        ("/admin/prompts/1/update", "POST"),
        ("/admin/prompts/1/delete", "POST"),
    ],
)
async def test_retired_management_routes_are_absent(api, path, method):
    response = (
        await api.get(path) if method == "GET" else await post(api, path, {})
    )
    assert response.status_code == 404


async def test_legacy_scenario_writes_are_rejected_without_changes(data, api):
    payload = {
        "title": "Old write",
        "prompt": "Old internal prompt",
        "student_profile": "Old profile",
        "framework_id": data.framework.id,
        "student_template_id": 1,
    }
    assert (await post(api, "/admin/scenarios", payload)).status_code == 422
    assert (
        await post(
            api, f"/admin/scenarios/{data.scenario.id}/update", {"is_active": 0}
        )
    ).status_code == 422
    assert (
        await post(api, f"/admin/scenarios/{data.scenario.id}/delete", {})
    ).status_code == 422
    listing = await api.get("/admin/scenarios")
    assert "Old write" not in listing.text
    assert "Test" in listing.text


async def test_admin_reads_list_counts_and_status_without_legacy_joins(
    data, api, draft
):
    from sqlalchemy import event

    from src.models import Session

    draft["title"] = "Unified <script>unsafe()</script>"
    draft["groups"] = [data.owner.group_id]
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    scenario_id = created.json()["id"]
    async with data.factory() as db:
        db.add_all(
            [
                Session(scenario_id=scenario_id, teacher_id=data.owner.id),
                Session(scenario_id=scenario_id, teacher_id=data.owner.id),
            ]
        )
        await db.commit()
    statements = []

    def capture(conn, cursor, statement, params, context, many):
        statements.append(statement.lower())

    event.listen(data.engine.sync_engine, "before_cursor_execute", capture)
    try:
        listing = await api.get("/admin/scenarios")
    finally:
        event.remove(data.engine.sync_engine, "before_cursor_execute", capture)
    assert listing.status_code == 200
    assert "세션 2개" in listing.text and "세션 1개" in listing.text
    assert "Unified &lt;script&gt;unsafe()&lt;/script&gt;" in listing.text
    assert "초안" in listing.text and "test" in listing.text
    assert "내부 전용" not in listing.text
    assert 'data-version="1"' in listing.text
    assert 'href="/admin/scenarios/new"' in listing.text
    assert (
        'href="/admin/scenarios/' + str(scenario_id) + '/edit"' in listing.text
    )
    assert not any(
        "analysis_framework" in sql or "prompt_template" in sql
        for sql in statements
    )
    counts = [sql for sql in statements if "count(session.id)" in sql]
    assert len(counts) == 1 and "group by session.scenario_id" in counts[0]
    dashboard = await api.get("/admin")
    assert (
        "/admin/frameworks" not in dashboard.text
        and "/admin/prompts" not in dashboard.text
    )
    groups = await api.get("/admin/groups")
    assert groups.status_code == 200 and "test" in groups.text
    stats = await api.get("/admin/stats")
    assert stats.status_code == 200
    assert stats.json()["total_sessions"] == 3
    assert stats.json()["active_sessions"] == 2


async def test_connection_impact_lists_roles_states_and_disabled_mentor(
    data, api, draft
):
    import copy

    from src.models import ModelConfig, ProviderConnection, Scenario

    async with data.factory() as db:
        connection = await db.get(ProviderConnection, 1)
        model = ModelConfig(
            provider_connection_id=connection.id,
            model_id="gpt-5-mini",
            display_name="Draft model",
        )
        db.add(model)
        await db.flush()
        selection = dict(
            model_config_id=model.id,
            provider_connection_id=connection.id,
            provider="openai",
            model_id="gpt-5-mini",
            options={
                "max_output_tokens": 900,
                "reasoning": {"effort": "medium"},
            },
        )
        config = copy.deepcopy(draft["config"])
        for role in ("student", "mentor", "analysis"):
            config[role]["resolved_model_config"] = selection
        db.add_all(
            [
                Scenario(
                    title="Draft impact", status="draft", config_json=config
                ),
                Scenario(
                    title="Published impact",
                    status="published",
                    config_json=config,
                ),
                Scenario(
                    title="Inactive impact",
                    status="published",
                    is_active=0,
                    config_json=config,
                ),
            ]
        )
        await db.commit()
    response = await api.get("/admin/ai/state")
    assert response.status_code == 200
    impact = next(
        p["impact"]
        for p in response.json()["providers"]
        if p["provider"] == "openai"
    )
    text = "\n".join(impact)
    for title in ("Draft impact", "Published impact", "Inactive impact"):
        rows = [row for row in impact if title in row]
        assert len(rows) == 3
        assert any("student" in row for row in rows)
        assert any("analysis" in row for row in rows)
        assert any("mentor" in row and "실행 제외" in row for row in rows)
    assert "초안" in text and "게시" in text and "비활성" in text
    assert all(
        "새 수업 실행 제외" in row or "멘토 off" in row
        for row in impact
        if "Draft impact" in row or "Inactive impact" in row
    )
    assert all(
        "새 수업 실행 참조" in row
        for row in impact
        if "Published impact" in row and "mentor" not in row
    )
    assert "숨긴 조건" not in response.text and "내부 전용" not in response.text
    google = next(
        p["impact"]
        for p in response.json()["providers"]
        if p["provider"] == "google"
    )
    assert not any("impact" in row for row in google)


async def test_native_delete_preserves_session_rows_and_current_permissions(
    data, api, draft
):
    from sqlalchemy import select
    from test_scenario_api import login

    from src.models import Message, Scenario, Session

    created = await post(api, "/admin/scenarios", draft)
    scenario_id = created.json()["id"]
    async with data.factory() as db:
        session = Session(scenario_id=scenario_id, teacher_id=data.owner.id)
        db.add(session)
        await db.flush()
        session_id = session.id
        db.add(
            Message(
                session_id=session_id,
                role="teacher",
                content="Preserved original",
            )
        )
        await db.commit()
    login(api, data.owner)
    await api.get("/scenarios")
    assert (
        await post(
            api,
            f"/admin/scenarios/{scenario_id}/delete",
            {"expected_version": 1},
        )
    ).status_code == 403
    login(api, data.admin)
    await api.get("/admin/scenarios")
    deleted = await post(
        api, f"/admin/scenarios/{scenario_id}/delete", {"expected_version": 1}
    )
    assert deleted.status_code == 200 and deleted.json()["version"] == 2
    assert (await api.get(f"/admin/scenarios/{scenario_id}")).status_code == 404
    async with data.factory() as db:
        assert (await db.get(Scenario, scenario_id)).deleted_at is not None
        preserved = await db.get(Session, session_id)
        assert (
            preserved.deleted_at is not None
            and preserved.teacher_id == data.owner.id
        )
        assert (
            await db.scalar(
                select(Message).where(Message.session_id == session_id)
            )
        ).content == "Preserved original"


@pytest.mark.parametrize(
    "field",
    [
        "framework_id",
        "student_template_id",
        "tutor_template_id",
        "video_url",
        "video_transcript",
    ],
)
@pytest.mark.parametrize("operation", ["create", "update"])
async def test_native_contract_rejects_retired_fields_atomically(
    api, draft, field, operation
):
    import copy

    body = copy.deepcopy(draft)
    path = "/admin/scenarios"
    if operation == "update":
        created = await post(api, path, draft)
        assert created.status_code == 201
        path += f"/{created.json()['id']}"
        before = (await api.get(path)).json()
        body["expected_version"] = 1
    body[field] = None
    rejected = await post(
        api, path + ("/update" if operation == "update" else ""), body
    )
    assert rejected.status_code == 422
    assert any(
        error["path"] == field and error["code"] == "extra_forbidden"
        for error in rejected.json()["detail"]
    )
    if operation == "update":
        assert (await api.get(path)).json() == before
