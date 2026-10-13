"""Saved scenario comparison through authenticated HTTP and real SDK mocks."""

import copy
from uuid import uuid4

import pytest
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.services.model_verification import ROLE_CONTRACT_VERSIONS

__all__ = ["client", "comparison", "scenario_payload", "student"]


@pytest.fixture
async def saved(data, student):
    from legacy_models import native_writer_defaults

    native_writer_defaults(data.engine.url.database)
    evidence = student.model.verification_state["student"]
    student.model.verification_state = {
        role: {**evidence, "role_contract_version": contract}
        for role, contract in ROLE_CONTRACT_VERSIONS.items()
    }
    await data.db.commit()
    return dict(
        title="Saved comparison",
        subject="",
        target_grade="",
        groups=[],
        config_schema_version=1,
        action="save_draft",
        config=copy.deepcopy(data.session.config_snapshot_json["config"]),
    )


async def test_new_saves_require_common_live_model_but_keep_role_options(
    data, client, student, saved
):
    login(client, data.admin)
    saved["config"]["analysis"]["resolved_model_config"] = None
    rejected = await client.post("/admin/scenarios", json=saved)
    assert rejected.status_code == 422
    assert rejected.json()["detail"][0]["code"] == "common_model_required"
    saved["config"]["analysis"]["resolved_model_config"] = copy.deepcopy(
        saved["config"]["student"]["resolved_model_config"]
    )
    saved["config"]["analysis"]["resolved_model_config"]["options"][
        "max_output_tokens"
    ] = 8192
    created = await client.post("/admin/scenarios", json=saved)
    assert created.status_code == 201, created.text
    path = f"/admin/scenarios/{created.json()['id']}"
    original = (await client.get(path)).json()
    student.model.enabled = False
    await data.db.commit()
    saved["expected_version"] = 1
    saved["title"] = "Must not save"
    rejected = await client.post(path + "/update", json=saved)
    assert rejected.status_code == 422
    assert (await client.get(path)).json() == original


async def test_saved_comparison_create_and_turn_replay_freeze_one_history(
    data, client, student, saved
):
    login(client, data.admin)
    created = await client.post("/admin/scenarios", json=saved)
    assert created.status_code == 201, created.text
    setup = dict(
        request_id=str(uuid4()),
        scenario_id=created.json()["id"],
        expected_scenario_version=1,
        panels=[
            dict(
                panel_id="a",
                model_config_id=student.model.id,
                role_options={"analysis": {"max_output_tokens": 8192}},
            )
        ],
    )
    response = await client.post("/admin/comparison-conversations", json=setup)
    assert response.status_code == 201, response.text
    parent = response.json()
    assert student.responses.create.await_count == 0
    assert parent["version"] == 1 and parent["children"] == []
    path = "/admin/comparison-conversations/" + parent["conversation_id"]
    assert (
        await client.post("/admin/comparison-conversations", json=setup)
    ).json() == parent
    turn = dict(request_id=str(uuid4()), expected_version=1, message="Why?")
    response = await client.post(path + "/turn", json=turn)
    assert response.status_code == 200, response.text
    accepted = frames(response)[0][1]
    assert accepted["conversation_id"] == parent["conversation_id"]
    state = (await client.get(path)).json()
    assert state["version"] == 2 and len(state["children"]) == 1
    assert state["pairs"] == []
    assert (await client.post(path + "/turn", json=turn)).json() == state
    assert student.responses.create.await_count == 1
    child = state["children"][0]
    panel = child["panels"][0]
    adoption = dict(
        request_id=str(uuid4()),
        expected_version=2,
        child_id=child["comparison_run_id"],
        panel_id="a",
        generation_id=panel["run_id"],
    )
    response = await client.post(path + "/adopt", json=adoption)
    assert response.status_code == 200, response.text
    assert response.json()["pairs"][0]["student"] == "Student answer"
    assert response.json()["version"] == 3
    stale = await client.post(
        path + "/turn", json={**turn, "request_id": str(uuid4())}
    )
    assert stale.status_code == 409
    assert (await client.get("/admin/comparisons")).json()["items"] == []
    bypass = await client.post(
        "/admin/comparisons/" + child["comparison_run_id"] + "/stop",
        json={
            "generations": [{"panel_id": "a", "generation_id": panel["run_id"]}]
        },
    )
    assert bypass.status_code == 409
    assert bypass.json()["detail"]["code"] == "use_conversation"
    apply = await client.get(path + "/panels/a/model")
    assert apply.status_code == 200
    assert apply.json()["role_options"]["analysis"] == {
        "max_output_tokens": 8192
    }
    login(client, data.owner)
    assert (await client.get(path)).status_code == 403


