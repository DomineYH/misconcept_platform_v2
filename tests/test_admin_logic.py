from datetime import datetime

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from src.api.routes import (
    admin_frameworks,
    admin_scenarios,
    admin_session_actions,
    session_analysis,
)
from src.api.schemas import FrameworkUpdateWeb, ScenarioUpdate
from src.models import (
    Message,
    PromptTemplate,
    QuestionAnalysis,
    Session,
    SessionSummary,
)
from src.models.scenario_group import ScenarioGroup


async def test_user_and_admin_analysis_contract(data):
    sid = data.session.id
    first = Message(
        session_id=sid,
        role="teacher",
        content="first",
        created_at=datetime(2026, 1, 1),
    )
    later = Message(
        session_id=sid,
        role="teacher",
        content="later",
        created_at=datetime(2026, 1, 1, 0, 1),
    )
    data.db.add_all(
        [
            later,
            first,
            SessionSummary(
                session_id=sid, distribution_json='{"A": 1}', feedback="legacy"
            ),
        ]
    )
    await data.db.flush()
    data.db.add(
        QuestionAnalysis(
            message_id=first.id,
            label="A",
            grade="우수",
            confidence=0.8,
            meta_json='{"reason": "test"}',
        )
    )
    await data.db.commit()
    normal = await session_analysis.get_analysis(sid, data.owner, data.db)
    admin = await admin_session_actions._load_analysis_response(sid, data.db)
    assert normal == admin
    assert set(normal) == {
        "distribution",
        "classification_enabled",
        "label_names",
        "feedback",
        "feedback_status",
        "retryable",
        "feedback_sections",
        "stats",
        "questions",
        "messages",
        "framework_label_criteria",
        "grade_counts",
        "session_ended_at",
    }
    assert normal["feedback_status"] == "legacy"
    assert [q["content"] for q in normal["questions"]] == ["first", "later"]
    assert normal["questions"][1]["label"] == "Unclassified"
    assert normal["messages"][0]["level"] == "high"
    assert normal["grade_counts"] == {"우수": 1, "개선": 0}
    assert normal["stats"]["duration_seconds"] == 86400
    with pytest.raises(HTTPException) as denied:
        await session_analysis.get_analysis(sid, data.other, data.db)
    assert denied.value.status_code == 403


async def test_scenario_update_null_omitted_templates_and_groups(data):
    student = PromptTemplate(
        bot_type="student",
        template_name="student",
        template_text="student template",
    )
    tutor = PromptTemplate(
        bot_type="tutor", template_name="tutor", template_text="tutor template"
    )
    data.db.add_all([student, tutor])
    await data.db.flush()
    scenario = data.scenario
    sid = scenario.id
    update = ScenarioUpdate(
        problem_situation=" problem ",
        greeting_message=" hello ",
        student_name=" Name ",
        subject=" Math ",
        student_template_id=student.id,
        tutor_template_id=tutor.id,
        chat_model="gpt-5",
        chat_temperature=0.5,
    )
    await admin_scenarios.update_scenario(sid, update, data.admin, data.db)
    assert (
        scenario.problem_situation,
        scenario.greeting_message,
        scenario.student_name,
        scenario.subject,
    ) == ("problem", "hello", "Name", "Math")
    await admin_scenarios.update_scenario(
        sid, ScenarioUpdate(), data.admin, data.db
    )
    await admin_scenarios.update_scenario(
        sid,
        ScenarioUpdate(
            **{k: None for k in update.model_fields_set}, group_ids=None
        ),
        data.admin,
        data.db,
    )
    assert (
        scenario.problem_situation,
        scenario.greeting_message,
        scenario.student_name,
        scenario.chat_model,
        scenario.chat_temperature,
        scenario.tutor_template_id,
    ) == ("problem", "hello", "Name", "gpt-5", 0.5, tutor.id)
    assert len((await data.db.scalars(select(ScenarioGroup))).all()) == 1
    await admin_scenarios.update_scenario(
        sid,
        ScenarioUpdate(
            problem_situation="",
            greeting_message="",
            tutor_template_id=-1,
            group_ids=[],
        ),
        data.admin,
        data.db,
    )
    assert (
        scenario.problem_situation is None and scenario.greeting_message is None
    )
    assert scenario.tutor_template_id is None
    assert (await data.db.scalars(select(ScenarioGroup))).all() == []
    with pytest.raises(HTTPException) as invalid:
        await admin_scenarios.update_scenario(
            sid,
            ScenarioUpdate(student_template_id=tutor.id),
            data.admin,
            data.db,
        )
    assert invalid.value.status_code == 400


async def test_framework_update_delete_and_scenario_soft_delete(data):
    fid, sid = data.framework.id, data.session.id
    await admin_frameworks.update_framework_web(
        fid, FrameworkUpdateWeb(category_name="category"), data.admin, data.db
    )
    await admin_frameworks.update_framework_web(
        fid, FrameworkUpdateWeb(), data.admin, data.db
    )
    assert data.framework.category_name == "category"
    await admin_frameworks.update_framework_web(
        fid, FrameworkUpdateWeb(category_name=None), data.admin, data.db
    )
    assert data.framework.category_name is None
    with pytest.raises(HTTPException) as conflict:
        await admin_frameworks.delete_framework_web(fid, data.admin, data.db)
    assert conflict.value.status_code == 409
    await admin_scenarios.delete_scenario(data.scenario.id, data.admin, data.db)
    assert (await data.db.get(Session, sid)).deleted_at is not None
    assert (await data.db.get(Session, sid)).teacher_id == data.owner.id
    result = await admin_frameworks.delete_framework_web(
        fid, data.admin, data.db
    )
    assert result["status"] == "deleted"
    assert (await data.db.scalars(select(Session))).all() == []
