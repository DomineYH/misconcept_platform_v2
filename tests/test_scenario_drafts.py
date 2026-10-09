"""Native drafts through authenticated HTTP, CSRF and migrated SQLite."""

import asyncio
import copy
import json
import re
from pathlib import Path

import httpx
import pytest
from starlette_csrf import CSRFMiddleware
from test_scenario_api import login

from src.api.dependencies import get_db_session
from src.config import config
from src.db.migrations import migrate
from src.main import app

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


@pytest.fixture
def draft():
    return json.loads(Path("tests/fixtures/s2_draft.json").read_text())


@pytest.fixture
async def api(data, monkeypatch):
    monkeypatch.setattr(migrate, "engine", data.engine)
    await migrate.run_all_migrations(through=30)
    from legacy_models import native_writer_defaults

    native_writer_defaults(data.engine.url.database)

    async def database():
        async with data.factory() as db:
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
        ) as client:
            login(client, data.admin)
            await client.get("/admin/scenarios")
            yield client
    finally:
        app.dependency_overrides.clear()


async def post(api, path, body):
    return await api.post(
        path,
        content=json.dumps(body),
        headers={
            "content-type": "application/json",
            "x-csrf-token": api.cookies["csrftoken"],
        },
    )


async def test_template_free_draft_round_trip_and_hidden_values(
    data, api, draft
):
    draft["groups"] = [data.owner.group_id]
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201, created.text
    saved = created.json()
    assert (saved["version"], saved["status"]) == (1, "draft")
    path = f"/admin/scenarios/{saved['id']}"
    retrieved = await api.get(path)
    assert retrieved.status_code == 200
    assert retrieved.json()["config"] == draft["config"]
    assert retrieved.json()["groups"] == draft["groups"]
    assert retrieved.json()["review_required"] is False
    draft["expected_version"] = 1
    draft["title"] = "재편집"
    draft["config"]["mentor"]["mode"] = "manual"
    draft["config"]["analysis"]["classification_enabled"] = True
    updated = await post(api, path + "/update", draft)
    assert updated.status_code == 200, updated.text
    assert updated.json()["version"] == 2
    reopened = (await api.get(path)).json()
    assert reopened["config"] == draft["config"]
    assert reopened["title"] == "재편집"
    assert reopened["config_version"] == 2
    assert not set(reopened["config"]) & {"id", "title", "groups", "status"}
    editor = await api.get(path + "/edit")
    assert editor.status_code == 200
    assert 'id="scenario-form"' in editor.text
    bootstrap = json.loads(
        re.search(
            r'<script id="scenario-editor-data" type="application/json">(.*?)</script>',
            editor.text,
            re.S,
        ).group(1)
    )
    assert bootstrap["config"] == draft["config"]


@pytest.mark.parametrize(
    "path,value",
    [
        ("title", " \n\t"),
        ("title", "x" * 201),
        ("title", True),
        ("is_active", None),
        ("is_active", 1),
        ("is_active", "true"),
        ("subject", "x" * 101),
        ("target_grade", "x" * 101),
        ("config_schema_version", 2),
        ("config_schema_version", True),
        ("groups", ["1"]),
        ("groups", [True]),
        ("groups", [999]),
        ("groups", [1, 1]),
        ("config.student.name", "x" * 51),
        ("config.student.name", 3),
        ("config.student.internal_profile", "x" * 50001),
        ("config.problem.public_text", "\ud800"),
        ("config.runtime.context_turn_limit", "10"),
        ("config.runtime.context_turn_limit", True),
        ("config.runtime.context_turn_limit", 1.5),
        ("config.runtime.context_turn_limit", 0),
        ("config.runtime.context_turn_limit", 101),
        ("config.mentor.mode", "enabled"),
        ("config.mentor.intervention_policy.start_turn", 0),
        ("config.mentor.intervention_policy.min_interval_turns", 1001),
        ("config.mentor.intervention_policy.max_interventions", 11),
        ("config.analysis.classification_enabled", 0),
        ("config.analysis.rubric", [{}] * 21),
        ("config.analysis.rubric", [{"id": "x" * 65}]),
        ("config.analysis.rubric", [{"name": "x" * 101}]),
        ("config.analysis.rubric", [{"level": "medium"}]),
        ("config.analysis.rubric", [{"unexpected": "PRIVATE-INPUT"}]),
        ("config.status", "draft"),
        ("config.title", "duplicate"),
        ("config.mentor.enabled", False),
        ("config.runtime.role_timeouts", {}),
        ("review_required", False),
        ("conversion_provenance_json", {}),
        ("video_url", "PRIVATE-INPUT"),
        ("unexpected", "PRIVATE-INPUT"),
    ],
)
async def test_invalid_draft_is_safe_and_atomic(data, api, draft, path, value):
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    endpoint = f"/admin/scenarios/{created.json()['id']}"
    before = (await api.get(endpoint)).json()
    draft["expected_version"] = 1
    draft["title"] = "Failure must not change title"
    draft["groups"] = [data.owner.group_id]
    target = draft
    parts = path.split(".")
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value
    rejected = await post(api, endpoint + "/update", draft)
    assert rejected.status_code == 422, rejected.text
    assert "PRIVATE-INPUT" not in rejected.text
    assert all(
        set(error) == {"path", "code", "message"}
        for error in rejected.json()["detail"]
    )
    assert (await api.get(endpoint)).json() == before


