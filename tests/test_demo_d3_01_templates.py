"""D3-01 PR①: demo layout data, CSRF readers and links keep normal markup intact."""

import json
import re
from types import SimpleNamespace

import pytest

from src.api.dependencies import templates

NORMAL = {"mode": "normal", "public_url": None}
NORMAL_LINKED = {"mode": "normal", "public_url": "https://demo.example.test/"}
DEMO = {
    "mode": "demo",
    "contract_version": "d0-03-v1-r3",
    "content_version": "c1",
    "fixture_version": "f1",
    "state": "ready",
    "browser_scope_id": "scope-1",
    "generation": 3,
    "auth_epoch": 2,
    "role": "teacher",
    "expires_at": "2026-10-20T08:00:00Z",
    "idle_ttl_seconds": 1800,
    "absolute_ttl_seconds": 28800,
    "active": False,
    "last_error": "</script><script>alert(1)</script>",
    "start_url": "/demo/start",
    "role_url": "/demo/role",
    "reset_url": "/demo/reset",
    "state_url": "/demo/state",
    "public_url": "https://demo.example.test/",
    "csrf_cookie_name": "demo_csrftoken",
    "csrf_header_name": "x-csrf-token",
    "ui_language": "ko",
    "tour_facts": None,
}
LEAKS = {
    "db_path": "/srv/demo/visitor.sqlite",
    "visitor_id": "visitor-secret",
    "user_id": 9876543210,
    "csrf_token": "token-value",
    "session_secret": "s3cret",
}
SHAPES = {"undefined": None, "normal": NORMAL, "demo": DEMO}
TEACHER = SimpleNamespace(nickname="Teacher", role="teacher")
ADMIN = SimpleNamespace(nickname="Admin", role="admin")

# Normal markup as it was before D3-01 (main 3b967f1).
HTMX_READER = """    document.addEventListener('htmx:configRequest', function(e) {
      var match = document.cookie.match(
        /(?:^|;\\s*)csrftoken=([^;]*)/
      );
      if (match) {
        e.detail.headers['x-csrf-token'] = match[1];
      }
    });"""
LOGOUT_READER = """      var match = document.cookie.match(
        /(?:^|;\\s*)csrftoken=([^;]*)/
      );
      var headers = uiLanguage.headers('/logout');
      if (match) {
        headers.set('x-csrf-token', match[1]);
      }"""
HEAD_START = '<!DOCTYPE html>\n\n<html lang="ko">\n<head>'
BODY_START = (
    '<body>\n  <a href="#main-content" class="skip-link">본문으로 건너뛰기</a>\n'
    "  \n  <header>"
)
STYLES = (
    '<link rel="stylesheet" href="/static/css/styles.css">\n'
    '  <script id="ui-strings" type="application/json">'
)
FOOTER = "      {} |\n    </p>\n  </footer>"


def render(name, shape, **context):
    if SHAPES[shape] is not None:
        context["demo_config"] = dict(SHAPES[shape])
    return templates.get_template(name).render(**context)


def demo_json(html):
    match = re.search(
        r'<script type="application/json" id="demo-config">(.*?)</script>', html
    )
    return match and json.loads(match.group(1))


def page(name, shape):
    return render(name, shape, **page_ctx(name))


@pytest.mark.parametrize("name", ["error.html", "login.html", "scenarios.html"])
def test_undefined_demo_config_keeps_pre_change_layout_fragments(name):
    html = page(name, "undefined")
    assert html.startswith(HEAD_START)
    assert STYLES in html
    assert HTMX_READER in html
    assert LOGOUT_READER in html
    assert BODY_START in html
    assert re.search(r"v0\.1\.0 \|\n.*\n    </p>\n  </footer>", html)
    for absent in (
        "demo-config",
        "data-demo-",
        "demo-banner",
        "demo-tour.css",
        "demo-public-link",
        "demo_csrftoken",
        "_demo_",
        "ds=",
    ):
        assert absent not in html


@pytest.mark.parametrize("name", ["error.html", "login.html", "scenarios.html"])
def test_normal_shape_adds_only_config_json(name):
    html = page(name, "normal")
    assert demo_json(html) == NORMAL
    without_json = re.sub(
        r'\n  <script type="application/json" id="demo-config">.*?</script>',
        "",
        html,
    )
    assert without_json == page(name, "undefined")


