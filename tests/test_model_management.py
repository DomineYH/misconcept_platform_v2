"""Administrator model/settings HTTP contract on migrated SQLite."""

import pytest
from sqlalchemy import select
from test_provider_connections import KEY, post
from test_provider_connections import api as provider_api

from src.models.model_config import ModelConfig

api = provider_api

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


async def write(api, path, **body):
    return await api.post(
        f"/admin/ai/{path}",
        json=body,
        headers={"x-csrf-token": api.cookies["csrftoken"]},
    )


async def test_register_known_unknown_models_and_safe_initial_settings(
    data, api
):
    snapshot = (await api.get("/admin/ai/state")).json()
    assert snapshot["models_available"] is True
    assert snapshot["probes_available"] is False
    assert snapshot["settings"]["defaults"] == dict(
        student=None, mentor=None, analysis=None
    )
    assert snapshot["settings"]["settings_version"] == 1
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="  gpt-5-mini  ",
            display_name=" Student ",
        )
    ).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="unlisted-custom",
            display_name="Unknown",
        )
    ).status_code == 200
    models = (await api.get("/admin/ai/state")).json()["models"]
    known, unknown = models
    assert (
        known["model_id"] == "gpt-5-mini" and known["display_name"] == "Student"
    )
    assert known["enabled"] is False and known["config_version"] == 1
    assert all(
        v["status"] == "unverified"
        for v in known["verification_state"].values()
    )
    assert (
        known["capabilities"]["text"]
        and known["capabilities"]["streaming"]
        and known["capabilities"]["structured"]
    )
    assert known["capabilities"]["checked_at"] == "2026-10-09"
    assert known["capabilities"]["sources"]
    assert unknown["capabilities"] is None and unknown["default_options"] == {}
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5-mini",
            display_name="Duplicate",
        )
    ).status_code == 409
    assert (
        await api.delete(
            f"/admin/ai/models/{known['id']}",
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
    ).status_code in (404, 405)


async def test_model_edit_options_and_conflict(data, api):
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5.2",
            display_name="Reasoning",
        )
    ).status_code == 200
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    options = {
        "temperature": 0,
        "reasoning": {"effort": "none"},
        "max_output_tokens": 128000,
    }
    body = dict(
        expected_version=1,
        display_name=" Edited ",
        enabled=False,
        default_options=options,
    )
    assert (
        await write(api, f"models/{model['id']}/update", **body)
    ).status_code == 200
    saved = (await api.get("/admin/ai/state")).json()["models"][0]
    assert saved["default_options"] == options
    assert saved["display_name"] == "Edited" and saved["config_version"] == 2
    assert (
        await write(api, f"models/{model['id']}/update", **body)
    ).status_code == 409
    assert (
        await api.post(
            f"/admin/ai/models/{model['id']}/probes",
            json={},
            headers={"x-csrf-token": api.cookies["csrftoken"]},
        )
    ).status_code == 404


async def test_model_activation_requires_current_role_verification(data, api):
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5-mini",
            display_name="Student",
        )
    ).status_code == 200
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    body = dict(
        expected_version=1,
        display_name="Student",
        enabled=True,
        default_options={},
    )
    assert (
        await write(api, f"models/{model['id']}/update", **body)
    ).status_code == 422

    async with data.factory() as db:
        stored = await db.scalar(
            select(ModelConfig).where(ModelConfig.id == model["id"])
        )
        stored.verification_state = {
            "student": dict(
                status="succeeded",
                credential_revision=0,
                connection_version=2,
                capability_definition_version=model["capabilities"][
                    "definition_version"
                ],
                role_contract_version="s1-v1",
            ),
            "mentor": {"status": "unverified"},
            "analysis": {"status": "unverified"},
        }
        await db.commit()
    assert (
        await write(api, f"models/{model['id']}/update", **body)
    ).status_code == 422
    public = (await api.get("/admin/ai/state")).json()["models"][0]
    assert public["verification_state"]["student"]["status"] == "stale"
    async with data.factory() as db:
        stored = await db.get(ModelConfig, model["id"])
        verified = dict(stored.verification_state)
        verified["student"] = {**verified["student"], "credential_revision": 1}
        stored.verification_state = verified
        await db.commit()
    assert (
        await write(api, f"models/{model['id']}/update", **body)
    ).status_code == 200
    assert (await post(api, "enabled", 2, enabled=False)).status_code == 200
    public = (await api.get("/admin/ai/state")).json()["models"][0]
    assert public["verification_state"]["student"]["status"] == "stale"
    assert (await post(api, "enabled", 3, enabled=True)).status_code == 200
    body["expected_version"] = 2
    assert (
        await write(api, f"models/{model['id']}/update", **body)
    ).status_code == 422


