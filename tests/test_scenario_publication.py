"""Publication through authenticated HTTP and isolated migrated SQLite."""

import asyncio
import copy

import pytest
from test_provider_connections import KEY
from test_provider_connections import api as provider_api
from test_provider_connections import post as provider_post
from test_scenario_api import login
from test_scenario_drafts import draft as draft_fixture
from test_scenario_drafts import post

from src.models import ModelConfig, ProviderConnection, Scenario
from src.services.model_capabilities import capabilities
from src.services.model_verification import ROLE_CONTRACT_VERSIONS

pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
api = provider_api
draft = draft_fixture


@pytest.fixture
async def publishable(data, api, draft):
    assert (await provider_post(api, "key", 1, api_key=KEY)).status_code == 200
    registered = await post(
        api,
        "/admin/ai/models",
        dict(provider="openai", model_id="gpt-5-mini", display_name="Verified"),
    )
    assert registered.status_code == 200
    async with data.factory() as db:
        connection = await db.get(ProviderConnection, 1)
        model = await db.get(ModelConfig, 1)
        definition = capabilities("openai", "gpt-5-mini")["definition_version"]
        model.enabled = True
        model.capability_definition_version = definition
        model.verification_state = {
            role: dict(
                status="succeeded",
                credential_revision=connection.credential_revision,
                connection_version=connection.connection_version,
                capability_definition_version=definition,
                role_contract_version=ROLE_CONTRACT_VERSIONS[role],
            )
            for role in ("student", "mentor", "analysis")
        }
        await db.commit()
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
    draft["action"] = "publish"
    draft["groups"] = [data.owner.group_id]
    draft["config"]["problem"]["learning_objective"] = "분수 비교"
    draft["config"]["student"].update(
        name="민수",
        misconception="분모가 크면 크다",
        behavior_instruction="생각을 설명한다",
        resolved_model_config=copy.deepcopy(selection),
    )
    draft["config"]["analysis"].update(
        context="교사 질문 평가",
        expected_understanding="전체가 같아야 한다",
        instruction="근거를 확인한다",
        resolved_model_config=copy.deepcopy(selection),
    )
    return draft


async def test_publish_and_draft_transitions_preserve_saved_options(
    data, api, publishable
):
    created = await post(api, "/admin/scenarios", publishable)
    assert created.status_code == 201, created.text
    assert created.json()["status"] == "published"
    assert created.json()["version"] == 1
    path = f"/admin/scenarios/{created.json()['id']}"
    saved = (await api.get(path)).json()
    assert saved["config"] == publishable["config"]
    # Valid frozen options remain usable even if current defaults are invalid.
    async with data.factory() as db:
        model = await db.get(ModelConfig, 1)
        model.default_options_json = {"temperature": 0.7}
        await db.commit()
    publishable["expected_version"] = 1
    assert (await post(api, path + "/update", publishable)).status_code == 200
    publishable.update(expected_version=2, action="save_draft")
    assert (await post(api, path + "/update", publishable)).json()[
        "status"
    ] == "draft"
    assert (await api.get(path)).json()["config_version"] == 3


@pytest.mark.parametrize(
    "operation", ["publish", "groups", "activation", "delete"]
)
async def test_all_mutations_share_one_revision_with_concurrent_requests(
    api, publishable, operation
):
    publishable["action"] = "save_draft"
    created = await post(api, "/admin/scenarios", publishable)
    path = f"/admin/scenarios/{created.json()['id']}"
    publishable.update(expected_version=1, title="First writer")
    second = copy.deepcopy(publishable)
    second["title"] = "Second writer"
    suffix = "/update"
    if operation == "publish":
        second["action"] = "publish"
    elif operation == "groups":
        second["groups"] = []
    elif operation == "activation":
        second["is_active"] = False
    else:
        suffix, second = "/delete", {"expected_version": 1}
    results = await asyncio.gather(
        post(api, path + "/update", publishable),
        post(api, path + suffix, second),
    )
    assert sorted(r.status_code for r in results) == [200, 409], [
        r.text for r in results
    ]
    winner = next(r for r in results if r.status_code == 200)
    assert winner.json()["version"] == 2
    assert next(r for r in results if r.status_code == 409).json()[
        "detail"
    ] == {"code": "version_conflict", "current_version": 2}
    saved = await api.get(path)
    if operation == "delete" and results[1].status_code == 200:
        assert saved.status_code == 404
    else:
        body = publishable if results[0].status_code == 200 else second
        assert saved.json()["title"] == body["title"]
        assert saved.json()["groups"] == body["groups"]
        assert saved.json()["is_active"] == body.get("is_active", True)
        assert saved.json()["status"] == (
            "published" if body["action"] == "publish" else "draft"
        )
        assert saved.json()["config_version"] == 2


