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


@pytest.mark.parametrize("language", ["ko", "en"])
@pytest.mark.parametrize(
    "status,active,review,native",
    [
        ("published", 1, True, True),
        ("draft", 0, False, True),
        ("published", 0, True, False),
    ],
)
def test_admin_scenario_badges_distinguish_status_meanings(
    scenario, language, status, active, review, native
):
    from starlette.requests import Request

    labels = {
        "ko": ("게시됨", "초안", "활성", "비활성", "검토 필요", "변환 필요"),
        "en": (
            "Published",
            "Draft",
            "Active",
            "Inactive",
            "Review required",
            "Conversion required",
        ),
    }[language]
    published, draft, enabled, disabled, review_label, conversion = labels
    scenario.status = status
    scenario.is_active = active
    scenario.review_required = review
    scenario.config_version = 1
    scenario.config_json = (
        {"problem": {"public_text": "Problem"}} if native else None
    )
    html = templates.get_template("admin/scenarios.html").render(
        request=Request(
            {
                "type": "http",
                "path": "/admin/scenarios",
                "query_string": b"",
                "headers": [(b"x-ui-language", language.encode())],
                "scheme": "http",
                "server": ("test", 80),
            }
        ),
        user=SimpleNamespace(nickname="Admin", role="admin"),
        scenarios=[scenario],
        groups=[],
        scenario_group_map={},
        session_counts={},
    )
    is_published = status == "published" and native
    publication_class = "meta-tag badge-success" if is_published else "meta-tag"
    publication_label = published if is_published else draft
    assert (
        f'<span class="{publication_class}">{publication_label}</span>' in html
    )
    assert (
        f'<span class="status-badge">{enabled if active else disabled}</span>'
        in html
    )
    assert 'class="status-badge active"' not in html
    assert (
        f'data-publication="{"published" if is_published else "draft"}"' in html
    )
    assert ('class="scenario-card inactive"' in html) == (not active)
    assert (
        f'<span class="meta-tag badge-warning">{review_label}</span>' in html
    ) == review
    assert (
        f'<span class="meta-tag badge-warning">{conversion}</span>' in html
    ) == (not native)
    assert f'<option value="published">{published}</option>' in html


@pytest.mark.parametrize(
    "subject", ["Math", "긴교과이름" * 20, "<script>unsafe()</script>"]
)
def test_teacher_subject_chip_uses_only_escaped_public_content(
    scenario, subject
):
    from markupsafe import escape

    scenario.subject = subject
    scenario.internal_profile = "PRIVATE STUDENT PROFILE"
    html = templates.get_template("scenarios.html").render(
        scenarios=[scenario],
        user=SimpleNamespace(nickname="Teacher", role="teacher"),
    )
    assert f'<span class="scenario-subject">{escape(subject)}</span>' in html
    assert html.index('class="scenario-subject"') < html.index(
        'class="scenario-title"'
    )
    assert 'href="/scenarios/1" class="btn-primary"' in html
    assert scenario.student_profile in html
    for private in (
        scenario.internal_profile,
        scenario.prompt,
        scenario.video_transcript,
    ):
        assert private not in html


@pytest.mark.parametrize("subject", [None, "", " \n\t "])
def test_teacher_subject_chip_omitted_when_blank(scenario, subject):
    scenario.subject = subject
    html = templates.get_template("scenarios.html").render(
        scenarios=[scenario],
        user=SimpleNamespace(nickname="Teacher", role="teacher"),
    )
    assert 'class="scenario-subject"' not in html
    assert scenario.title in html
    assert 'href="/scenarios/1" class="btn-primary"' in html


@pytest.mark.parametrize(
    "mode,greeting,has_messages,ended,show_hint",
    [
        ("off", "Configured but hidden", False, False, True),
        ("off", None, True, False, False),
        ("manual", "Welcome", False, False, False),
        ("manual", "Welcome", True, False, False),
        ("manual", None, False, False, True),
        ("auto", " \n\t ", False, False, True),
        ("off", None, False, True, False),
    ],
)
def test_chat_first_question_hint_matches_visible_conversation(
    scenario, mode, greeting, has_messages, ended, show_hint
):
    scenario.mentor_mode = mode
    scenario.greeting_message = greeting
    message = SimpleNamespace(
        id=1, role="teacher", content="Saved question", created_at=None
    )
    html = templates.get_template("chat.html").render(
        scenario=scenario,
        session_id=1,
        student_name=scenario.student_name,
        user=SimpleNamespace(nickname="Teacher", role="teacher"),
        messages=[message] if has_messages else [],
        session_ended=ended,
    )
    assert ('id="chat-empty-state"' in html) is show_hint
    assert ('class="message message-mentor greeting-message"' in html) is (
        mode != "off" and bool(greeting and greeting.strip())
    )
    if show_hint:
        assert (
            '<p id="chat-empty-state">'
            "문제 상황을 확인하고 학생에게 첫 질문을 보내세요.</p>"
        ) in html


def test_chat_heading_escapes_public_name_and_defaults_to_situation(scenario):
    scenario.student_name = "<script>public name</script>"
    html = render_chat(scenario)
    assert (
        '<h2 id="chat-title">'
        "&lt;script&gt;public name&lt;/script&gt;와의 대화</h2>"
    ) in html
    assert (
        'class="info-tab active" data-tab="situation" role="tab" '
        'id="info-tab-situation" aria-controls="tab-situation" '
        'aria-selected="true" tabindex="0"'
    ) in html
    assert 'class="tab-pane active" id="tab-situation"' in html
    assert 'class="tab-pane active" id="tab-guide"' not in html