async def test_settings_validation_versions_and_empty_defaults(data, api):
    settings = (await api.get("/admin/ai/state")).json()["settings"]
    body = dict(
        expected_version=1,
        defaults=dict(student=None, mentor=None, analysis=None),
        limits={**settings["limits"], "total": 10},
        timeouts={**settings["timeouts"], "connect": 7},
    )
    assert (await write(api, "settings/update", **body)).status_code == 200
    saved = (await api.get("/admin/ai/state")).json()["settings"]
    assert saved["settings_version"] == 2 and saved["limits"]["total"] == 10
    assert (
        saved["timeouts"]["connect"] == 7
        and saved["defaults"] == body["defaults"]
    )
    assert (await write(api, "settings/update", **body)).status_code == 409
    body["expected_version"] = 2
    for invalid in [
        {"limits": {**body["limits"], "admin": 4}},
        {"limits": {**body["limits"], "total": 3, "admin": 3}},
        {"limits": {**body["limits"], "openai": 1}},
        {"limits": {**body["limits"], "total": True}},
        {"timeouts": {**body["timeouts"], "connect": 0}},
        {"timeouts": {**body["timeouts"], "student_first_output": 181}},
        {"timeouts": {**body["timeouts"], "mentor_first_output": 181}},
        {"timeouts": {**body["timeouts"], "connect": "5"}},
        {"defaults": {**body["defaults"], "student": 9999}},
        {"defaults": {**body["defaults"], "teacher": None}},
        {"prompt": "PRIVATE-SENTINEL"},
    ]:
        response = await write(api, "settings/update", **{**body, **invalid})
        assert (
            response.status_code == 422
            and "PRIVATE-SENTINEL" not in response.text
        )
        assert (await api.get("/admin/ai/state")).json()["settings"] == saved


