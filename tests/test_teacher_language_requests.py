"""Teacher language at authenticated HTTP and isolated SDK boundaries."""

from datetime import datetime
from uuid import uuid4

import pytest
from test_mentor_generation import mentor as mentor_fixture
from test_scenario_api import client as client_fixture
from test_scenario_api import login
from test_scenario_api import scenario_payload as scenario_fixture
from test_student_generation import student as student_fixture

client = client_fixture
scenario_payload = scenario_fixture
student = student_fixture
mentor = mentor_fixture


@pytest.mark.parametrize("action", ["end", "analyze_regenerate"])
async def test_admin_shared_legacy_error_uses_request_language(
    data, client, action
):
    login(client, data.admin)
    client.cookies.set("ui_lang", "ko")
    response = await client.post(
        f"/admin/sessions/{data.session.id}/{action}",
        json={"request_id": str(uuid4())},
        headers={"X-UI-Language": "en"},
    )
    assert response.status_code == 409
    assert response.json() == {
        "detail": {
            "code": "legacy_read_only",
            "message": "Historical sessions are read only. Start a new practice with a published scenario.",
        }
    }


async def test_ended_student_request_has_stable_code_and_localized_message(
    data, client, student
):
    data.session.ended_at = datetime(2026, 1, 1)
    await data.db.commit()
    login(client, data.owner)
    for language, message in (
        ("en", "The conversation has ended."),
        ("ko", "대화가 종료되었습니다."),
    ):
        response = await client.post(
            f"/sessions/{data.session.id}/turns/stream",
            json={"request_id": str(uuid4()), "content": "원본 질문"},
            headers={"X-UI-Language": language},
        )
        assert response.status_code == 400
        assert response.json()["detail"] == {
            "code": "session_ended",
            "message": message,
        }
    student.responses.create.assert_not_called()


async def test_student_sse_failure_localizes_notice_and_preserves_output(
    data, client, student
):
    from test_student_generation import event, frames

    login(client, data.owner)
    client.cookies.set("ui_lang", "ko")
    student.stream.events = [
        event("response.output_text.delta", delta="원본 잠정 응답"),
        event("error", code="server_error", message="PRIVATE PROVIDER ERROR"),
    ]
    response = await client.post(
        f"/sessions/{data.session.id}/turns/stream",
        json={"request_id": str(uuid4()), "content": "원본 질문"},
        headers={"X-UI-Language": "en"},
    )
    events = frames(response)
    assert events[1][1]["text"] == "원본 잠정 응답"
    assert events[-1][0] == "run.failed"
    assert events[-1][1]["code"] == "transient"
    assert (
        events[-1][1]["message"] == "Could not generate the student response."
    )
    assert "PRIVATE PROVIDER ERROR" not in response.text


async def test_mentor_sse_failure_localizes_notice_without_exposing_raw_output(
    data, client, mentor
):
    from types import SimpleNamespace

    from test_mentor_generation import complete_turn, mentor_url
    from test_student_generation import frames

    login(client, data.owner)
    turn = await complete_turn(client, data)
    mentor.responses.create.side_effect = None
    mentor.responses.create.return_value = SimpleNamespace(
        output_text="PRIVATE INVALID MENTOR OUTPUT", usage=None
    )
    response = await client.post(
        mentor_url(data, turn),
        json={"request_id": str(uuid4()), "trigger": "manual"},
        headers={"X-UI-Language": "en"},
    )
    events = frames(response)
    assert events[-1][0] == "run.failed"
    assert events[-1][1]["code"] == "invalid_json"
    assert events[-1][1]["message"] == "Could not generate mentor coaching."
    assert "PRIVATE INVALID MENTOR OUTPUT" not in response.text


async def test_http_fragments_and_errors_follow_header_over_cookie(
    data, client
):
    from src.models import Message

    data.db.add(
        Message(
            session_id=data.session.id,
            role="tutor",
            content="원본 코칭 <script>unsafe()</script>",
        )
    )
    await data.db.commit()
    login(client, data.owner)
    for language, mentor_name, error_message in (
        ("en", "Mentor", "Run not found"),
        ("ko", "멘토", "실행을 찾을 수 없습니다."),
    ):
        client.cookies.set("ui_lang", "ko" if language == "en" else "en")
        headers = {"X-UI-Language": language, "HX-Request": "true"}
        fragment = await client.get(
            f"/sessions/{data.session.id}/messages/updates", headers=headers
        )
        assert fragment.status_code == 200
        assert f'class="message-sender">{mentor_name}</div>' in fragment.text
        assert (
            "원본 코칭 &lt;script&gt;unsafe()&lt;/script&gt;" in fragment.text
        )
        missing = await client.get(f"/runs/{uuid4()}", headers=headers)
        assert missing.status_code == 404
        assert missing.json() == {"detail": error_message}