async def begin(client, student, saved):
    created = await client.post("/admin/scenarios", json=saved)
    assert created.status_code == 201, created.text
    created = await client.post(
        "/admin/comparison-conversations",
        json=dict(
            request_id=str(uuid4()),
            scenario_id=created.json()["id"],
            expected_scenario_version=1,
            panels=[
                dict(
                    panel_id=panel,
                    model_config_id=student.model.id,
                    role_options={"analysis": {"max_output_tokens": 8192}},
                )
                for panel in ["a", "b"]
            ],
        ),
    )
    assert created.status_code == 201, created.text
    return (
        "/admin/comparison-conversations/" + created.json()["conversation_id"]
    )


async def act(client, path, action, **values):
    state = (await client.get(path)).json()
    return await client.post(
        path + "/" + action,
        json=dict(
            request_id=str(uuid4()), expected_version=state["version"], **values
        ),
    )


async def add_pair(client, path):
    response = await act(client, path, "turn", message="Common question")
    assert response.status_code == 200, response.text
    state = (await client.get(path)).json()
    child = state["children"][-1]
    panel = child["panels"][0]
    response = await act(
        client,
        path,
        "adopt",
        child_id=child["comparison_run_id"],
        panel_id=panel["panel_id"],
        generation_id=panel["run_id"],
    )
    assert response.status_code == 200, response.text
    return response.json()["pairs"][-1]["turn_id"]


async def test_auto_policy_explicit_resolution_manual_trigger_and_shared_final_analysis(
    data, client, student, saved, monkeypatch
):
    import json
    from types import SimpleNamespace

    import httpx2
    from lesson_fixtures import mentor_output
    from s4_analysis_fixtures import analysis_reply, prompt_inputs
    from test_analysis_invocations import USAGE, analysis_transport
    from test_student_probe import response_body

    login(client, data.admin)
    saved["config"]["mentor"].update(
        mode="auto",
        name="Mentor",
        behavior_instruction="Coach",
        resolved_model_config=copy.deepcopy(
            saved["config"]["student"]["resolved_model_config"]
        ),
    )
    saved["config"]["mentor"]["intervention_policy"].update(
        condition="Look for mistakes",
        start_turn=2,
        min_interval_turns=2,
        max_interventions=1,
        window_turns=10,
    )
    path = await begin(client, student, saved)
    target = await add_pair(client, path)
    state = (await client.get(path)).json()
    assert state["pending_auto"] is True
    assert (await act(client, path, "end")).status_code == 409
    skipped = await act(
        client, path, "mentor", target_turn_id=target, trigger="auto"
    )
    assert skipped.status_code == 200, skipped.text
    assert skipped.json()["mentor_actions"][0]["panel_decisions"] == {
        "a": "before_start",
        "b": "before_start",
    }
    assert student.responses.create.await_count == 2
    student.responses.create.return_value = SimpleNamespace(
        output_text=mentor_output("Shared coaching"),
        usage=None,
        output=[],
        status="completed",
    )
    manual = await act(
        client, path, "mentor", target_turn_id=target, trigger="manual"
    )
    assert manual.status_code == 200, manual.text
    assert student.responses.create.await_count == 4
    assert "PRIVATE-REASON" not in manual.text
    calls = student.responses.create.call_args_list[-2:]
    assert calls[0].kwargs["input"] == calls[1].kwargs["input"]
    assert "Look for mistakes" not in calls[0].kwargs["instructions"]
    capped = await act(
        client, path, "mentor", target_turn_id=target, trigger="manual"
    )
    assert capped.status_code == 200
    assert capped.json()["mentor_actions"][-1]["panel_decisions"] == {
        "a": "cap",
        "b": "cap",
    }

    async def upstream(request, payload):
        inputs = prompt_inputs(payload)
        return httpx2.Response(
            200,
            json=response_body(
                json.dumps(analysis_reply(inputs["messages"])), USAGE
            ),
        )

    _, calls = analysis_transport(monkeypatch, upstream)
    ended = await act(client, path, "end")
    assert ended.status_code == 200, ended.text
    state = (await client.get(path)).json()
    assert state["dialogue_status"] == "ended"
    assert state["children"][-1]["target_role"] == "analysis"
    assert len(calls) == 2
    assert prompt_inputs(calls[0]) == prompt_inputs(calls[1])
    assert (
        await act(client, path, "turn", message="Too late")
    ).status_code == 409
    assert len(state["pairs"]) == 1


