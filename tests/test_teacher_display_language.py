"""Teacher display language through the request/render boundary."""

from types import SimpleNamespace

from test_display_language import request

from src.api.dependencies import templates


def test_chat_heading_and_first_question_hint_follow_display_language():
    scenario = SimpleNamespace(
        title="원본 제목",
        student_name=None,
        mentor_mode="off",
        student_profile="원본 소개",
        problem_situation="원본 문제",
        greeting_message="Hidden welcome",
        subject="수학",
    )
    for language, fallback, hint in (
        (
            "ko",
            "학생봇와의 대화",
            "문제 상황을 확인하고 학생에게 첫 질문을 보내세요.",
        ),
        (
            "en",
            "Conversation with Student bot",
            "Read the problem statement and send the student your first question.",
        ),
    ):
        for name in (None, " \t ", "공개 이름"):
            scenario.student_name = name
            html = templates.get_template("chat.html").render(
                request=request(language),
                scenario=scenario,
                session_id=1,
                student_name=name,
                user=SimpleNamespace(nickname="교사", role="teacher"),
                messages=[],
            )
            heading = fallback
            if name == "공개 이름":
                heading = (
                    "공개 이름와의 대화"
                    if language == "ko"
                    else "Conversation with 공개 이름"
                )
            assert f'<h2 id="chat-title">{heading}</h2>' in html
            assert f'<p id="chat-empty-state">{hint}</p>' in html
            assert "원본 문제" in html
            assert "Hidden welcome" not in html


def test_scenario_selection_round_trip_preserves_public_content():
    scenario = SimpleNamespace(
        id=7,
        title="원본 시나리오 <script>unsafe()</script>",
        problem_situation="원본 문제",
        student_profile="원본 학생 소개",
    )
    for language, heading, action in (
        ("ko", "시나리오 선택", "대화 시작"),
        ("en", "Choose a scenario", "Start conversation"),
        ("ko", "시나리오 선택", "대화 시작"),
    ):
        html = templates.get_template("scenarios.html").render(
            request=request(language, "en" if language == "ko" else "ko"),
            user=SimpleNamespace(nickname="교사", role="teacher"),
            scenarios=[scenario],
        )
        assert heading in html
        assert action in html
        assert "원본 시나리오 &lt;script&gt;unsafe()&lt;/script&gt;" in html
        assert "원본 문제" in html
        assert "원본 학생 소개" in html


def test_chat_controls_translate_without_changing_lesson_or_role_names():
    import json

    scenario = SimpleNamespace(
        title="원본 제목",
        student_name="학생 원본 이름",
        mentor_name="멘토 원본 이름",
        mentor_mode="auto",
        student_profile="원본 소개",
        problem_situation="원본 문제",
        greeting_message="원본 멘토 인사",
        subject="수학",
    )
    for language, action in (
        ("ko", "대화 종료 후 분석 보기"),
        ("en", "End conversation and view analysis"),
        ("ko", "대화 종료 후 분석 보기"),
    ):
        html = templates.get_template("chat.html").render(
            request=request(language),
            user=SimpleNamespace(nickname="교사", role="teacher"),
            scenario=scenario,
            session_id=1,
            student_name=scenario.student_name,
            messages=[],
        )
        assert action in html
        for content in vars(scenario).values():
            if content not in ("auto",):
                assert content in html
        strings = json.loads(
            html.split('<script id="ui-strings" type="application/json">')[
                1
            ].split("</script>")[0]
        )
        assert strings["teacher.end_and_analyze"] == action
        if language == "en":
            assert 'aria-label="Teacher message"' in html
            assert 'placeholder="Enter a message..."' in html


def test_shared_message_and_analysis_fragments_use_request_language():
    from datetime import datetime

    message = SimpleNamespace(
        id=5,
        role="tutor",
        content="원본 코칭 <script>unsafe()</script>",
        turn_id=None,
        created_at=None,
    )
    user = SimpleNamespace(nickname="교사", role="teacher")
    session = SimpleNamespace(
        id=1,
        started_at=datetime(2026, 1, 1),
        ended_at=None,
        deleted_at=None,
        teacher=None,
        messages=[message],
    )
    context = dict(
        request=request("en", "ko"),
        user=user,
        session=session,
        session_id=1,
        scenario_title="원본 제목",
        student_name="원본 학생",
        message=message,
        messages=[message],
        feedback="원본 분석",
        feedback_status="degraded",
        feedback_sections={},
        distribution={},
        questions=[],
        analysis=SimpleNamespace(
            id=3,
            session_id=1,
            content="원본 질문",
            label="원본 분류",
            grade="원본 평가",
            confidence=0.8,
            reasoning=None,
            scenario_title="원본 제목",
            snapshot_provenance=None,
            created_at=datetime(2026, 1, 1),
        ),
    )
    for template, expected in (
        ("partials/message.html", "Mentor"),
        ("partials/analysis_modal.html", "Session analysis results"),
        ("partials/analysis_detail_modal.html", "Analysis details (ID: 3)"),
        ("partials/analysis_detail_tabs.html", "Detailed analysis"),
        ("partials/session_detail.html", "Session ID"),
        ("partials/analysis_result.html", "Post-session analysis results"),
        ("analysis.html", "Session analysis report"),
        ("session_history.html", "Conversation history"),
    ):
        html = templates.get_template(template).render(**context)
        assert expected in html, template
    html = templates.get_template("partials/message.html").render(**context)
    assert "원본 코칭 &lt;script&gt;unsafe()&lt;/script&gt;" in html