@pytest.fixture
async def selected_model(api):
    registered = await post(
        api,
        "/admin/ai/models",
        {
            "provider": "openai",
            "model_id": "gpt-5-mini",
            "display_name": "Unavailable student",
        },
    )
    assert registered.status_code == 200
    page = await api.get("/admin/scenarios/new")
    bootstrap = json.loads(
        re.search(
            r'<script id="scenario-editor-data" type="application/json">(.*?)</script>',
            page.text,
            re.S,
        ).group(1)
    )
    model = bootstrap["model_choices"][0]
    assert model["enabled"] is False
    assert model["connection_available"] is False
    return dict(
        model_config_id=model["id"],
        provider_connection_id=model["provider_connection_id"],
        provider=model["provider"],
        model_id=model["model_id"],
        options={"max_output_tokens": 900, "reasoning": {"effort": "medium"}},
    )


@pytest.mark.parametrize(
    "options",
    [
        {"temperature": "hot"},
        {"temperature": True},
        {"temperature": None},
        {"max_output_tokens": "900"},
        {"max_output_tokens": True},
        {"max_output_tokens": 0},
        {"reasoning": {"effort": 1}},
        {"reasoning": {"effort": "medium", "secret": "PRIVATE-INPUT"}},
        {"arbitrary": "PRIVATE-INPUT"},
        {"thinking": {"budget": 10}},
    ],
)
async def test_model_options_structure_is_validated_even_when_hidden(
    api, draft, selected_model, options
):
    draft["config"]["mentor"]["resolved_model_config"] = selected_model
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    path = f"/admin/scenarios/{created.json()['id']}"
    before = (await api.get(path)).json()
    draft["expected_version"] = 1
    selected_model["options"] = options
    rejected = await post(api, path + "/update", draft)
    assert rejected.status_code == 422, rejected.text
    assert "PRIVATE-INPUT" not in rejected.text
    assert (await api.get(path)).json() == before


@pytest.mark.parametrize("entry", ["api", "detail"])
async def test_draft_never_starts_new_lesson(data, api, draft, entry):
    draft["groups"] = [data.owner.group_id]
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    sid = created.json()["id"]
    for user in (data.owner, data.admin):
        login(api, user)
        await api.get("/login")
        response = (
            await post(api, "/sessions", {"scenario_id": sid})
            if entry == "api"
            else await api.get(f"/scenarios/{sid}")
        )
        assert response.status_code == 400
        assert response.json()["detail"]["code"] == "scenario_not_published"
    login(api, data.owner)
    listing = await api.get("/scenarios")
    assert draft["title"] not in listing.text
    assert "unsafe()" not in listing.text
    login(api, data.admin)
    history = await api.get("/admin/sessions")
    assert [row["id"] for row in history.json()["sessions"]] == [
        data.session.id
    ]