async def test_failed_panel_retry_is_durable_and_aggregate_delete_keeps_ledger(
    data, client, student, saved
):
    from sqlalchemy import select

    from src.models import ApiUsageLog
    from src.services.invocation_types import InvocationError

    login(client, data.admin)
    path = await begin(client, student, saved)
    student.responses.create.side_effect = [
        student.stream,
        InvocationError("transient"),
    ]
    response = await act(client, path, "turn", message="Same question")
    assert response.status_code == 200
    state = (await client.get(path)).json()
    child = state["children"][0]
    failed = next(
        panel for panel in child["panels"] if panel["status"] == "failed"
    )
    successful = next(
        panel for panel in child["panels"] if panel["status"] == "completed"
    )
    student.responses.create.side_effect = None
    student.responses.create.return_value = student.stream
    retry_path = (
        path
        + f"/children/{child['comparison_run_id']}/panels/{failed['panel_id']}/retry"
    )
    body = dict(
        request_id=str(uuid4()),
        expected_version=state["version"],
        generation_id=failed["run_id"],
    )
    retry = await client.post(retry_path, json=body)
    assert retry.status_code == 200, retry.text
    state = (await client.get(path)).json()
    assert len(state["children"][0]["panels"][1]["attempts"]) == 1
    assert state["children"][0]["panels"][0]["run_id"] == successful["run_id"]
    assert student.responses.create.await_count == 3
    assert (await client.post(retry_path, json=body)).json() == state
    assert state["expires_at"] == child["expires_at"]
    history = (await client.get("/admin/comparison-conversations")).json()
    assert len(history["items"]) == 1
    assert (await client.delete(path)).status_code == 204
    assert (await client.get(path)).status_code == 404
    async with data.factory() as db:
        usages = (await db.scalars(select(ApiUsageLog))).all()
        assert usages and all(
            usage.run_id is None and usage.usage_scope == "comparison"
            for usage in usages
        )


async def test_expired_aggregate_cleanup_is_one_item_and_preserves_usage(
    data, client, student, saved
):
    from datetime import timedelta

    from sqlalchemy import select

    from src.models import ApiUsageLog, ComparisonConversation, ComparisonRun
    from src.services.comparison_retention import cleanup_comparisons
    from src.services.comparison_state import utcnow

    login(client, data.admin)
    path = await begin(client, student, saved)
    await add_pair(client, path)
    await add_pair(client, path)
    identifier = path.rsplit("/", 1)[1]
    async with data.factory() as db:
        parent = await db.get(ComparisonConversation, identifier)
        parent.expires_at = utcnow() - timedelta(days=1)
        for child in (await db.scalars(select(ComparisonRun))).all():
            child.expires_at = parent.expires_at
        await db.commit()
    assert (await client.get(path)).status_code == 404
    dry = await cleanup_comparisons(data.factory)
    assert dry == dict(targets=1, deleted=0, failed=0, skipped_running=0)
    assert (await cleanup_comparisons(data.factory, execute=True))[
        "deleted"
    ] == 1
    async with data.factory() as db:
        assert await db.get(ComparisonConversation, identifier) is None
        assert (await db.scalars(select(ComparisonRun))).all() == []
        assert all(
            usage.run_id is None
            for usage in (await db.scalars(select(ApiUsageLog))).all()
        )