@pytest.mark.parametrize("name", ["error.html", "login.html", "scenarios.html"])
def test_normal_public_link_only_when_url_present(name):
    assert "demo-public-link" not in page(name, "normal")
    html = render(
        name, "undefined", demo_config=NORMAL_LINKED, **page_ctx(name)
    )
    assert html.count('class="demo-public-link"') == 1
    assert (
        '<a href="https://demo.example.test/" class="demo-public-link">' in html
    )
    assert demo_json(html) == NORMAL_LINKED


def page_ctx(name):
    return {
        "error.html": {"status_code": 404, "user": TEACHER},
        "login.html": {},
        "scenarios.html": {"scenarios": [], "user": TEACHER},
    }[name]


@pytest.mark.parametrize("name", ["error.html", "login.html", "scenarios.html"])
def test_demo_layout_root_data_banner_and_safe_json(name):
    html = render(
        name, "undefined", demo_config={**DEMO, **LEAKS}, **page_ctx(name)
    )
    assert (
        '<html lang="ko" data-demo-mode="demo" data-demo-scope="scope-1"'
        ' data-demo-generation="3" data-demo-auth-epoch="2">'
    ) in html
    data = demo_json(html)
    assert data == DEMO
    for leaked in LEAKS.values():
        assert str(leaked) not in html
    assert "</script><script>alert(1)" not in html
    assert '<link rel="stylesheet" href="/static/css/demo-tour.css">' in html
    banner = re.search(r'<div class="demo-banner".*?</div>', html, re.S).group(
        0
    )
    assert 'role="note"' in banner
    # Missing catalog keys still render (translate() fallback); no raw key text.
    assert "demo.banner" not in banner and "demo.content_language" not in banner
    # The demo space links back to itself, never to the public demo URL.
    assert "demo-public-link" not in html


def test_demo_without_visitor_has_mode_but_no_namespace_attributes():
    shape = {
        **DEMO,
        "browser_scope_id": None,
        "generation": None,
        "auth_epoch": None,
    }
    html = render("error.html", "undefined", demo_config=shape, status_code=404)
    assert '<html lang="ko" data-demo-mode="demo">' in html
    assert "ds=" not in html


def test_layout_csrf_readers_use_demo_cookie_only_in_demo():
    demo = page("error.html", "demo")
    assert "/(?:^|;\\s*)demo_csrftoken=([^;]*)/" in demo
    assert demo.count("demo_csrftoken=([^;]*)") == 2
    assert "csrftoken=([^;]*)" not in demo.replace("demo_csrftoken", "")
    # The CSRF value itself is never rendered.
    assert "token-value" not in demo
    for shape in ("undefined", "normal"):
        html = page("error.html", shape)
        assert html.count("/(?:^|;\\s*)csrftoken=([^;]*)/") == 2


ADMIN_PAGES = {
    "admin/users.html": dict(
        users=[], groups=[], current_page=1, total_pages=1, user=ADMIN
    ),
    "admin/groups.html": dict(groups_data=[], user=ADMIN),
    "admin/about.html": dict(contributors=[], user=ADMIN),
}


@pytest.mark.parametrize("name", ADMIN_PAGES)
@pytest.mark.parametrize("shape", ["undefined", "normal", "demo"])
def test_admin_inline_csrf_reader_cookie_name_per_mode(name, shape):
    html = render(name, shape, **ADMIN_PAGES[name])
    cookie = "demo_csrftoken" if shape == "demo" else "csrftoken"
    # layout's two readers plus the page's own reader.
    assert html.count(f"/(?:^|;\\s*){cookie}=([^;]*)/") == 3
    if shape != "demo":
        assert "demo_csrftoken" not in html


@pytest.mark.parametrize("status", [403, 404])
def test_error_page_demo_recovery_link_keeps_normal_link(status):
    normal = render("error.html", "undefined", status_code=status, user=TEACHER)
    assert 'href="/demo"' not in normal
    html = render("error.html", "demo", status_code=status, user=TEACHER)
    assert '<a href="/scenarios" class="btn-primary">' in html
    assert '<a href="/demo" class="btn-secondary">' in html
    section = html[html.index('<section class="empty-state"') :]
    assert section.index('href="/scenarios"') < section.index('href="/demo"')


def scenario():
    return SimpleNamespace(
        id=7,
        title="종이띠",
        subject=None,
        student_name="학생",
        student_profile="profile",
        description="d",
        grade_level=None,
        is_active=True,
        problem_situation="문제",
    )


NS = "ds=scope-1&amp;dg=3&amp;da=2"


