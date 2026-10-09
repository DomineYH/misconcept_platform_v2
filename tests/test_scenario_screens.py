"""Rendered screens keep legacy video data out of the browser."""

from types import SimpleNamespace

import pytest

from src.api.dependencies import templates


@pytest.fixture
def scenario():
    return SimpleNamespace(
        id=1,
        title="Screen scenario",
        student_name="Student",
        student_profile="Public student profile",
        subject="Math",
        problem_situation="Public problem <script>unsafe()</script>",
        greeting_message="Mentor hello <script>unsafe()</script>",
        prompt="PRIVATE STUDENT PROMPT",
        video_url="https://www.youtube.com/watch?v=legacy-secret",
        video_transcript="PRIVATE LEGACY TRANSCRIPT",
    )


def render_chat(scenario):
    return templates.get_template("chat.html").render(
        scenario=scenario,
        session_id=1,
        student_name=scenario.student_name,
        user=SimpleNamespace(nickname="Teacher", role="teacher"),
        messages=[],
    )


def test_teacher_sees_public_problem_profile_and_mentor_without_video(scenario):
    html = render_chat(scenario)
    assert "Public problem &lt;script&gt;unsafe()&lt;/script&gt;" in html
    assert "Public student profile" in html
    assert "Mentor hello &lt;script&gt;unsafe()&lt;/script&gt;" in html
    for removed in (
        'data-tab="video"',
        "<iframe",
        "videoUrl",
        "영상",
        scenario.video_url,
        scenario.video_transcript,
        scenario.prompt,
    ):
        assert removed not in html


def test_admin_forms_do_not_expose_legacy_video(scenario):
    scenario.is_active = 1
    scenario.config_json = {
        "problem": {"public_text": scenario.problem_situation}
    }
    scenario.status = "draft"
    scenario.config_version = 1
    scenario.review_required = True
    html = templates.get_template("admin/scenarios.html").render(
        user=SimpleNamespace(nickname="Admin", role="admin"),
        scenarios=[scenario],
        groups=[],
        scenario_group_map={},
        session_counts={1: 0},
    )
    for removed in (
        "video_url",
        "video_transcript",
        "영상",
        "YouTube",
        scenario.video_url,
        scenario.video_transcript,
    ):
        assert removed not in html
    assert 'href="/admin/scenarios/new"' in html
    assert 'href="/admin/scenarios/1/edit"' in html
    assert 'data-version="1"' in html
    for retired in (
        "framework_id",
        "student_template_id",
        "tutor_template_id",
        'id="create-form"',
        scenario.prompt,
    ):
        assert retired not in html


@pytest.mark.parametrize("problem", [None, "", " \n\t "])
def test_missing_public_problem_requests_completion_without_prompt(
    scenario, problem
):
    scenario.problem_situation = problem
    listing = templates.get_template("scenarios.html").render(
        scenarios=[scenario],
        user=SimpleNamespace(nickname="Teacher", role="teacher"),
    )
    for html in (render_chat(scenario), listing):
        assert "문제 상황 보완 필요" in html
        assert scenario.prompt not in html
        assert scenario.video_transcript not in html
    assert 'href="/scenarios/1"' not in listing
    assert "disabled" in listing