async def test_saved_picker_and_source_editor_handoff_do_not_save(
    data, client, student, saved
):
    import json
    import re

    login(client, data.admin)
    path = await begin(client, student, saved)
    state = (await client.get(path)).json()
    source_id = state["source_snapshot"]["scenario_id"]
    picker = await client.get("/admin/scenarios?compare=1")
    assert f"/admin/model-comparison?scenario_id={source_id}" in picker.text
    page = await client.get(f"/admin/model-comparison?scenario_id={source_id}")
    assert page.status_code == 200
    assert 'id="comparison-conversation-data"' in page.text
    edit = await client.get(
        f'/admin/scenarios/{source_id}/edit?conversation={state["conversation_id"]}&panel=a'
    )
    assert edit.status_code == 200
    bootstrap = json.loads(
        re.search(
            r'<script id="scenario-editor-data" type="application/json">(.*?)</script>',
            edit.text,
            re.S,
        ).group(1)
    )
    assert bootstrap["model_handoff"]["role_options"]["analysis"] == {
        "max_output_tokens": 8192
    }
    assert (
        bootstrap["title"] == saved["title"]
        and bootstrap["config_version"] == 1
    )
    assert (await client.get(f"/admin/scenarios/{source_id}")).json()[
        "config"
    ] == saved["config"]
    assert student.responses.create.await_count == 0


async def test_version_race_stop_and_legacy_running_slot_are_atomic(
    data, client, student, saved, comparison
):
    import asyncio

    from src.models import ComparisonConversation
    from src.services.generation_lifecycle import interrupt_orphans

    login(client, data.admin)
    path = await begin(client, student, saved)
    gate = asyncio.Event()
    student.stream.events.insert(0, gate)
    body = dict(
        request_id=str(uuid4()), expected_version=1, message="Durable question"
    )
    task = asyncio.create_task(client.post(path + "/turn", json=body))
    try:
        await asyncio.wait_for(student.stream.read_started.wait(), 10)
        state = (await client.get(path)).json()
        assert state["version"] == 2 and len(state["children"]) == 1
        # Admission is visible to a separate committed reader before any output.
        async with data.factory() as db:
            parent = await db.get(
                ComparisonConversation, state["conversation_id"]
            )
            assert (
                parent.state_json["receipts"][body["request_id"]]["child_id"]
                == state["children"][0]["comparison_run_id"]
            )
        assert (await client.post(path + "/turn", json=body)).json() == state
        assert (
            await client.post(
                path + "/turn", json={**body, "request_id": str(uuid4())}
            )
        ).status_code == 409
        assert (
            await client.post("/admin/comparisons", json=comparison)
        ).status_code == 409
        assert (await client.delete(path)).status_code == 409
        child = state["children"][0]
        panel = child["panels"][0]
        assert (
            await act(
                client,
                path,
                "adopt",
                child_id=child["comparison_run_id"],
                panel_id=panel["panel_id"],
                generation_id=panel["run_id"],
            )
        ).status_code == 409
        stopped = await act(
            client,
            path,
            "stop",
            child_id=child["comparison_run_id"],
            generations=[
                dict(panel_id=panel["panel_id"], generation_id=panel["run_id"])
                for panel in child["panels"]
            ],
        )
        assert stopped.status_code == 200, stopped.text
    finally:
        gate.set()
        await asyncio.wait_for(task, 10)
    state = (await client.get(path)).json()
    assert state["phase"] == "retry_required"
    assert state["pairs"] == []
    assert all(
        panel["status"] == "cancelled"
        for panel in state["children"][0]["panels"]
    )
    # Restart preserves the parent and never resumes provider calls.
    count = student.responses.create.await_count
    await interrupt_orphans(data.factory)
    assert (await client.get(path)).json()["pairs"] == []
    assert student.responses.create.await_count == count