@pytest.mark.parametrize(
    "name,context,hrefs",
    [
        (
            "scenarios.html",
            dict(scenarios=None, user=TEACHER),
            ["/scenarios/7"],
        ),
        (
            "session_history.html",
            dict(
                session=SimpleNamespace(id=5, ended_at="x"),
                scenario_title="t",
                messages=[],
                user=TEACHER,
            ),
            ["/sessions/5/export.csv", "/sessions/5/analysis_page"],
        ),
    ],
)
def test_data_links_carry_namespace_only_in_demo(name, context, hrefs):
    if context.get("scenarios", []) is None:
        context["scenarios"] = [scenario()]
    for shape in ("undefined", "normal", "demo"):
        html = render(name, shape, **context)
        for href in hrefs:
            if shape == "demo":
                assert f'href="{href}?{NS}"' in html
            else:
                assert f'href="{href}"' in html
                assert "ds=" not in html


STEPS = ["start", "scenario", "dialogue", "analysis", "admin_csv", "reset"]
ACTIONS = ["resume", "role", "role", "reset", "restart", "skip", "close"]


@pytest.mark.parametrize("shape", ["undefined", "normal"])
def test_tour_panel_and_script_absent_outside_demo(shape):
    html = page("scenarios.html", shape)
    for absent in ("demo-tour", "data-demo-step", "data-demo-action"):
        assert absent not in html


def test_demo_tour_panel_markup_contract():
    html = page("scenarios.html", "demo")
    assert (
        html.count('<script src="/static/js/demo-tour.js" defer></script>') == 1
    )
    assert re.findall(r'data-demo-step="([a-z_]+)"', html) == STEPS
    assert re.findall(r'data-demo-action="([a-z]+)"', html) == ACTIONS
    assert re.findall(r'data-demo-role="([a-z]+)"', html) == [
        "teacher",
        "admin",
    ]
    panel = re.search(
        r'<section id="demo-tour".*?</section>', html, re.S
    ).group(0)
    assert 'aria-labelledby="demo-tour-title"' in panel
    assert '<h2 id="demo-tour-title" tabindex="-1">' in panel
    assert 'role="status" aria-live="polite"' in panel
    assert (
        '<button type="button" class="btn-secondary" id="demo-tour-csv" hidden>'
        in (panel)
    )
    for label in ("complete", "current", "pending", "reset-done"):
        assert f'data-label-{label}="' in panel
    assert 'data-confirm="' in panel
    # The panel sits in normal flow before <main>, never inside it.
    assert html.index('id="demo-tour"') < html.index('<main id="main-content">')
    assert "csrf-v1" not in html and "token-value" not in html


# --- PR③: entry page, demo_error recovery, partial provenance ---------------

NO_VISIT = {
    **DEMO,
    "state": None,
    "browser_scope_id": None,
    "generation": None,
    "auth_epoch": None,
    "role": None,
}


def test_entry_page_offers_start_roles_without_the_tour_panel(monkeypatch):
    from src.i18n import CATALOG

    # Until #201 ships the key, prove the TTL values reach the placeholders.
    monkeypatch.setitem(
        CATALOG["demo"],
        "entry_retention",
        {"ko": "{idle_minutes}|{absolute_hours}"},
    )
    html = render("demo.html", "undefined", demo_config=NO_VISIT)
    assert '<html lang="ko" data-demo-mode="demo">' in html
    assert 'class="demo-banner"' in html
    assert (
        html.count('<script src="/static/js/demo-tour.js" defer></script>') == 1
    )
    # The tour starts after a role is chosen; the entry page has only a status line.
    assert 'id="demo-tour"' not in html and "data-demo-step" not in html
    assert re.findall(r'data-demo-action="([a-z]+)"', html) == [
        "resume",
        "start",
        "start",
    ]
    for role in ("teacher", "admin"):
        assert re.search(
            rf'<button type="button" class="btn-primary" data-demo-action="start"'
            rf' data-demo-role="{role}" value="{role}">',
            html,
        )
    assert '<p id="demo-tour-status" role="status" aria-live="polite"' in html
    main = html[html.index('<main id="main-content">') :]
    assert main.index("<h2") < main.index('data-demo-action="start"')
    assert "<p>30|8</p>" in main  # idle 1800 s → 30 min, absolute 28800 s → 8 h


def error_page(status, **context):
    return render(
        "error.html", "undefined", status_code=status, user=TEACHER, **context
    )


@pytest.mark.parametrize("status", [403, 404])
def test_error_page_none_demo_error_is_normal_markup(status):
    assert error_page(status, demo_error=None) == error_page(status)