async def test_publish_and_delete_reject_missing_revision_permissions_and_csrf(
    data, api, publishable
):
    created = await post(api, "/admin/scenarios", publishable)
    path = f"/admin/scenarios/{created.json()['id']}"
    before = (await api.get(path)).json()
    assert (await post(api, path + "/update", publishable)).status_code == 422
    assert (await post(api, path + "/delete", {})).status_code == 422
    publishable["expected_version"] = 1
    for endpoint, body in (
        (path + "/update", publishable),
        (path + "/delete", {"expected_version": 1}),
    ):
        forbidden = await asyncio.gather(
            api.post(endpoint, json=body), api.post(endpoint, json=body)
        )
        assert [r.status_code for r in forbidden] == [403, 403]
        login(api, data.owner)
        await api.get("/login")
        assert (await post(api, endpoint, body)).status_code == 403
        api.cookies.delete("session_id")
        assert (await post(api, endpoint, body)).status_code == 401
        login(api, data.admin)
        await api.get(path)
    assert (await api.get(path)).json() == before


async def test_deleted_revision_rejects_stale_update_and_delete(
    api, publishable
):
    created = await post(api, "/admin/scenarios", publishable)
    path = f"/admin/scenarios/{created.json()['id']}"
    assert (await post(api, path + "/delete", {"expected_version": 1})).json()[
        "version"
    ] == 2
    publishable["expected_version"] = 1
    for endpoint, body in (
        (path + "/update", publishable),
        (path + "/delete", {"expected_version": 1}),
    ):
        rejected = await post(api, endpoint, body)
        assert rejected.status_code == 409, rejected.text
        assert rejected.json()["detail"]["current_version"] == 2
    assert (
        await post(api, path + "/delete", {"expected_version": 2})
    ).status_code == 404
    assert (await api.get(path)).status_code == 404


@pytest.mark.parametrize(
    "invalidation",
    ["connection", "model", "role", "definition", "metadata", "options"],
)
async def test_current_model_authorization_and_saved_options_gate_publication(
    data, api, publishable, invalidation
):
    created = await post(api, "/admin/scenarios", publishable)
    path = f"/admin/scenarios/{created.json()['id']}"
    before = (await api.get(path)).json()
    async with data.factory() as db:
        connection = await db.get(ProviderConnection, 1)
        model = await db.get(ModelConfig, 1)
        if invalidation == "connection":
            connection.enabled = False
        elif invalidation == "model":
            model.enabled = False
        elif invalidation == "role":
            model.verification_state = {
                **model.verification_state,
                "student": {"status": "failed"},
            }
        elif invalidation == "definition":
            model.capability_definition_version = "old"
        elif invalidation == "metadata":
            connection.catalog_models_json = [
                {"model_id": model.model_id, "shutdown_date": "2020-01-01"}
            ]
        await db.commit()
    if invalidation == "options":
        publishable["config"]["student"]["resolved_model_config"]["options"][
            "temperature"
        ] = 0.7
    publishable.update(expected_version=1, title="Must not publish", groups=[])
    rejected = await post(api, path + "/update", publishable)
    assert rejected.status_code == 422
    assert "config.student.resolved_model_config" in [
        e["path"] for e in rejected.json()["detail"]
    ]
    assert (await api.get(path)).json() == before
    publishable["action"] = "save_draft"
    assert (await post(api, path + "/update", publishable)).status_code == 200
    assert (await api.get(path)).json()["config"] == publishable["config"]