async def test_same_revision_has_one_winner_and_no_partial_group_change(
    data, api, draft
):
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    path = f"/admin/scenarios/{created.json()['id']}"
    draft["expected_version"] = 1
    second = copy.deepcopy(draft)
    draft.update(title="First", groups=[data.owner.group_id])
    second.update(title="Second", groups=[])
    results = await asyncio.gather(
        post(api, path + "/update", draft), post(api, path + "/update", second)
    )
    assert sorted(r.status_code for r in results) == [200, 409]
    winner = draft if results[0].status_code == 200 else second
    saved = (await api.get(path)).json()
    assert (saved["title"], saved["groups"], saved["config_version"]) == (
        winner["title"],
        winner["groups"],
        2,
    )
    conflict = next(r for r in results if r.status_code == 409)
    assert conflict.json()["detail"] == {
        "code": "version_conflict",
        "current_version": 2,
    }
    assert (
        await post(api, path + "/update", {"title": "Legacy bypass"})
    ).status_code == 422
    for value in (None, True, "2", 0):
        invalid = copy.deepcopy(draft)
        invalid["expected_version"] = value
        assert (await post(api, path + "/update", invalid)).status_code == 422
    del draft["expected_version"]
    assert (await post(api, path + "/update", draft)).status_code == 422
    assert (await api.get(path)).json() == saved


async def test_admin_and_csrf_required_for_all_draft_reads_and_writes(
    data, api, draft
):
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    path = f"/admin/scenarios/{created.json()['id']}"
    before = (await api.get(path)).json()
    draft["expected_version"] = 1
    for method, endpoint, body in (
        ("GET", path, None),
        ("GET", path + "/edit", None),
        ("GET", "/admin/scenarios/new", None),
        ("POST", "/admin/scenarios", draft),
        ("POST", path + "/update", draft),
    ):
        login(api, data.owner)
        await api.get("/login")
        result = (
            await api.get(endpoint)
            if method == "GET"
            else await post(api, endpoint, body)
        )
        assert result.status_code == 403
        assert "unsafe()" not in result.text
    login(api, data.admin)
    await api.get(path)
    assert (await api.post(path + "/update", json=draft)).status_code == 403
    assert (await api.get(path)).json() == before
    api.cookies.clear()
    assert (await api.get(path)).status_code == 303
    login(api, data.admin)
    assert (await api.get("/admin/scenarios/99999")).status_code == 404


async def test_utf8_request_limit_checks_actual_stream_and_safe_json_errors(
    api, draft
):
    encoded = json.dumps(draft, ensure_ascii=False).encode()
    body = encoded + b" " * (1024 * 1024 - len(encoded))
    headers = {
        "content-type": "application/json",
        "x-csrf-token": api.cookies["csrftoken"],
    }
    assert (
        await api.post("/admin/scenarios", content=body, headers=headers)
    ).status_code == 201

    async def chunks():
        for start in range(0, len(body), 4096):
            yield body[start : start + 4096]
        yield b" "

    too_large = await api.post(
        "/admin/scenarios",
        content=chunks(),
        headers={**headers, "content-length": "1"},
    )
    assert too_large.status_code == 422
    assert too_large.json()["detail"][0]["code"] == "request_too_large"
    invalid = await api.post(
        "/admin/scenarios",
        content=b'{"config":"PRIVATE-INPUT",',
        headers=headers,
    )
    assert invalid.status_code == 422
    assert "PRIVATE-INPUT" not in invalid.text


@pytest.mark.parametrize(
    "field,value",
    [
        ("model_config_id", 999),
        ("provider_connection_id", 2),
        ("provider", "google"),
        ("model_id", "invented"),
    ],
)
async def test_submitted_model_identity_cannot_bypass_registration(
    api, draft, selected_model, field, value
):
    draft["config"]["student"]["resolved_model_config"] = selected_model
    selected_model[field] = value
    rejected = await post(api, "/admin/scenarios", draft)
    assert rejected.status_code == 422


