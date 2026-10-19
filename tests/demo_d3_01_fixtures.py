"""Demo-mode page fixtures for the tour browser check; never imported by runtime."""

from types import SimpleNamespace

from src.api.dependencies import templates
from src.demo.session import CONTENT_VERSION, CONTRACT_VERSION, FIXTURE_VERSION
from src.i18n import display_language

HTML = "text/html; charset=utf-8"
NOT_FOUND = (404, "text/plain; charset=utf-8", b"not found")


def _demo_config(request, scope, generation, epoch, role, state):
    return dict(
        mode="demo",
        contract_version=CONTRACT_VERSION,
        content_version=CONTENT_VERSION,
        fixture_version=FIXTURE_VERSION,
        state=state,
        browser_scope_id=scope,
        generation=generation,
        auth_epoch=epoch,
        role=role,
        expires_at="2026-10-20T08:00:00Z",
        idle_ttl_seconds=1800,
        absolute_ttl_seconds=28800,
        active=False,
        last_error=None,
        start_url="/demo/start",
        role_url="/demo/role",
        reset_url="/demo/reset",
        state_url="/demo/state",
        public_url=None,
        csrf_cookie_name="demo_csrftoken",
        csrf_header_name="x-csrf-token",
        ui_language=display_language(request),
        tour_facts=None,
    )


def demo_fixture(path, query, request):
    """Return (status, mime, body) for /fixtures/demo/*."""
    page = path.removeprefix("/fixtures/demo/")
    first = lambda key, default: query.get(key, [default])[0]  # noqa: E731
    try:
        generation, epoch = int(first("g", "1")), int(first("e", "1"))
        status = int(first("status", "404"))
    except ValueError:
        return NOT_FOUND
    role = first("role", "teacher")
    if role not in {"teacher", "admin"} or (
        page == "error" and status not in {403, 404}
    ):
        return NOT_FOUND
    if page == "normal":
        demo = dict(mode="normal", public_url=None)
        role = "teacher"
    elif page in {"page", "error"}:
        scope = first("scope", "scope-a")
        if first("novisit", "") == "1":
            scope = generation = epoch = role = None
        demo = _demo_config(
            request, scope, generation, epoch, role, first("state", "ready")
        )
    else:
        return NOT_FOUND
    user = (
        None
        if role is None
        else SimpleNamespace(nickname=f"Demo {role}", role=role)
    )
    name, extra = (
        ("error.html", dict(status_code=status))
        if page == "error"
        else ("scenarios.html", dict(scenarios=[]))
    )
    body = (
        templates.get_template(name)
        .render(request=request, user=user, demo_config=demo, **extra)
        .encode()
    )
    return (status if page == "error" else 200), HTML, body