@pytest.mark.parametrize(
    "status,recovery",
    [
        (410, "demo.expired"),
        (401, "demo.start_required"),
        (409, "demo.stale_role"),
    ],
)
def test_error_page_demo_error_titles_and_links_by_recovery_key(
    status, recovery
):
    detail = {
        "code": "X",
        "retryable": False,
        "recovery": recovery,
        "generation": None,
        "auth_epoch": None,
    }
    html = error_page(status, demo_error=detail, demo_config=NO_VISIT)
    from src.i18n import translate

    section = html[html.index('<section class="empty-state"') :]
    section = section[: section.index("</section>")]
    assert f"<p>{status}</p>" in section
    assert (
        f'<h2 class="empty-state-title" id="error-title">{translate(recovery)}</h2>'
        in section
    )
    for absent in (
        "error_forbidden",
        translate("common.error_forbidden_title"),
    ):
        assert absent not in section
    assert '<a href="/demo" class="btn-secondary">' in section
    assert f"<title>\n    {status} · {translate(recovery)}" in html


def test_error_page_404_demo_error_uses_its_recovery_text():
    detail = {"code": "DEMO_RESOURCE_NOT_FOUND", "retryable": False}
    detail["recovery"] = "demo.not_found"
    html = error_page(404, demo_error=detail)
    from src.i18n import translate

    assert translate("common.error_not_found_title") in html
    assert f"<p>{translate('demo.not_found')}</p>" in html
    assert '<a href="/scenarios" class="btn-primary">' in html
    assert '<a href="/demo" class="btn-secondary">' in html


SESSION = SimpleNamespace(
    id=5,
    teacher=None,
    started_at=__import__("datetime").datetime(2026, 10, 20, 9, 0),
    ended_at=__import__("datetime").datetime(2026, 10, 20, 9, 5),
    deleted_at=None,
    messages=[],
)
PARTIALS = {
    "partials/session_detail.html": dict(
        session=SESSION, scenario_title="t", student_name="s"
    ),
    "partials/analysis_result.html": dict(
        session_id=5, analysis_result_url="/sessions/5/analysis_result"
    ),
    "partials/analysis_modal.html": dict(
        session_id=5,
        feedback_status="no_dialogue",
        feedback_sections=None,
        distribution={},
        analyses=[],
        user=TEACHER,
    ),
}


def provenance(origin):
    return {
        "mode": "demo",
        "response_source": "prepared_example",
        "external_ai_called": False,
        "contract_version": "d0-03-v1-r3",
        "content_version": "d0-02-v1",
        "fixture_version": "demo-fixture-v1",
        "record_origin": origin,
    }


@pytest.mark.parametrize("name", PARTIALS)
def test_partials_show_record_origin_only_with_a_provenance_object(name):
    base = render(name, "undefined", **PARTIALS[name])
    assert "demo-record-origin" not in base
    assert render(name, "undefined", provenance=None, **PARTIALS[name]) == base
    # Demo without a provenance object shows only the layout banner, no origin.
    assert "demo-record-origin" not in render(name, "demo", **PARTIALS[name])
    weird = render(
        name, "undefined", provenance=provenance("other"), **PARTIALS[name]
    )
    assert weird == base
    for origin in ("seed_prepared", "visitor_generated"):
        html = render(
            name, "demo", provenance=provenance(origin), **PARTIALS[name]
        )
        assert (
            html.count(
                f'<p class="demo-record-origin" data-record-origin="{origin}">'
            )
            == 1
        )


def test_session_detail_keeps_deleted_display_with_provenance():
    deleted = SimpleNamespace(
        **{**vars(SESSION), "deleted_at": SESSION.ended_at}
    )
    context = dict(PARTIALS["partials/session_detail.html"], session=deleted)
    html = render(
        "partials/session_detail.html",
        "demo",
        provenance=provenance("visitor_generated"),
        **context,
    )
    from src.i18n import translate

    assert translate("teacher.deleted") in html
    assert f"<p>{translate('teacher.deleted_session_notice')}</p>" in html
    assert 'data-record-origin="visitor_generated"' in html


def test_analysis_modal_links_carry_namespace_only_in_demo():
    context = PARTIALS["partials/analysis_modal.html"]
    normal = render("partials/analysis_modal.html", "undefined", **context)
    assert 'href="/sessions/5/analysis_page"' in normal and "ds=" not in normal
    html = render("partials/analysis_modal.html", "demo", **context)
    for href in ("/sessions/5/analysis_page", "/sessions/5/export.csv"):
        assert f'href="{href}?{NS}"' in html