async def test_disabled_mentor_does_not_require_current_role_authorization(
    data, api, publishable
):
    publishable["config"]["mentor"]["resolved_model_config"] = copy.deepcopy(
        publishable["config"]["student"]["resolved_model_config"]
    )
    async with data.factory() as db:
        model = await db.get(ModelConfig, 1)
        model.verification_state = {
            **model.verification_state,
            "mentor": {"status": "failed"},
        }
        await db.commit()
    assert (await post(api, "/admin/scenarios", publishable)).status_code == 201
    publishable["config"]["mentor"].update(
        mode="manual", behavior_instruction="교사를 돕는다"
    )
    assert (await post(api, "/admin/scenarios", publishable)).status_code == 422


@pytest.mark.parametrize(
    "field",
    [
        "problem.public_text",
        "problem.learning_objective",
        "student.name",
        "student.misconception",
        "student.behavior_instruction",
        "student.resolved_model_config",
        "analysis.context",
        "analysis.expected_understanding",
        "analysis.instruction",
        "analysis.resolved_model_config",
        "mentor.name",
        "mentor.behavior_instruction",
        "mentor.resolved_model_config",
        "mentor.intervention_policy.condition",
        "analysis.rubric_name",
        "analysis.rubric.0.id",
        "analysis.rubric.0.name",
        "analysis.rubric.0.criteria",
    ],
)
async def test_publication_requires_active_fields_and_is_atomic(
    data, api, publishable, field
):
    created = await post(api, "/admin/scenarios", publishable)
    assert created.status_code == 201
    path = f"/admin/scenarios/{created.json()['id']}"
    before = (await api.get(path)).json()
    publishable.update(expected_version=1, title="Must not save", groups=[])
    selection = publishable["config"]["student"]["resolved_model_config"]
    publishable["config"]["mentor"].update(
        mode="auto",
        behavior_instruction="교사 질문을 도와준다",
        resolved_model_config=copy.deepcopy(selection),
    )
    publishable["config"]["analysis"].update(
        classification_enabled=True,
        rubric_name="질문",
        rubric=[
            dict(
                id="A", name="생각 탐색", criteria="이유를 묻는다", level="high"
            )
        ],
    )
    target = publishable["config"]
    parts = field.split(".")
    for part in parts[:-1]:
        target = target[int(part)] if isinstance(target, list) else target[part]
    target[parts[-1]] = (
        None if parts[-1] == "resolved_model_config" else " \n\t"
    )
    rejected = await post(api, path + "/update", publishable)
    assert rejected.status_code == 422, rejected.text
    assert f"config.{field}" in [
        error["path"] for error in rejected.json()["detail"]
    ]
    assert (await api.get(path)).json() == before


@pytest.mark.parametrize(
    "rows,code",
    [
        ([dict(id=" A", name="A", criteria="理由")], "invalid_rubric_id"),
        ([dict(id="한글", name="A", criteria="理由")], "invalid_rubric_id"),
        ([dict(id="a!", name="A", criteria="理由")], "invalid_rubric_id"),
        (
            [
                dict(id="A", name="A", criteria="理由"),
                dict(id="A", name="B", criteria="理由"),
            ],
            "duplicate_rubric_id",
        ),
        (
            [
                dict(id="A", name=" A ", criteria="理由"),
                dict(id="a", name="A", criteria="理由"),
            ],
            "duplicate_rubric_name",
        ),
        ([], "required"),
    ],
)
async def test_rubric_rules_apply_only_when_classification_enabled(
    api, publishable, rows, code
):
    publishable["config"]["analysis"].update(
        classification_enabled=True, rubric_name="분류", rubric=rows
    )
    rejected = await post(api, "/admin/scenarios", publishable)
    assert rejected.status_code == 422, rejected.text
    assert code in [error["code"] for error in rejected.json()["detail"]]
    publishable["config"]["analysis"]["classification_enabled"] = False
    created = await post(api, "/admin/scenarios", publishable)
    assert created.status_code == 201, created.text
    assert (await api.get(f"/admin/scenarios/{created.json()['id']}")).json()[
        "config"
    ]["analysis"]["rubric"] == [dict(row, level=None) for row in rows]


