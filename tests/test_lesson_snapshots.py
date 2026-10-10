"""Native lesson start/reuse through authenticated HTTP and temporary SQLite."""

import re

import pytest
from test_scenario_api import login
from test_scenario_drafts import draft as draft_fixture
from test_scenario_drafts import post
from test_scenario_publication import api as publication_api
from test_scenario_publication import publishable as publishable_fixture

from src.models import Session

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
api = publication_api
draft = draft_fixture
publishable = publishable_fixture


async def start(api, scenario_id, entry):
    if entry == "api":
        response = await post(api, "/sessions", {"scenario_id": scenario_id})
        return response, response.json().get("id")
    response = await api.get(f"/scenarios/{scenario_id}")
    match = re.search(r'"sessionId": (\d+)', response.text)
    return response, int(match[1]) if match else None


@pytest.mark.parametrize("entry", ["api", "detail"])
async def test_native_start_freezes_complete_config_and_public_display(
    data, api, publishable, entry
):
    publishable["config"]["student"].update(
        public_profile="PUBLIC PROFILE", internal_profile="PRIVATE PROFILE"
    )
    created = await post(api, "/admin/scenarios", publishable)
    sid = created.json()["id"]
    login(api, data.owner)
    await api.get("/scenarios")
    response, session_id = await start(api, sid, entry)
    assert response.status_code == (
        201 if entry == "api" else 200
    ), response.text
    async with data.factory() as db:
        session = await db.get(Session, session_id)
        assert session.config_snapshot_json == {
            "schema_version": 1,
            "scenario_context": {
                "title": publishable["title"],
                "subject": publishable["subject"],
                "target_grade": publishable["target_grade"],
            },
            "config": publishable["config"],
        }
        assert session.source_scenario_version == 1
        assert session.snapshot_origin == "native"
        assert session.snapshot_created_at is not None
        assert re.fullmatch(r"[0-9a-f]{64}", session.config_hash)
    chat = await api.get(f"/scenarios/{sid}")
    assert chat.status_code == 200
    assert "PUBLIC PROFILE" in chat.text
    for private in (
        "PRIVATE PROFILE",
        "resolved_model_config",
        "max_output_tokens",
        "config_snapshot_json",
        "misconception",
        "criteria",
    ):
        assert private not in chat.text


async def test_reuse_and_polling_keep_snapshot_after_scenario_edit(
    data, api, publishable
):
    from src.models import Message

    created = await post(api, "/admin/scenarios", publishable)
    sid = created.json()["id"]
    login(api, data.owner)
    await api.get("/scenarios")
    response, session_id = await start(api, sid, "detail")
    assert response.status_code == 200
    async with data.factory() as db:
        session = await db.get(Session, session_id)
        before = (
            session.config_snapshot_json,
            session.config_hash,
            session.source_scenario_version,
            session.snapshot_created_at,
        )
        db.add(
            Message(
                session_id=session_id, role="student", content="Saved answer"
            )
        )
        await db.commit()
    login(api, data.admin)
    await api.get("/admin/scenarios")
    publishable.update(
        expected_version=1, title="CHANGED TITLE", action="save_draft"
    )
    publishable["config"]["student"]["name"] = "CHANGED STUDENT"
    publishable["config"]["problem"]["public_text"] = "CHANGED PROBLEM"
    assert (
        await post(api, f"/admin/scenarios/{sid}/update", publishable)
    ).status_code == 200
    login(api, data.owner)
    await api.get("/scenarios")
    reused, reused_id = await start(api, sid, "detail")
    assert reused.status_code == 200
    assert reused_id == session_id
    for changed in ("CHANGED TITLE", "CHANGED STUDENT", "CHANGED PROBLEM"):
        assert changed not in reused.text
    assert "민수" in reused.text
    assert "Saved answer" in reused.text
    polling = await api.get(f"/sessions/{session_id}/messages/updates")
    assert "민수" in polling.text
    assert "CHANGED STUDENT" not in polling.text
    assert (await start(api, sid, "api"))[0].status_code == 400
    async with data.factory() as db:
        session = await db.get(Session, session_id)
        assert (
            session.config_snapshot_json,
            session.config_hash,
            session.source_scenario_version,
            session.snapshot_created_at,
        ) == before