def test_history_metadata_translates_without_rewriting_snapshot_fields():
    provenance = dict(
        snapshot_origin="legacy_reconstructed",
        snapshot_created_at=None,
        unknown_fields=[
            "scenario_context",
            "config.student.name",
            "config.student.model",
        ],
        unknown_field_names=[
            "시나리오 제목·과목·학년",
            "학생봇 소개·지시·모델·옵션",
        ],
    )
    html = templates.get_template("partials/snapshot_provenance.html").render(
        request=request("en"),
        snapshot_provenance=provenance,
    )
    assert "Scenario title, subject and grade" in html
    assert "Student bot profile, instructions, model and options" in html
    assert (
        html.count("Student bot profile, instructions, model and options") == 1
    )
    assert provenance["unknown_fields"] == [
        "scenario_context",
        "config.student.name",
        "config.student.model",
    ]


def test_shared_session_row_localizes_actions_and_confirmation():
    from datetime import datetime

    html = templates.get_template("partials/session_row.html").render(
        request=request("en"),
        session=SimpleNamespace(
            id=1,
            teacher=None,
            started_at=datetime(2026, 1, 1),
            ended_at=None,
            snapshot_origin="native",
        ),
        session_display=lambda session: dict(
            scenario_title="원본 제목",
            snapshot_provenance=dict(snapshot_origin="native"),
        ),
    )
    assert 'aria-label="Active sessions cannot be downloaded"' in html
    assert 'hx-confirm="End and analyze this session?"' in html
    assert 'hx-confirm="Delete this session? (Cannot be undone)"' in html
    assert "원본 제목" in html


def test_chunk_notice_translates_on_initial_page_and_shared_modal():
    notice = "부분 분석: 검증된 6 / 7개 메시지의 결과입니다. 완료·실패·미실행 범위는 분석 범위에서 확인하세요."
    for language, expected in (
        (
            "en",
            "Partial analysis: validated results for 6 / 7 messages. Check analysis coverage for completed, failed and unprocessed ranges.",
        ),
        ("ko", notice),
    ):
        for template in ("analysis.html", "partials/analysis_modal.html"):
            html = templates.get_template(template).render(
                request=request(language),
                user=SimpleNamespace(nickname="교사", role="teacher"),
                session_id=1,
                analysis_result_url="/sessions/1/analysis",
                feedback=notice,
                feedback_status="degraded",
                accepted_report={
                    "status": "degraded",
                    "coverage": {
                        "reviewed_message_ids": list(range(6)),
                        "input_message_ids": list(range(7)),
                        "chunks": [{"status": "failed"}],
                    },
                },
            )
            assert f"<p>{expected}</p>" in html
            if language == "en":
                assert f"<p>{notice}</p>" not in html


def test_failure_and_no_dialogue_notices_use_status_without_rewriting_korean():
    for status, stored, english in (
        (
            "failed",
            "보관된 과거 실패 안내",
            "Analysis feedback could not be generated.",
        ),
        (
            "no_dialogue",
            "대화가 없어 분석할 수 없습니다.",
            "There is no conversation to analyze.",
        ),
    ):
        for language in ("en", "ko"):
            for template in ("analysis.html", "partials/analysis_modal.html"):
                for initial in (False, True):
                    html = templates.get_template(template).render(
                        request=request(language),
                        user=SimpleNamespace(nickname="교사", role="teacher"),
                        session_id=1,
                        feedback=stored,
                        feedback_status=status,
                        classification_enabled=False,
                        distribution={},
                        questions=[],
                        messages=[],
                        feedback_sections={},
                        **(
                            {"analysis_result_url": "/sessions/1/analysis"}
                            if initial
                            else {}
                        ),
                    )
                    expected = english if language == "en" else stored
                    assert f"<p>{expected}</p>" in html
