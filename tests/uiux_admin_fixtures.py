"""Synthetic administrator screens; never use operating data or providers."""

from datetime import datetime
from types import SimpleNamespace


def contributors():
    return [
        dict(
            id=1,
            name='긴 소개 이름 "따옴표" ' * 8,
            affiliation="합성 소속 " * 15,
            bio="여러 단락의 합성 이력\n" * 30,
            phone=None,
            email="synthetic@example.test",
            sort_order=0,
        )
    ]


def admin_fixture(name, *, empty=False, active=False):
    user = SimpleNamespace(
        id=1,
        username="synthetic",
        nickname="긴 사용자 이름 " * 12,
        role="admin",
        group_id=None,
        group=None,
        created_at=datetime(2026, 10, 1),
    )
    context = dict(user=user)
    if name == "about":
        context["contributors"] = [] if empty else contributors()
    elif name == "user_conversations":
        context["users"] = (
            []
            if empty
            else [
                dict(
                    user=user,
                    session_count=2,
                    ended_session_count=1,
                    last_session=datetime(2026, 10, 1),
                )
            ]
        )
    elif name in {"sessions", "row"}:
        context.update(
            sessions=(
                []
                if empty
                else [
                    SimpleNamespace(
                        id=1,
                        teacher=user,
                        summary=True,
                        snapshot_origin="native",
                        started_at=datetime(2026, 10, 1),
                        ended_at=None if active else datetime(2026, 10, 1, 10),
                    )
                ]
            ),
            teachers=[user],
            current_teacher_id=None,
            current_date_from="",
            current_date_to="",
            total_pages=1,
            total_count=0 if empty else 1,
            current_page=1,
            analysis_session_ids=set(),
            session_display=lambda session: dict(
                scenario_title="긴 시나리오 이름 " * 20,
                snapshot_provenance=dict(snapshot_origin="native"),
            ),
        )
        if name == "row":
            context["session"] = context["sessions"][0]
            return "partials/session_row.html", context
    elif name == "analysis":
        context.update(
            analyses=(
                []
                if empty
                else [
                    dict(
                        id=1,
                        content="긴 분석 질문 " * 40,
                        label="Recall",
                        confidence=None,
                        session_id=1,
                        snapshot_provenance=dict(snapshot_origin="native"),
                        scenario_title="긴 시나리오 " * 20,
                        created_at=datetime(2026, 10, 1),
                    )
                ]
            ),
            scenarios=[],
            available_labels={"Recall": "회상"},
            stats=dict(
                total_analyses=0 if empty else 1,
                avg_confidence=None,
                most_common_label="회상",
            ),
            current_filters=dict(
                scenario_id=None, label=None, date_from=None, date_to=None
            ),
            pagination=dict(page=1, total_pages=1, total=0 if empty else 1),
        )
    elif name == "detail":
        context.update(
            analysis=dict(
                id=1,
                content="긴 원문 질문\n" * 60,
                label="Recall",
                grade=None,
                confidence=None,
                reasoning=None,
                session_id=1,
                scenario_title="합성 시나리오",
                created_at=datetime(2026, 10, 1),
                snapshot_provenance=dict(snapshot_origin="native"),
            )
        )
        return "partials/analysis_detail_modal.html", context
    return f"admin/{name}.html", context