async def test_native_csv_uses_frozen_title_after_edit(data, api, publishable):
    from src.models import Message

    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    started, session_id = await start(api, sid, "api")
    assert started.status_code == 201
    async with data.factory() as db:
        db.add(
            Message(
                session_id=session_id, role="student", content="Saved answer"
            )
        )
        await db.commit()
    assert (
        await post(api, f"/sessions/{session_id}/close", {})
    ).status_code == 200
    publishable.update(expected_version=1, title="EDITED CSV TITLE")
    assert (
        await post(api, f"/admin/scenarios/{sid}/update", publishable)
    ).status_code == 200
    csv_response = await api.get(f"/admin/sessions/{session_id}/download")
    assert csv_response.status_code == 200
    assert (
        publishable["config"]["student"]["name"] in csv_response.text
    )  # Native CSV uses the frozen public student name.
    assert "EDITED CSV TITLE" not in csv_response.text
    assert "새 통합 초안" in csv_response.text


@pytest.mark.parametrize("entry", ["api", "detail"])
@pytest.mark.parametrize(
    "state,expected",
    [
        ("draft", 400),
        ("inactive", 404),
        ("unassigned", 403),
        ("deleted", 404),
        ("model_disabled", 400),
        ("connection_disabled", 400),
        ("student_stale", 400),
        ("analysis_stale", 400),
        ("identity_mismatch", 400),
        ("invalid_options", 400),
        ("missing_config", 400),
        ("empty_problem", 400),
        ("empty_objective", 400),
        ("review_required", 400),
    ],
)
async def test_start_policy_is_shared_and_failed_start_is_atomic(
    data, api, publishable, entry, state, expected
):
    from sqlalchemy import func, select

    from src.models import ModelConfig, ProviderConnection, Scenario

    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    async with data.factory() as db:
        scenario = await db.get(Scenario, sid)
        model = await db.get(ModelConfig, 1)
        if state == "draft":
            scenario.status = "draft"
        elif state == "inactive":
            scenario.is_active = False
        elif state == "unassigned":
            owner = await db.get(type(data.owner), data.owner.id)
            owner.group_id = None
        elif state == "deleted":
            scenario.mark_deleted()
        elif state == "model_disabled":
            model.enabled = False
        elif state == "review_required":
            scenario.review_required = True
        elif state == "connection_disabled":
            (await db.get(ProviderConnection, 1)).enabled = False
        elif state.endswith("stale"):
            role = state.split("_")[0]
            model.verification_state = {
                **model.verification_state,
                role: {
                    **model.verification_state[role],
                    "role_contract_version": "old-contract",
                },
            }
        else:
            config = scenario.config_json.copy()
            if state == "missing_config":
                config = None
            else:
                import copy

                config = copy.deepcopy(config)
                selected = config["student"]["resolved_model_config"]
                if state == "identity_mismatch":
                    selected["model_id"] = "gpt-5.2"
                elif state == "empty_problem":
                    config["problem"]["public_text"] = " "
                elif state == "empty_objective":
                    config["problem"]["learning_objective"] = ""
                else:
                    selected["options"] = {"temperature": 0.5}
            scenario.config_json = config
        await db.commit()
    login(api, data.owner)
    await api.get("/scenarios")
    response, _ = await start(api, sid, entry)
    assert response.status_code == expected, response.text
    assert "PRIVATE" not in response.text
    async with data.factory() as db:
        assert (
            await db.scalar(
                select(func.count(Session.id)).where(Session.scenario_id == sid)
            )
            == 0
        )