async def test_unavailable_model_and_saved_options_survive_default_changes(
    api, draft, selected_model
):
    draft["config"]["student"]["resolved_model_config"] = selected_model
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    path = f"/admin/scenarios/{created.json()['id']}"
    changed = await post(
        api,
        f"/admin/ai/models/{selected_model['model_config_id']}/update",
        {
            "expected_version": 1,
            "display_name": "Changed defaults",
            "enabled": False,
            "default_options": {
                "max_output_tokens": 1024,
                "reasoning": {"effort": "high"},
            },
        },
    )
    assert changed.status_code == 200
    reopened = (await api.get(path)).json()
    assert (
        reopened["config"]["student"]["resolved_model_config"] == selected_model
    )
    draft["expected_version"] = 1
    assert (await post(api, path + "/update", draft)).status_code == 200
    assert (await api.get(path)).json()["config"]["student"][
        "resolved_model_config"
    ] == selected_model


async def test_native_delete_requires_revision_and_hides_deleted_draft(
    api, draft
):
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    path = f"/admin/scenarios/{created.json()['id']}"
    before = (await api.get(path)).json()
    assert (await post(api, path + "/delete", {})).status_code == 422
    assert (
        await post(api, path + "/delete", {"expected_version": 2})
    ).status_code == 409
    assert (await api.get(path)).json() == before
    deleted = await post(api, path + "/delete", {"expected_version": 1})
    assert deleted.status_code == 200
    assert deleted.json()["version"] == 2
    assert (await api.get(path)).status_code == 404


async def test_invalid_unicode_field_name_returns_safe_validation_error(
    api, draft
):
    draft["\ud800"] = "PRIVATE-INPUT"
    result = await post(api, "/admin/scenarios", draft)
    assert result.status_code == 422
    assert "PRIVATE-INPUT" not in result.text


async def test_omitted_activation_preserves_saved_inactive_draft(api, draft):
    draft["is_active"] = False
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201
    path = f"/admin/scenarios/{created.json()['id']}"
    draft.pop("is_active")
    draft["expected_version"] = 1
    assert (await post(api, path + "/update", draft)).status_code == 200
    assert (await api.get(path)).json()["is_active"] is False


@pytest.mark.parametrize(
    "provider,model_id,options",
    [
        (
            "anthropic",
            "claude-sonnet-4-6",
            {
                "max_output_tokens": 2048,
                "thinking": {"type": "enabled", "budget_tokens": 1024},
            },
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            {
                "thinking": {"type": "adaptive"},
                "output_config": {"effort": "high"},
            },
        ),
        (
            "anthropic",
            "claude-sonnet-4-6",
            {"thinking": {"type": "disabled"}, "temperature": 0.7},
        ),
        (
            "google",
            "gemini-2.5-flash",
            {"thinking": {"budget": -1}, "temperature": 0.7},
        ),
        ("google", "gemini-2.5-flash", {"thinking": {"level": "high"}}),
        (
            "openai",
            "direct-unavailable-model",
            {"reasoning": {"effort": "high"}, "max_output_tokens": 900},
        ),
    ],
)
async def test_provider_option_shapes_round_trip_without_draft_capability_gate(
    api, draft, provider, model_id, options
):
    registered = await post(
        api,
        "/admin/ai/models",
        {
            "provider": provider,
            "model_id": model_id,
            "display_name": "Saved unavailable model",
        },
    )
    assert registered.status_code == 200
    page = await api.get("/admin/scenarios/new")
    bootstrap = json.loads(
        re.search(
            r'<script id="scenario-editor-data" type="application/json">(.*?)</script>',
            page.text,
            re.S,
        ).group(1)
    )
    model = bootstrap["model_choices"][0]
    selected = dict(
        model_config_id=model["id"],
        provider_connection_id=model["provider_connection_id"],
        provider=provider,
        model_id=model_id,
        options=options,
    )
    draft["config"]["mentor"]["resolved_model_config"] = selected
    created = await post(api, "/admin/scenarios", draft)
    assert created.status_code == 201, created.text
    saved = (await api.get(f"/admin/scenarios/{created.json()['id']}")).json()
    assert saved["config"]["mentor"]["resolved_model_config"] == selected