async def test_incompatible_panel_options_and_revoked_active_role_refuse_without_calls(
    data, client, student, saved
):
    login(client, data.admin)
    created = await client.post("/admin/scenarios", json=saved)
    setup = dict(
        request_id=str(uuid4()),
        scenario_id=created.json()["id"],
        expected_scenario_version=1,
        panels=[
            dict(
                panel_id="a",
                model_config_id=student.model.id,
                role_options={"student": {"thinking": {"budget": 0}}},
            )
        ],
    )
    response = await client.post("/admin/comparison-conversations", json=setup)
    assert response.status_code == 422, response.text
    assert "thinking" not in response.text
    assert student.responses.create.await_count == 0
    path = await begin(client, student, saved)
    student.model.verification_state = {
        **student.model.verification_state,
        "analysis": {"status": "failed"},
    }
    await data.db.commit()
    state = (await client.get(path)).json()
    rejected = await act(
        client, path, "turn", message="Cannot use this common model"
    )
    assert rejected.status_code == 422, rejected.text
    assert (await client.get(path)).json() == state
    assert student.responses.create.await_count == 0


async def test_source_edits_and_registration_defaults_never_change_frozen_rounds(
    data, client, student, saved
):
    from src.models import Scenario

    login(client, data.admin)
    path = await begin(client, student, saved)
    initial = (await client.get(path)).json()
    await add_pair(client, path)
    async with data.factory() as db:
        source = await db.get(
            Scenario, initial["source_snapshot"]["scenario_id"]
        )
        config = copy.deepcopy(source.config_json)
        config["student"]["behavior_instruction"] = "Different instructions"
        source.config_json = config
        source.config_version += 1
        await db.commit()
    student.model.default_options_json = {
        "max_output_tokens": 9000,
        "reasoning": {"effort": "high"},
    }
    await data.db.commit()
    response = await act(client, path, "turn", message="Same next question")
    assert response.status_code == 200, response.text
    calls = student.responses.create.call_args_list[-2:]
    assert calls[0].kwargs["input"] == calls[1].kwargs["input"]
    assert calls[0].kwargs["input"] == [
        dict(role="user", content="Common question"),
        dict(role="assistant", content="Student answer"),
        dict(role="user", content="Same next question"),
    ]
    assert "Different instructions" not in calls[0].kwargs["instructions"]
    assert calls[0].kwargs["max_output_tokens"] == 1024
    state = (await client.get(path)).json()
    assert state["source_snapshot"] == initial["source_snapshot"]
    assert all(
        child["expires_at"] == initial["expires_at"]
        for child in state["children"]
    )
    # An adopted round and its competing responses are immutable audit history.
    child = state["children"][0]
    panel = child["panels"][0]
    assert (
        await act(
            client,
            path,
            "adopt",
            child_id=child["comparison_run_id"],
            panel_id="a",
            generation_id=panel["run_id"],
        )
    ).status_code == 409