@pytest.mark.parametrize("entry", ["api", "detail"])
async def test_frozen_options_survive_invalid_current_defaults(
    data, api, publishable, entry
):
    from src.models import ModelConfig

    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    async with data.factory() as db:
        model = await db.get(ModelConfig, 1)
        model.default_options_json = {"temperature": 0.7}
        await db.commit()
    response, session_id = await start(api, sid, entry)
    assert response.status_code == (201 if entry == "api" else 200)
    async with data.factory() as db:
        session = await db.get(Session, session_id)
        assert session.config_snapshot_json["config"]["student"][
            "resolved_model_config"
        ]["options"] == {
            "max_output_tokens": 900,
            "reasoning": {"effort": "medium"},
        }


@pytest.mark.parametrize("state", ["inactive", "unassigned", "deleted"])
async def test_current_acl_still_blocks_reuse(data, api, publishable, state):
    from src.models import Scenario

    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    login(api, data.owner)
    await api.get("/scenarios")
    response, _ = await start(api, sid, "detail")
    assert response.status_code == 200
    async with data.factory() as db:
        scenario = await db.get(Scenario, sid)
        if state == "inactive":
            scenario.is_active = False
        elif state == "deleted":
            scenario.mark_deleted()
        else:
            (await db.get(type(data.owner), data.owner.id)).group_id = None
        await db.commit()
    response, _ = await start(api, sid, "detail")
    assert response.status_code == (403 if state == "unassigned" else 404)


@pytest.mark.parametrize(
    "damage",
    [
        "missing",
        "missing_field",
        "hash",
        "schema",
        "origin",
        "time",
        "revision",
    ],
)
async def test_missing_or_corrupt_snapshot_cannot_fall_back_to_scenario(
    data, api, publishable, damage
):
    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    response, session_id = await start(api, sid, "api")
    assert response.status_code == 201
    async with data.factory() as db:
        session = await db.get(Session, session_id)
        if damage == "missing":
            session.config_snapshot_json = None
        elif damage == "missing_field":
            import copy

            from src.services.lesson_snapshots import canonical_hash

            envelope = copy.deepcopy(session.config_snapshot_json)
            del envelope["config"]["student"]["public_profile"]
            session.config_snapshot_json = envelope
            session.config_hash = canonical_hash(envelope)
        elif damage == "hash":
            session.config_hash = "0" * 64
        elif damage == "schema":
            session.config_snapshot_json = dict(
                session.config_snapshot_json, schema_version=2
            )
        elif damage == "origin":
            session.snapshot_origin = None
        elif damage == "time":
            session.snapshot_created_at = None
        else:
            session.source_scenario_version = None
        await db.commit()
    response, _ = await start(api, sid, "detail")
    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "configuration_unavailable"}


async def test_session_create_rejects_client_model_selection_and_requires_csrf(
    data, api, publishable
):
    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    assert (
        await api.post("/sessions", json={"scenario_id": sid})
    ).status_code == 403
    response = await post(
        api,
        "/sessions",
        {
            "scenario_id": sid,
            "resolved_model_config": {"model_id": "client-override"},
        },
    )
    assert response.status_code == 422


async def test_listing_uses_public_config_and_skips_unconverted_scenarios(
    data, api, publishable
):
    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    login(api, data.owner)
    response = await api.get("/scenarios")
    assert response.status_code == 200
    assert f'href="/scenarios/{sid}"' in response.text
    assert 'href="/scenarios/1"' not in response.text
    assert (
        publishable["config"]["problem"]["public_text"].replace('"', "&#34;")
        in response.text
    )
    for private in ("resolved_model_config", "misconception", "criteria"):
        assert private not in response.text


@pytest.mark.parametrize("entry", ["api", "detail"])
async def test_active_mentor_requires_its_current_role_evidence(
    data, api, publishable, entry
):
    import copy

    from src.models import ModelConfig

    publishable["config"]["mentor"].update(
        mode="manual",
        resolved_model_config=copy.deepcopy(
            publishable["config"]["student"]["resolved_model_config"]
        ),
        welcome_message="Public welcome",
    )
    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    async with data.factory() as db:
        model = await db.get(ModelConfig, 1)
        model.verification_state = {
            **model.verification_state,
            "mentor": {"status": "unverified"},
        }
        await db.commit()
    response, _ = await start(api, sid, entry)
    assert response.status_code == 400
    assert response.json()["detail"] == {"code": "configuration_unavailable"}