async def test_case_sensitive_rubric_ids_and_optional_levels_publish(
    api, publishable
):
    publishable["config"]["analysis"].update(
        classification_enabled=True,
        rubric_name="분류",
        rubric=[
            dict(id="A", name="탐색", criteria="이유"),
            dict(id="a", name="유도", criteria="정답", level="low"),
        ],
    )
    assert (await post(api, "/admin/scenarios", publishable)).status_code == 201


async def test_review_is_server_managed_and_acknowledgement_is_revision_bound(
    data, api, publishable
):
    publishable["action"] = "save_draft"
    publishable["config"]["problem"]["public_text"] = ""
    created = await post(api, "/admin/scenarios", publishable)
    path = f"/admin/scenarios/{created.json()['id']}"
    warning = dict(
        path="student.public_profile",
        code="privacy_change",
        message="공개 소개 검토",
        blocking=False,
    )
    async with data.factory() as db:
        scenario = await db.get(Scenario, created.json()["id"])
        scenario.review_required = True
        scenario.review_reasons = [
            warning,
            dict(
                path="problem.public_text",
                code="required",
                message="공개 문제 보완",
                blocking=True,
            ),
        ]
        await db.commit()
    before = (await api.get(path)).json()
    publishable.update(
        action="publish", expected_version=1, acknowledge_review=True
    )
    rejected = await post(api, path + "/update", publishable)
    assert rejected.status_code == 422
    assert (await api.get(path)).json() == before
    publishable["config"]["problem"]["public_text"] = "보완한 공개 문제"
    publishable["acknowledge_review"] = False
    rejected = await post(api, path + "/update", publishable)
    assert rejected.status_code == 422
    assert (
        rejected.json()["detail"][0]["code"]
        == "review_acknowledgement_required"
    )
    assert (await api.get(path)).json() == before
    publishable["action"] = "save_draft"
    saved = await post(api, path + "/update", publishable)
    assert saved.status_code == 200
    assert saved.json()["review_reasons"] == [warning]
    assert saved.json()["review_required"] is True
    publishable.update(action="publish", acknowledge_review=True)
    conflict = await post(api, path + "/update", publishable)
    assert conflict.status_code == 409
    assert conflict.json()["detail"]["current_version"] == 2
    publishable["expected_version"] = 2
    published = await post(api, path + "/update", publishable)
    assert published.status_code == 200
    assert published.json()["version"] == 3
    result = (await api.get(path)).json()
    assert result["status"] == "published"
    assert result["review_required"] is False
    assert result["review_reasons"] == []


async def test_acknowledgement_cannot_waive_unresolved_conversion_blocker(
    data, api, publishable
):
    publishable["action"] = "save_draft"
    created = await post(api, "/admin/scenarios", publishable)
    path = f"/admin/scenarios/{created.json()['id']}"
    async with data.factory() as db:
        scenario = await db.get(Scenario, created.json()["id"])
        scenario.review_required = True
        scenario.review_reasons = [
            dict(
                path="student.behavior_instruction",
                code="unknown_placeholder",
                message="원문 보완 필요",
                blocking=True,
            )
        ]
        await db.commit()
    before = (await api.get(path)).json()
    publishable.update(
        action="publish", expected_version=1, acknowledge_review=True
    )
    rejected = await post(api, path + "/update", publishable)
    assert rejected.status_code == 422
    assert (await api.get(path)).json() == before
    publishable["config"]["student"][
        "behavior_instruction"
    ] = "원문을 검토하여 다시 작성한 학생 지시"
    publishable["action"] = "save_draft"
    corrected = await post(api, path + "/update", publishable)
    assert corrected.status_code == 200
    assert corrected.json()["review_reasons"] == []
    publishable.update(
        action="publish", expected_version=2, acknowledge_review=False
    )
    assert (await post(api, path + "/update", publishable)).status_code == 200