async def test_mentor_counts_and_intervals_are_per_panel_with_retry_deduplication(
    data, client, student, saved
):
    from types import SimpleNamespace

    from lesson_fixtures import mentor_output

    login(client, data.admin)
    saved["config"]["mentor"].update(
        mode="auto",
        name="Mentor",
        behavior_instruction="Coach",
        resolved_model_config=copy.deepcopy(
            saved["config"]["student"]["resolved_model_config"]
        ),
    )
    saved["config"]["mentor"]["intervention_policy"].update(
        condition="Mistakes",
        start_turn=1,
        min_interval_turns=2,
        max_interventions=1,
        window_turns=10,
    )
    path = await begin(client, student, saved)
    target = await add_pair(client, path)

    def result(coaching):
        return SimpleNamespace(
            output_text=mentor_output(coaching),
            usage=None,
            output=[],
            status="completed",
        )

    from src.services.invocation_types import InvocationError

    student.responses.create.side_effect = [
        result("Coach panel a"),
        result(None),
        InvocationError("transient"),
    ]
    checked = await act(
        client, path, "mentor", target_turn_id=target, trigger="auto"
    )
    assert checked.status_code == 200, checked.text
    manual = await act(
        client, path, "mentor", target_turn_id=target, trigger="manual"
    )
    assert manual.status_code == 200
    state = (await client.get(path)).json()
    assert state["mentor_actions"][-1]["panel_decisions"] == {
        "a": "cap",
        "b": "admitted",
    }
    # Failure does not consume an intervention; retry belongs to this logical action.
    child = state["children"][-1]
    assert child["panels"][0]["status"] == "failed"
    student.responses.create.side_effect = None
    student.responses.create.return_value = result("Recovered coaching")
    retry = await client.post(
        path + f"/children/{child['comparison_run_id']}/panels/b/retry",
        json=dict(
            request_id=str(uuid4()),
            expected_version=state["version"],
            generation_id=child["panels"][0]["run_id"],
        ),
    )
    assert retry.status_code == 200, retry.text
    recovered = (await client.get(path)).json()
    assert recovered["mentor_actions"] == state["mentor_actions"]
    assert recovered["children"][-1]["panels"][0]["status"] == "completed"
    assert len(recovered["children"][-1]["panels"][0]["attempts"]) == 1
    student.responses.create.return_value = student.stream
    target = await add_pair(client, path)
    skipped = await act(
        client, path, "mentor", target_turn_id=target, trigger="auto"
    )
    assert skipped.status_code == 200
    assert skipped.json()["mentor_actions"][-1]["panel_decisions"] == {
        "a": "interval",
        "b": "interval",
    }
    count = student.responses.create.await_count
    duplicate = await act(
        client, path, "mentor", target_turn_id=target, trigger="auto"
    )
    assert duplicate.status_code == 200
    assert student.responses.create.await_count == count
    assert len(duplicate.json()["mentor_actions"]) == 3


async def test_owner_admin_and_csrf_guard_every_parent_action(
    data, client, student, saved
):
    import httpx
    from starlette_csrf import CSRFMiddleware

    from src.config import config
    from src.main import app
    from src.models import User

    login(client, data.admin)
    path = await begin(client, student, saved)
    initial = (await client.get(path)).json()
    second = User(username="second_admin", nickname="Second", role="admin")
    data.db.add(second)
    await data.db.commit()
    login(client, second)
    for suffix in ["", "/panels/a/model"]:
        assert (await client.get(path + suffix)).status_code == 404
    assert (
        await client.post(
            path + "/turn",
            json=dict(
                request_id=str(uuid4()), expected_version=1, message="Denied"
            ),
        )
    ).status_code == 404
    protected = CSRFMiddleware(
        app, secret=config.SESSION_SECRET, header_name="x-csrf-token"
    )
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=protected), base_url="http://test"
    ) as secured:
        login(secured, data.admin)
        await secured.get(path)
        for action in [
            "turn",
            "adopt",
            "mentor",
            "end",
            "stop",
            "children/" + str(uuid4()) + "/panels/a/retry",
        ]:
            assert (
                await secured.post(path + "/" + action, json={})
            ).status_code == 403
        assert (await secured.delete(path)).status_code == 403
        assert (
            await secured.post("/admin/comparison-conversations", json={})
        ).status_code == 403
    login(client, data.admin)
    assert (await client.get(path)).json() == initial
    assert student.responses.create.await_count == 0


