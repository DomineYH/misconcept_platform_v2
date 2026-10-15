"""Admin display language through real request/render boundaries."""

import json
import re
from types import SimpleNamespace

import pytest
from starlette.requests import Request

from src.api.dependencies import templates


def request(language):
    return Request(
        {
            "type": "http",
            "path": "/admin",
            "headers": [(b"x-ui-language", language.encode())],
            "scheme": "http",
            "server": ("test", 80),
            "query_string": b"",
        }
    )


@pytest.mark.parametrize(
    "language,title", [("en", "Admin dashboard"), ("ko", "관리자 대시보드")]
)
def test_dashboard_labels_follow_request_language(language, title):
    html = templates.env.get_template("admin/dashboard.html").render(
        request=request(language),
        user=SimpleNamespace(role="admin", nickname="원문 사용자"),
    )
    assert f"<h1>{title}</h1>" in html
    strings = json.loads(
        html.split('id="ui-strings" type="application/json">')[1].split(
            "</script>"
        )[0]
    )
    assert strings["admin.dashboard"] == title
    assert "원문 사용자" in html


@pytest.mark.parametrize(
    "page,title",
    [
        ("about", "About management"),
        ("user_conversations", "User conversations"),
        ("sessions", "Dialogue sessions"),
        ("analysis", "Analysis management"),
    ],
)
def test_admin_surrounding_labels_translate_without_changing_content(
    page, title
):
    from tests.uiux_admin_fixtures import admin_fixture

    template, context = admin_fixture(page)
    html = templates.env.get_template(template).render(
        request=request("en"), **context
    )
    assert re.search(r"<h1\b[^>]*>\s*" + re.escape(title) + r"\s*</h1>", html)
    assert "긴 사용자 이름" in html
    assert html.count('id="ui-strings"') == 1


@pytest.mark.parametrize(
    "language,label", [("ko", "검토 필요"), ("en", "Review required")]
)
def test_review_badge_is_distinct_from_required_field_notice(language, label):
    scenario = SimpleNamespace(
        id=1,
        title="원문 제목",
        is_active=1,
        status="draft",
        review_required=True,
        config_json={"problem": {"public_text": "원문 문제"}},
    )
    html = templates.env.get_template("admin/scenarios.html").render(
        request=request(language),
        user=SimpleNamespace(role="admin", nickname="Admin"),
        scenarios=[scenario],
        groups=[],
        scenario_group_map={},
        session_counts={1: 0},
    )
    assert f'<span class="meta-tag">{label}</span>' in html
    assert "원문 제목" in html