@pytest.mark.parametrize("entry", ["api", "detail"])
async def test_scenario_edit_racing_start_cannot_mix_revisions(
    data, api, publishable, entry
):
    import asyncio
    import copy

    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    edited = copy.deepcopy(publishable)
    edited.update(
        expected_version=1,
        title="NEW REVISION TITLE",
        subject="NEW SUBJECT",
        target_grade="NEW GRADE",
    )
    edited["config"]["problem"]["public_text"] = "NEW REVISION PROBLEM"
    edited["config"]["student"]["name"] = "NEW STUDENT"
    edited["config"]["runtime"]["context_turn_limit"] = 7
    update_response, (start_response, session_id) = await asyncio.gather(
        post(api, f"/admin/scenarios/{sid}/update", edited),
        start(api, sid, entry),
    )
    assert update_response.status_code == 200
    assert start_response.status_code == (201 if entry == "api" else 200)
    async with data.factory() as db:
        session = await db.get(Session, session_id)
        expected = (
            publishable if session.source_scenario_version == 1 else edited
        )
        assert session.source_scenario_version in (1, 2)
        assert session.config_snapshot_json == {
            "schema_version": 1,
            "scenario_context": {
                key: expected[key]
                for key in ("title", "subject", "target_grade")
            },
            "config": expected["config"],
        }


async def test_concurrent_detail_requests_reuse_one_immutable_session(
    data, api, publishable
):
    import asyncio

    from sqlalchemy import func, select

    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    results = await asyncio.gather(
        start(api, sid, "detail"), start(api, sid, "detail")
    )
    assert all(response.status_code == 200 for response, _ in results)
    assert results[0][1] == results[1][1]
    async with data.factory() as db:
        assert (
            await db.scalar(
                select(func.count(Session.id)).where(Session.scenario_id == sid)
            )
            == 1
        )


async def test_native_mentor_mode_stays_frozen_until_a_new_lesson(
    api, publishable
):
    import copy

    publishable["config"]["mentor"].update(
        mode="manual",
        resolved_model_config=copy.deepcopy(
            publishable["config"]["student"]["resolved_model_config"]
        ),
        welcome_message="Public mentor welcome",
    )
    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    response, session_id = await start(api, sid, "detail")
    assert response.status_code == 200
    assert '"mentorEnabled": true' in response.text
    assert "Public mentor welcome" in response.text
    publishable.update(expected_version=1)
    publishable["config"]["mentor"]["mode"] = "off"
    assert (
        await post(api, f"/admin/scenarios/{sid}/update", publishable)
    ).status_code == 200
    reused, reused_id = await start(api, sid, "detail")
    assert reused_id == session_id
    assert '"mentorEnabled": true' in reused.text
    assert (
        await post(api, f"/sessions/{session_id}/close", {})
    ).status_code == 200
    new, new_id = await start(api, sid, "detail")
    assert new_id != session_id
    assert '"mentorEnabled": false' in new.text
    assert "Public mentor welcome" not in new.text


@pytest.mark.parametrize("revocation", ["model", "connection"])
async def test_native_record_remains_readable_after_model_revocation(
    data, api, publishable, revocation
):
    from src.models import ModelConfig, ProviderConnection

    sid = (await post(api, "/admin/scenarios", publishable)).json()["id"]
    created, session_id = await start(api, sid, "detail")
    assert created.status_code == 200
    async with data.factory() as db:
        (
            await db.get(
                ModelConfig if revocation == "model" else ProviderConnection, 1
            )
        ).enabled = False
        await db.commit()
    response, reused_id = await start(api, sid, "detail")
    assert response.status_code == 200
    assert reused_id == session_id
    assert (await start(api, sid, "api"))[0].status_code == 400