async def test_saved_version_and_all_panel_budget_preflight_leave_no_action(
    data, client, student, saved
):
    from sqlalchemy import select

    from src.models import ComparisonConversation

    login(client, data.admin)
    created = await client.post("/admin/scenarios", json=saved)
    setup = dict(
        request_id=str(uuid4()),
        scenario_id=created.json()["id"],
        expected_scenario_version=2,
        panels=[
            dict(
                panel_id="a",
                model_config_id=student.model.id,
                role_options={"analysis": {"max_output_tokens": 8192}},
            )
        ],
    )
    response = await client.post("/admin/comparison-conversations", json=setup)
    assert response.status_code == 409
    async with data.factory() as db:
        assert (await db.scalars(select(ComparisonConversation))).all() == []
    setup["expected_scenario_version"] = 1
    setup["panels"].append(
        dict(
            panel_id="b",
            model_config_id=student.model.id,
            role_options={
                "student": {"max_output_tokens": 128001},
                "analysis": {"max_output_tokens": 8192},
            },
        )
    )
    response = await client.post("/admin/comparison-conversations", json=setup)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_options"
    assert response.json()["detail"]["errors"][0]["panel_id"] == "b"
    assert student.responses.create.await_count == 0
    async with data.factory() as db:
        assert (await db.scalars(select(ComparisonConversation))).all() == []


async def test_one_panel_full_input_refusal_preserves_parent_without_truncation(
    data, client, student, saved
):
    from src.services.model_capabilities import capabilities

    # A combined-limit model lets the larger output reserve block only panel b.
    student.model.model_id = "gpt-5.2"
    student.model.capabilities_json = capabilities("openai", "gpt-5.2")
    for role in ROLE_CONTRACT_VERSIONS:
        selection = saved["config"][role]["resolved_model_config"]
        if selection:
            selection["model_id"] = "gpt-5.2"
    await data.db.commit()
    login(client, data.admin)
    saved["config"]["problem"]["public_text"] = "p" * 50000
    for field, character in [
        ("internal_profile", "i"),
        ("misconception", "m"),
        ("behavior_instruction", "b"),
    ]:
        saved["config"]["student"][field] = character * 50000
    created = await client.post("/admin/scenarios", json=saved)
    assert created.status_code == 201
    setup = dict(
        request_id=str(uuid4()),
        scenario_id=created.json()["id"],
        expected_scenario_version=1,
        panels=[
            dict(
                panel_id=panel,
                model_config_id=student.model.id,
                role_options={
                    "student": {"max_output_tokens": budget},
                    "analysis": {"max_output_tokens": 8192},
                },
            )
            for panel, budget in [("a", 1024), ("b", 128000)]
        ],
    )
    created = await client.post("/admin/comparison-conversations", json=setup)
    assert created.status_code == 201, created.text
    path = (
        "/admin/comparison-conversations/" + created.json()["conversation_id"]
    )
    before = (await client.get(path)).json()
    refused = await act(client, path, "turn", message="한" * 50000)
    assert refused.status_code == 422, refused.text
    assert refused.json()["detail"]["code"] == "context_limit"
    assert refused.json()["detail"]["errors"][0]["panel_id"] == "b"
    assert (await client.get(path)).json() == before
    assert student.responses.create.await_count == 0


async def test_deleted_source_blocks_apply_but_frozen_dialogue_can_continue(
    data, client, student, saved
):
    login(client, data.admin)
    path = await begin(client, student, saved)
    state = (await client.get(path)).json()
    source_id = state["source_snapshot"]["scenario_id"]
    deleted = await client.post(
        f"/admin/scenarios/{source_id}/delete", json={"expected_version": 1}
    )
    assert deleted.status_code == 200
    assert (await client.get(path + "/panels/a/model")).status_code == 404
    response = await act(
        client, path, "turn", message="Frozen source remains usable"
    )
    assert response.status_code == 200, response.text
    assert (await client.get(path)).json()["children"][0]["panels"][0][
        "status"
    ] == "completed"