async def test_options_and_registration_trust_boundaries(data, api):
    import asyncio
    import json

    from test_scenario_api import login

    from src.services.model_capabilities import validate_model_and_options

    with pytest.raises(ValueError, match="capability_definition_required"):
        validate_model_and_options("openai", "custom", {})
    for model_id in ("gpt-5-mini", "gpt-5.2", "custom"):
        assert (
            await write(
                api,
                "models",
                provider="openai",
                model_id=model_id,
                display_name=model_id,
            )
        ).status_code == 200
    models = (await api.get("/admin/ai/state")).json()["models"]
    for index, options in [
        (0, {"temperature": 0}),
        (0, {"reasoning": {"effort": "none"}}),
        (1, {"temperature": 1, "reasoning": {"effort": "high"}}),
        (1, {"reasoning": {"effort": "minimal"}}),
        (1, {"max_output_tokens": 0}),
        (1, {"max_output_tokens": 128001}),
        (1, {"max_output_tokens": True}),
        (1, {"temperature": True}),
        (1, {"temperature": 3}),
        (1, {"temperature": 10**400}),
        (1, {"temperature": "0"}),
        (1, {"reasoning": {"effort": "none", "budget_tokens": 1}}),
        (1, {"top_p": 0.5}),
        (2, {"max_output_tokens": 1}),
    ]:
        rejected = await write(
            api,
            f"models/{models[index]['id']}/update",
            expected_version=1,
            display_name="Unchanged",
            enabled=False,
            default_options=options,
        )
        assert rejected.status_code == 422
    for extra in [
        dict(model_id="changed"),
        dict(capabilities={"text": True}),
        dict(verification_state={"student": {"status": "succeeded"}}),
        dict(expected_version=True),
        dict(enabled="false"),
    ]:
        response = await write(
            api,
            "models/1/update",
            **{
                **dict(
                    expected_version=1,
                    display_name="Name",
                    enabled=False,
                    default_options={},
                ),
                **extra,
            },
        )
        assert response.status_code == 422
    for model_id in (" ", "id\x00tail", "id\ntail", "\ud800"):
        response = await api.post(
            "/admin/ai/models",
            content=json.dumps(
                dict(
                    provider="openai",
                    model_id=model_id,
                    display_name="PRIVATE-SENTINEL",
                )
            ),
            headers={
                "Content-Type": "application/json",
                "x-csrf-token": api.cookies["csrftoken"],
            },
        )
        assert (
            response.status_code == 422
            and "PRIVATE-SENTINEL" not in response.text
        )
    assert (await api.get("/admin/ai/state")).json()["models"] == models
    login(api, data.owner)
    await api.get("/admin/ai")
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="forbidden",
            display_name="Forbidden",
        )
    ).status_code == 403
    login(api, data.admin)
    await api.get("/admin/ai")
    assert (
        await api.post(
            "/admin/ai/models",
            json=dict(
                provider="openai", model_id="no-csrf", display_name="Forbidden"
            ),
        )
    ).status_code == 403
    race = await asyncio.gather(
        *[
            write(
                api,
                "models/2/update",
                expected_version=1,
                display_name="Race",
                enabled=False,
                default_options={},
            )
            for _ in range(2)
        ]
    )
    assert sorted(r.status_code for r in race) == [200, 409]
    assert (
        await write(
            api,
            "models/3/update",
            expected_version=1,
            display_name="Renamed unknown",
            enabled=False,
            default_options={},
        )
    ).status_code == 200


async def test_defaults_require_matching_role_and_preserve_unavailable_references(
    data, api
):
    assert (await post(api, "key", 1, api_key=KEY)).status_code == 200
    assert (
        await write(
            api,
            "models",
            provider="openai",
            model_id="gpt-5-mini",
            display_name="Student",
        )
    ).status_code == 200
    model = (await api.get("/admin/ai/state")).json()["models"][0]
    async with data.factory() as db:
        stored = await db.get(ModelConfig, model["id"])
        stored.verification_state = {
            "student": dict(
                status="succeeded",
                credential_revision=1,
                connection_version=2,
                capability_definition_version=model["capabilities"][
                    "definition_version"
                ],
                role_contract_version="s1-v1",
            ),
            "mentor": {"status": "unverified"},
            "analysis": {"status": "unverified"},
        }
        await db.commit()
    assert (
        await write(
            api,
            "models/1/update",
            expected_version=1,
            display_name="Student",
            enabled=True,
            default_options={},
        )
    ).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    settings = state["settings"]
    body = dict(
        expected_version=1,
        defaults=dict(student=None, mentor=1, analysis=None),
        limits=settings["limits"],
        timeouts=settings["timeouts"],
    )
    assert (await write(api, "settings/update", **body)).status_code == 422
    body["defaults"] = dict(student=1, mentor=None, analysis=None)
    assert (await write(api, "settings/update", **body)).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    assert state["settings"]["defaults"]["student"] == dict(
        model_config_id=1, available=True
    )
    assert any("Student" in item for item in state["providers"][0]["impact"])
    assert (await post(api, "enabled", 2, enabled=False)).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    assert state["settings"]["defaults"]["student"] == dict(
        model_config_id=1, available=False
    )
    body["expected_version"] = 2
    body["limits"] = {**body["limits"], "total": 10}
    assert (await write(api, "settings/update", **body)).status_code == 200
    assert (await post(api, "delete", 3)).status_code == 200
    state = (await api.get("/admin/ai/state")).json()
    assert (
        state["models"][0]["id"] == 1
        and state["models"][0]["display_name"] == "Student"
    )
    assert state["settings"]["defaults"]["student"] == dict(
        model_config_id=1, available=False
    )
    assert any("Student" in item for item in state["providers"][0]["impact"])