async def test_missing_conversion_model_is_resolved_by_a_valid_replacement(
    data, api, publishable
):
    selected = publishable["config"]["student"]["resolved_model_config"]
    publishable["config"]["student"]["resolved_model_config"] = None
    publishable["action"] = "save_draft"
    created = await post(api, "/admin/scenarios", publishable)
    path = f"/admin/scenarios/{created.json()['id']}"
    async with data.factory() as db:
        scenario = await db.get(Scenario, created.json()["id"])
        scenario.review_required = True
        scenario.review_reasons = [
            dict(
                path="student.resolved_model_config",
                code="missing_registration",
                message="등록 모델 선택 필요",
                blocking=True,
            )
        ]
        await db.commit()
    publishable.update(
        action="publish", expected_version=1, acknowledge_review=True
    )
    assert (await post(api, path + "/update", publishable)).status_code == 422
    publishable["config"]["student"]["resolved_model_config"] = selected
    assert (await post(api, path + "/update", publishable)).status_code == 200


@pytest.mark.parametrize(
    "field,code,new_title,expected",
    [
        ("conversion.source", "required", "Unrelated edit", 422),
        ("title", "oversized_source", "Reviewed replacement title", 200),
    ],
)
async def test_conversion_blockers_require_a_recognized_field_repair(
    data, api, publishable, field, code, new_title, expected
):
    publishable["action"] = "save_draft"
    created = await post(api, "/admin/scenarios", publishable)
    path = f"/admin/scenarios/{created.json()['id']}"
    async with data.factory() as db:
        scenario = await db.get(Scenario, created.json()["id"])
        scenario.review_required = True
        scenario.review_reasons = [
            dict(path=field, code=code, message="원문 보완 필요", blocking=True)
        ]
        await db.commit()
    before = (await api.get(path)).json()
    publishable.update(
        action="publish",
        expected_version=1,
        acknowledge_review=True,
        title=new_title,
    )
    response = await post(api, path + "/update", publishable)
    assert response.status_code == expected, response.text
    if expected == 422:
        assert (await api.get(path)).json() == before
    else:
        assert response.json()["review_required"] is False


async def test_new_install_seed_publishes_without_templates_or_frameworks(
    data, api, publishable, monkeypatch
):
    from legacy_models import AnalysisFramework, PromptTemplate
    from sqlalchemy import delete, select, text
    from test_scenario_api import login

    from src.db import seed
    from src.models import ScenarioGroup, Session, User

    # Remove only the historical fixture before exercising a new installation.
    async with data.factory() as db:
        await db.execute(delete(Session))
        await db.execute(delete(ScenarioGroup))
        await db.execute(delete(Scenario))
        await db.execute(delete(AnalysisFramework))
        await db.execute(delete(PromptTemplate))
        await db.commit()
    monkeypatch.setattr(seed, "AsyncSessionLocal", data.factory)
    await seed.seed_database()
    async with data.factory() as db:
        seeded = (await db.scalars(select(Scenario))).one()
        scenario_id = seeded.id
        admin = await db.scalar(select(User).where(User.username == "admin"))
        assert (
            await db.execute(
                text(
                    "SELECT name FROM sqlite_master WHERE name IN ('analysis_framework','prompt_template')"
                )
            )
        ).all() == []
    login(api, admin)
    await api.get("/admin/scenarios")
    path = f"/admin/scenarios/{scenario_id}"
    saved = (await api.get(path)).json()
    assert saved["status"] == "draft"
    for role in ("student", "analysis"):
        saved["config"][role]["resolved_model_config"] = publishable["config"][
            role
        ]["resolved_model_config"]
    response = await post(
        api,
        path + "/update",
        dict(
            title=saved["title"],
            subject=saved["subject"],
            target_grade=saved["target_grade"],
            groups=saved["groups"],
            config=saved["config"],
            config_schema_version=1,
            expected_version=1,
            action="publish",
        ),
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "published"
    assert response.json()["version"] == 2
    listing = await api.get("/scenarios")
    assert saved["title"] in listing.text