async def test_ended_analysis_degraded_retry_keeps_successful_sibling_and_final_record(
    data, client, student, saved, monkeypatch
):
    import json

    import httpx2
    from s4_analysis_fixtures import analysis_reply, prompt_inputs
    from test_analysis_invocations import USAGE, analysis_transport
    from test_student_probe import response_body

    login(client, data.admin)
    saved["config"]["analysis"].update(
        classification_enabled=True,
        rubric_name="Questions",
        rubric=[
            dict(
                id="A",
                name="Explore",
                criteria="Explore thinking",
                level="high",
            )
        ],
    )
    path = await begin(client, student, saved)
    await add_pair(client, path)
    seen = []

    async def upstream(request, payload):
        inputs = prompt_inputs(payload)
        seen.append(inputs)
        reply = analysis_reply(inputs["messages"])
        if len(seen) == 2:
            reply["message_classifications"][0]["quote"] = "Unverified quote"
        return httpx2.Response(
            200, json=response_body(json.dumps(reply), USAGE)
        )

    analysis_transport(monkeypatch, upstream)
    ended = await act(client, path, "end")
    assert ended.status_code == 200, ended.text
    before = (await client.get(path)).json()
    assert before["dialogue_status"] == "ended"
    child = before["children"][-1]
    assert [panel["status"] for panel in child["panels"]] == ["ok", "degraded"]
    assert seen[0] == seen[1]
    retry = await client.post(
        path + f"/children/{child['comparison_run_id']}/panels/b/retry",
        json=dict(
            request_id=str(uuid4()),
            expected_version=before["version"],
            generation_id=child["panels"][1]["run_id"],
        ),
    )
    assert retry.status_code == 200, retry.text
    after = (await client.get(path)).json()
    assert after["pairs"] == before["pairs"]
    assert after["expires_at"] == before["expires_at"]
    assert (
        after["children"][-1]["panels"][0]["run_id"]
        == child["panels"][0]["run_id"]
    )
    assert after["children"][-1]["panels"][1]["status"] == "ok"
    assert (
        after["children"][-1]["panels"][1]["attempts"][0]["status"]
        == "degraded"
    )
    assert len(seen) == 3 and seen[2] == seen[0]


async def test_revoked_panel_does_not_interrupt_authorized_sibling(
    data, client, student, saved
):
    import asyncio

    from test_student_generation import FakeStream

    from src.models import ModelConfig

    login(client, data.admin)
    other = ModelConfig(
        provider_connection_id=student.connection.id,
        model_id="gpt-5.2",
        display_name="Second common model",
        enabled=True,
        capability_definition_version=student.model.capability_definition_version,
        default_options_json={"max_output_tokens": 1024},
        verification_state=copy.deepcopy(student.model.verification_state),
    )
    data.db.add(other)
    await data.db.commit()
    created = await client.post("/admin/scenarios", json=saved)
    setup = dict(
        request_id=str(uuid4()),
        scenario_id=created.json()["id"],
        expected_scenario_version=1,
        panels=[
            dict(
                panel_id=panel,
                model_config_id=model.id,
                role_options={"analysis": {"max_output_tokens": 8192}},
            )
            for panel, model in [("a", student.model), ("b", other)]
        ],
    )
    created = await client.post("/admin/comparison-conversations", json=setup)
    assert created.status_code == 201, created.text
    path = (
        "/admin/comparison-conversations/" + created.json()["conversation_id"]
    )
    gates = [asyncio.Event(), asyncio.Event()]
    streams = [
        FakeStream([gate, *copy.deepcopy(student.stream.events)])
        for gate in gates
    ]
    student.responses.create.side_effect = lambda **kwargs: streams[
        int(kwargs["model"] == other.model_id)
    ]
    task = asyncio.create_task(
        act(client, path, "turn", message="Compare fairly")
    )
    try:
        await asyncio.wait_for(
            asyncio.gather(*(stream.read_started.wait() for stream in streams)),
            10,
        )
        other.enabled = False
        await data.db.commit()
    finally:
        for gate in gates:
            gate.set()
        await asyncio.wait_for(task, 10)
    state = (await client.get(path)).json()
    panels = state["children"][0]["panels"]
    assert panels[0]["status"] == "completed"
    assert panels[0]["text"] == "Student answer"
    assert panels[1]["status"] == "interrupted"
    adopted = await act(
        client,
        path,
        "adopt",
        child_id=state["children"][0]["comparison_run_id"],
        panel_id="a",
        generation_id=panels[0]["run_id"],
    )
    assert adopted.status_code == 200, adopted.text
    assert adopted.json()["pairs"][0]["student"] == "Student answer"
