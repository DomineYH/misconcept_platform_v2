"""D3-02: demo CSV provenance, echo headers and deletion facts (§22–§24.1)."""

import csv
import io
from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from s4_record_fixtures import stored_result
from sqlalchemy import select
from starlette.requests import Request
from test_export_deleted_teacher import (  # noqa: F401  (fixtures)
    export_api,
    export_history,
)
from test_s4_csv import ADMIN_COLUMNS, NEW_COLUMNS, TEACHER_COLUMNS

from src.api.routes import (
    admin_session_actions,
    admin_session_export,
    admin_users,
)
from src.demo.database import DemoError
from src.demo.lifecycle import NORMAL_SCOPE
from src.demo.session import TourFacts
from src.main import app
from src.models import (
    ComparisonRun,
    GenerationRun,
    Message,
    Session,
    SessionSummary,
    User,
)
from src.services.export import DEMO_COLUMNS, CSVExporter

__all__ = ["stored_result", "export_api", "export_history"]
# Deletion tests below use the default `data` schema without a stored result.
CSV_CASE = pytest.mark.parametrize(
    ("data", "stored_result"), [("baseline", "ok")], indirect=True
)
ADMIN_ALL = ADMIN_COLUMNS + NEW_COLUMNS
TEACHER_ALL = TEACHER_COLUMNS + NEW_COLUMNS
CONSTANTS = {
    "demo_response_source": "prepared_example",
    "demo_external_ai_called": "false",
    "demo_content_version": "d0-02-v1",
    "demo_fixture_version": "demo-fixture-v1",
}


def parse(text):
    reader = csv.DictReader(io.StringIO(text.removeprefix("﻿")))
    rows = list(reader)
    return reader.fieldnames, rows


def assert_origins(rows, seed_id):
    for row in rows:
        for key, value in CONSTANTS.items():
            assert row[key] == value
        expected = (
            "seed_prepared"
            if row["session_id"] == str(seed_id)
            else "visitor_generated"
        )
        assert row["demo_record_origin"] == expected


@pytest.fixture
def demo_mode(monkeypatch, data):
    """Patch only the route modules' config; auth/DB deps stay normal."""
    demo = SimpleNamespace(APP_MODE="demo")
    monkeypatch.setattr(admin_session_export, "config", demo)
    monkeypatch.setattr(admin_session_actions, "config", demo)
    monkeypatch.setattr(
        app.state,
        "demo_request_db",
        SimpleNamespace(seed_refs={"completed_session": data.session.id}),
        raising=False,
    )
    return data.session.id


# --- 1. exporter level -----------------------------------------------------


async def run_exporter(data, export_history, **kwargs):
    ids = [data.session.id, export_history.id]
    exporter = CSVExporter()
    return {
        "teacher": await exporter.export_session(ids[0], data.db, **kwargs),
        "teacher_multi": await exporter.export_multiple_sessions(
            ids, data.db, **kwargs
        ),
        "admin": await exporter.export_session_admin(ids[0], data.db, **kwargs),
        "admin_multi": await exporter.export_multiple_sessions_admin(
            ids, data.db, **kwargs
        ),
    }


@CSV_CASE
async def test_exporter_demo_appends_suffix_columns_after_existing_ones(
    data, export_history, stored_result
):
    plain = await run_exporter(data, export_history)
    demo = await run_exporter(
        data, export_history, demo_seed_session_id=data.session.id
    )
    expected = {
        "teacher": TEACHER_ALL,
        "teacher_multi": TEACHER_ALL,
        "admin": ADMIN_ALL,
        "admin_multi": ADMIN_ALL,
    }
    assert len(TEACHER_ALL) == 20 and len(ADMIN_ALL) == 27
    for name, columns in expected.items():
        plain_fields, plain_rows = parse(plain[name])
        fields, rows = parse(demo[name])
        assert plain_fields == columns
        assert fields == columns + DEMO_COLUMNS
        # No banner/comment rows; provenance never overwrites existing values.
        assert len(rows) == len(plain_rows) > 0
        for plain_row, row in zip(plain_rows, rows):
            assert {k: row[k] for k in columns} == plain_row
        assert_origins(rows, data.session.id)


@CSV_CASE
async def test_exporter_multi_marks_non_seed_session_visitor_generated(
    data, export_history, stored_result
):
    demo = await run_exporter(
        data, export_history, demo_seed_session_id=data.session.id
    )
    for name in ("teacher_multi", "admin_multi"):
        _, rows = parse(demo[name])
        origins = {r["session_id"]: r["demo_record_origin"] for r in rows}
        assert origins == {
            str(data.session.id): "seed_prepared",
            str(export_history.id): "visitor_generated",
        }


@CSV_CASE
async def test_exporter_without_seed_is_unchanged_and_has_no_demo_columns(
    data, export_history, stored_result
):
    first = await run_exporter(data, export_history)
    second = await run_exporter(data, export_history, demo_seed_session_id=None)
    assert first == second
    for text in first.values():
        fields, _ = parse(text)
        assert not any(name.startswith("demo_") for name in fields)


# --- 2. formula / quoting defense ---------------------------------------------


@CSV_CASE
async def test_exporter_demo_keeps_formula_prefix_and_quoted_multiline_round_trip(
    data, export_history, stored_result
):
    first = await data.db.get(Message, 101)
    second = await data.db.get(Message, 102)
    first.content = "=1+1"
    second.content = 'a, "quoted"\n두 번째 줄\r\n끝'
    await data.db.commit()
    demo = await run_exporter(
        data, export_history, demo_seed_session_id=data.session.id
    )
    for text in demo.values():
        _, rows = parse(text)
        by_id = {r.get("message_id"): r for r in rows}
        for row in rows:
            if row["role"] == "summary":
                continue
            assert not row["content"].startswith("=")
        contents = [r["content"] for r in rows]
        assert "'=1+1" in contents
        assert 'a, "quoted"\n두 번째 줄\r\n끝' in contents
        assert by_id  # parsed without row loss


# --- helpers for routes -------------------------------------------------------


def disposition(name):
    return f"attachment; filename=demo_d0-02-v1_admin_{name}.csv"


def demo_rows(response, name, session_ids):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert response.headers["content-type"].startswith("text/csv")
    value = response.headers["content-disposition"]
    assert value == disposition(name)
    assert value.isascii()
    fields, rows = parse(response.content.decode("utf-8-sig"))
    assert fields == ADMIN_ALL + DEMO_COLUMNS and len(fields) == 32
    assert rows
    assert {r["session_id"] for r in rows} <= {str(i) for i in session_ids}
    return rows


# --- 3. demo routes -------------------------------------------------------------


@CSV_CASE
async def test_demo_all_and_user_exports_use_demo_filename_without_echo_headers(
    data, export_history, export_api, demo_mode
):
    ids = [data.session.id, export_history.id]
    response = await export_api.get("/admin/sessions/export")
    rows = demo_rows(response, "all", ids)
    assert {r["session_id"] for r in rows} == {str(i) for i in ids}
    assert_origins(rows, demo_mode)
    for response, name, owner in (
        (
            await export_api.get(
                f"/admin/user-conversations/{data.owner.id}/export"
            ),
            f"user-{data.owner.id}",
            [data.session.id],
        ),
        (
            await export_api.get(
                f"/admin/user-conversations/{data.other.id}/export"
            ),
            f"user-{data.other.id}",
            [export_history.id],
        ),
    ):
        rows = demo_rows(response, name, owner)
        assert {r["session_id"] for r in rows} == {str(i) for i in owner}
        assert_origins(rows, demo_mode)
    for response in (
        await export_api.get("/admin/sessions/export"),
        await export_api.get(
            f"/admin/user-conversations/{data.owner.id}/export"
        ),
    ):
        assert not [k for k in response.headers if k.startswith("x-demo")]


@pytest.mark.parametrize("mode", ["single", "selected"])
@CSV_CASE
async def test_demo_download_and_selected_echo_request_id_and_row_count(
    data, export_history, export_api, demo_mode, mode
):
    ids = [data.session.id, export_history.id]
    expected_ids = ids[:1] if mode == "single" else ids
    name = f"session-{ids[0]}" if mode == "single" else "selected"
    request_id = str(uuid4())

    echoed = await download_with(export_api, mode, ids, request_id)
    rows = demo_rows(echoed, name, expected_ids)
    assert {r["session_id"] for r in rows} == {str(i) for i in expected_ids}
    assert echoed.headers["x-demo-export-request-id"] == request_id
    assert echoed.headers["x-demo-export-row-count"] == str(len(rows))

    bare = await download_with(export_api, mode, ids, None)
    bare_rows = demo_rows(bare, name, expected_ids)
    assert "x-demo-export-request-id" not in bare.headers
    assert bare.headers["x-demo-export-row-count"] == str(len(bare_rows))


@pytest.mark.parametrize("mode", ["single", "selected"])
@pytest.mark.parametrize(
    "bad",
    [
        "not-a-uuid",
        "",
        str(uuid4()).upper(),
        uuid4().hex,
        "{" + str(uuid4()) + "}",
    ],
)
@CSV_CASE
async def test_demo_non_canonical_request_id_is_invalid_request(
    data, export_history, export_api, demo_mode, mode, bad
):
    ids = [data.session.id, export_history.id]
    with pytest.raises(DemoError) as raised:
        await download_with(export_api, mode, ids, bad)
    assert raised.value.code == "DEMO_INVALID_REQUEST"
    assert raised.value.status_code == 422


async def download_with(client, mode, ids, request_id):
    headers = (
        {} if request_id is None else {"x-demo-export-request-id": request_id}
    )
    if mode == "single":
        return await client.get(
            f"/admin/sessions/{ids[0]}/download", headers=headers
        )
    return await client.post(
        "/admin/sessions/export-selected",
        data={"session_ids": ids},
        headers=headers,
    )


@CSV_CASE
async def test_demo_selected_export_contains_only_requested_sessions(
    data, export_history, export_api, demo_mode
):
    only_other = await download_with(
        export_api, "selected", [export_history.id], None
    )
    rows = demo_rows(only_other, "selected", [export_history.id])
    assert {r["session_id"] for r in rows} == {str(export_history.id)}
    assert {r["demo_record_origin"] for r in rows} == {"visitor_generated"}


# --- 4. not found ------------------------------------------------------------------


@CSV_CASE
async def test_demo_download_missing_or_soft_deleted_session_is_resource_not_found(
    data, export_history, export_api, demo_mode
):
    with pytest.raises(DemoError) as raised:
        await export_api.get("/admin/sessions/999999/download")
    assert raised.value.code == "DEMO_RESOURCE_NOT_FOUND"
    assert raised.value.status_code == 404

    export_history.mark_deleted()
    await data.db.commit()
    with pytest.raises(DemoError) as raised:
        await export_api.get(f"/admin/sessions/{export_history.id}/download")
    assert raised.value.code == "DEMO_RESOURCE_NOT_FOUND"
    assert raised.value.status_code == 404


@CSV_CASE
async def test_demo_user_export_missing_user_is_resource_not_found(
    export_api, demo_mode
):
    with pytest.raises(DemoError) as raised:
        await export_api.get("/admin/user-conversations/999999/export")
    assert raised.value.code == "DEMO_RESOURCE_NOT_FOUND"
    assert raised.value.status_code == 404


# --- 5. seed-ref misconfiguration -------------------------------------------------


@pytest.mark.parametrize(
    "runtime",
    [
        "absent",
        SimpleNamespace(seed_refs={}),
        SimpleNamespace(seed_refs={"completed_session": "7"}),
        SimpleNamespace(seed_refs={"completed_session": None}),
        SimpleNamespace(seed_refs={"completed_session": True}),
    ],
)
@CSV_CASE
async def test_demo_seed_ref_misconfiguration_is_configuration_invalid(
    data, export_history, export_api, demo_mode, monkeypatch, runtime
):
    if runtime == "absent":
        monkeypatch.delattr(app.state, "demo_request_db")
    else:
        monkeypatch.setattr(app.state, "demo_request_db", runtime)
    ids = [data.session.id, export_history.id]
    requests = (
        lambda: export_api.get("/admin/sessions/export"),
        lambda: download_with(export_api, "selected", ids, None),
        lambda: export_api.get(f"/admin/sessions/{ids[0]}/download"),
        lambda: export_api.get(
            f"/admin/user-conversations/{data.owner.id}/export"
        ),
    )
    for request in requests:
        with pytest.raises(DemoError) as raised:
            await request()
        assert raised.value.code == "DEMO_CONFIGURATION_INVALID"


# --- 6. soft delete ----------------------------------------------------------------


@CSV_CASE
async def test_demo_exports_omit_soft_deleted_session_rows_and_content(
    data, export_history, export_api, demo_mode, stored_result
):
    secret = [m["content"] for m in stored_result[1]["messages"]]
    data.session.mark_deleted()
    await data.db.commit()
    ids = [data.session.id, export_history.id]

    for response, name in (
        (await export_api.get("/admin/sessions/export"), "all"),
        (await download_with(export_api, "selected", ids, None), "selected"),
        (
            await export_api.get(
                f"/admin/user-conversations/{data.other.id}/export"
            ),
            f"user-{data.other.id}",
        ),
    ):
        rows = demo_rows(response, name, [export_history.id])
        for row in rows:
            assert row["session_id"] == str(export_history.id)
            assert all(row[k] not in secret for k in ("content", "feedback"))
        assert not any(text in response.text for text in secret if text)

    # The deleted session's owner has nothing left to export.
    response = await export_api.get(
        f"/admin/user-conversations/{data.owner.id}/export"
    )
    assert response.status_code == 200
    assert not any(text in response.text for text in secret if text)

    with pytest.raises(DemoError) as raised:
        await export_api.get(f"/admin/sessions/{data.session.id}/download")
    assert raised.value.code == "DEMO_RESOURCE_NOT_FOUND"


# --- 7. normal-mode guard ----------------------------------------------------------


@CSV_CASE
async def test_normal_mode_keeps_old_filenames_columns_and_has_no_demo_headers(
    data, export_history, export_api
):
    ids = [data.session.id, export_history.id]
    cases = (
        (await export_api.get("/admin/sessions/export"), "sessions_export.csv"),
        (
            await download_with(export_api, "selected", ids, str(uuid4())),
            "selected_sessions.csv",
        ),
        (
            await export_api.get(
                f"/admin/user-conversations/{data.owner.id}/export"
            ),
            f"user_{data.owner.id}_conversations.csv",
        ),
        (
            await download_with(export_api, "single", ids, str(uuid4())),
            f"session_{ids[0]}.csv",
        ),
    )
    for response, filename in cases:
        assert response.status_code == 200, response.text
        assert response.headers["content-disposition"] == (
            f"attachment; filename={filename}"
        )
        assert not [k for k in response.headers if k.startswith("x-demo")]
        fields, _ = parse(response.content.decode("utf-8-sig"))
        assert fields == ADMIN_ALL
        assert not any(name.startswith("demo_") for name in fields)

    # Normal mode keeps the plain 404 and ignores any demo request header.
    missing = await export_api.get("/admin/sessions/999999/download")
    assert missing.status_code == 404
    missing = await export_api.get("/admin/user-conversations/999999/export")
    assert missing.status_code == 404


# --- §22/§24.1 demo deletion facts ---------------------------------------

DEL_SCOPE = ("visitor-1", 1)


def del_request(ctx=None):
    state = {"demo_context": ctx} if ctx else {}
    return Request(
        {
            "type": "http",
            "method": "POST",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "state": state,
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )


class DelLifecycle:
    def __init__(self):
        self.calls = []

    def cancel(self, scope, kind, identifier):
        self.calls.append((scope, kind, identifier))


@pytest.fixture
async def del_env(data, monkeypatch):
    events, retained = [], []
    handle = object()

    @asynccontextmanager
    async def write_guard(ctx):
        events.append("guard_enter")
        yield handle
        events.append("guard_exit")

    state = SimpleNamespace(journey=data.session.id)

    async def capture(ctx, got, tour):
        assert got is handle
        events.append("capture")
        return TourFacts(
            browser_scope_id="s",
            generation=1,
            auth_epoch=1,
            journey_session_id=state.journey,
        )

    def retain(got, facts):
        assert got is handle
        events.append("retain")
        retained.append(facts)

    demo = SimpleNamespace(
        visitors=SimpleNamespace(write_guard=write_guard),
        tour_content=SimpleNamespace(seed_refs={"teacher": data.owner.id}),
    )

    @asynccontextmanager
    async def factory():
        async with data.factory() as db:
            commit = db.commit

            async def spy():
                await commit()
                events.append("commit")

            db.commit = spy
            yield db

    ctx = SimpleNamespace(factory=factory, scope=DEL_SCOPE)
    for module in (admin_session_actions, admin_users):
        monkeypatch.setattr(module, "config", SimpleNamespace(APP_MODE="demo"))
    monkeypatch.setattr(
        admin_session_actions, "get_demo_session", lambda r: demo
    )
    monkeypatch.setattr(admin_session_actions, "capture_tour_facts", capture)
    monkeypatch.setattr(admin_session_actions, "retain_tour_facts", retain)
    lifecycle = DelLifecycle()
    monkeypatch.setattr(admin_users, "lifecycle", lifecycle)
    return SimpleNamespace(
        events=events,
        retained=retained,
        state=state,
        lifecycle=lifecycle,
        request=del_request(ctx),
    )


async def del_session_with_history(data, teacher_id, *, ended=True):
    async with data.factory() as db:
        row = Session(
            scenario_id=data.scenario.id,
            teacher_id=teacher_id,
            started_at=datetime(2026, 10, 2),
            ended_at=datetime(2026, 10, 2, 1) if ended else None,
        )
        db.add(row)
        await db.flush()
        db.add_all(
            [
                Message(session_id=row.id, role="teacher", content="hello"),
                SessionSummary(
                    session_id=row.id, distribution_json="{}", feedback="fb"
                ),
            ]
        )
        await db.commit()
        return row.id


async def del_counts(data, session_id):
    async with data.factory() as db:
        messages = len(
            (
                await db.scalars(
                    select(Message).where(Message.session_id == session_id)
                )
            ).all()
        )
        summaries = len(
            (
                await db.scalars(
                    select(SessionSummary).where(
                        SessionSummary.session_id == session_id
                    )
                )
            ).all()
        )
        row = await db.get(Session, session_id)
        return messages, summaries, row.deleted_at, row.teacher_id


ORDER = ["guard_enter", "capture", "commit", "retain", "guard_exit"]


async def test_demo_session_delete_commits_then_retains_journey_fact(
    data, del_env
):
    target = await del_session_with_history(data, data.owner.id)
    del_env.state.journey = target
    response = await admin_session_actions.delete_session(
        target, data.admin, data.db, del_env.request
    )
    assert response.status_code == 200 and response.body == b""
    assert del_env.events == ORDER
    assert del_env.retained[0].session_deleted is True
    messages, summaries, deleted_at, teacher_id = await del_counts(data, target)
    assert (messages, summaries) == (1, 1)
    assert deleted_at is not None and teacher_id == data.owner.id


async def test_demo_non_journey_session_delete_keeps_fact_false(data, del_env):
    target = await del_session_with_history(data, data.owner.id)
    await admin_session_actions.delete_session(
        target, data.admin, data.db, del_env.request
    )
    assert del_env.events == ORDER
    assert del_env.retained[0].session_deleted is False
    assert del_env.retained[0].teacher_deleted is False
    assert (await del_counts(data, target))[2] is not None


async def test_demo_session_delete_failure_rolls_back_without_retain(
    data, del_env
):
    target = await del_session_with_history(data, data.owner.id)
    await admin_session_actions.delete_session(
        target, data.admin, data.db, del_env.request
    )
    del_env.events.clear()
    del_env.retained.clear()
    async with data.factory() as db:
        first = (await db.get(Session, target)).deleted_at

    with pytest.raises(HTTPException) as again:
        await admin_session_actions.delete_session(
            target, data.admin, data.db, del_env.request
        )
    assert again.value.status_code == 400
    with pytest.raises(DemoError) as missing:
        await admin_session_actions.delete_session(
            999999, data.admin, data.db, del_env.request
        )
    assert missing.value.code == "DEMO_RESOURCE_NOT_FOUND"
    assert missing.value.status_code == 404
    assert del_env.retained == []
    assert "retain" not in del_env.events
    async with data.factory() as db:
        assert (await db.get(Session, target)).deleted_at == first


async def test_demo_teacher_delete_retains_seed_teacher_fact(data, del_env):
    ended = await del_session_with_history(data, data.owner.id)
    result = await admin_users.delete_user(
        data.owner.id, data.admin, data.db, del_env.request
    )
    assert result == {"status": "deleted", "user_id": data.owner.id}
    assert del_env.events == ORDER
    assert del_env.retained[0].teacher_deleted is True
    assert del_env.retained[0].session_deleted is False
    async with data.factory() as db:
        assert await db.get(User, data.owner.id) is None
    for session_id in (ended, data.session.id):
        messages, summaries, deleted_at, teacher_id = await del_counts(
            data, session_id
        )
        assert teacher_id is None and deleted_at is None
    assert await del_counts(data, ended) == (1, 1, None, None)


async def test_demo_non_seed_user_delete_keeps_teacher_fact_false(
    data, del_env
):
    await admin_users.delete_user(
        data.other.id, data.admin, data.db, del_env.request
    )
    assert del_env.events == ORDER
    assert del_env.retained[0].teacher_deleted is False
    async with data.factory() as db:
        assert await db.get(User, data.other.id) is None
        assert await db.get(User, data.owner.id) is not None


@pytest.mark.parametrize("case", ["self", "active_session"])
async def test_demo_teacher_delete_limits_roll_back_without_retain(
    data, del_env, case
):
    if case == "self":
        target = data.admin.id
    else:
        target = data.other.id
        active = await del_session_with_history(data, target, ended=False)
    with pytest.raises(HTTPException) as error:
        await admin_users.delete_user(
            target, data.admin, data.db, del_env.request
        )
    assert error.value.status_code == 400
    assert del_env.retained == []
    assert "retain" not in del_env.events
    async with data.factory() as db:
        assert await db.get(User, target) is not None
        if case == "active_session":
            row = await db.get(Session, active)
            assert row.teacher_id == target and row.deleted_at is None


async def del_running_comparison(data, owner_id):
    run_id, comparison_id = str(uuid4()), str(uuid4())
    async with data.factory() as db:
        db.add(
            ComparisonRun(
                id=comparison_id,
                admin_id=data.admin.id,
                scenario_id=data.scenario.id,
                source_snapshot={},
                source_hash="h",
                target_role="student",
                mode="same_context",
                canonical_context=[],
                context_hash="h",
                panel_configs=[],
                request_id=str(uuid4()),
                input_hash="h",
                created_at=datetime(2026, 10, 1),
                expires_at=datetime(2026, 11, 1),
                status="running",
            )
        )
        await db.flush()
        db.add(
            GenerationRun(
                id=run_id,
                owner_id=owner_id,
                comparison_run_id=comparison_id,
                panel_id=str(uuid4()),
                turn_id=str(uuid4()),
                operation="student",
                request_id=str(uuid4()),
                input_hash="h",
                config_hash="h",
                provider="demo",
                model="m",
                status="running",
            )
        )
        await db.commit()
    return run_id


async def test_demo_teacher_delete_cancels_runs_in_visitor_scope(data, del_env):
    run_id = await del_running_comparison(data, data.other.id)
    await admin_users.delete_user(
        data.other.id, data.admin, data.db, del_env.request
    )
    assert del_env.lifecycle.calls == [(DEL_SCOPE, "run", run_id)]


async def test_normal_teacher_delete_cancels_runs_in_normal_scope(
    data, monkeypatch
):
    lifecycle = DelLifecycle()
    monkeypatch.setattr(admin_users, "lifecycle", lifecycle)
    run_id = await del_running_comparison(data, data.other.id)
    result = await admin_users.delete_user(
        data.other.id, data.admin, data.db, del_request()
    )
    assert result == {"status": "deleted", "user_id": data.other.id}
    assert lifecycle.calls == [(NORMAL_SCOPE, "run", run_id)]


async def test_normal_deletes_never_touch_demo_services(data, monkeypatch):
    def forbid(*args, **kwargs):
        raise AssertionError("normal mode must not use demo services")

    for name in ("get_demo_session", "capture_tour_facts", "retain_tour_facts"):
        monkeypatch.setattr(admin_session_actions, name, forbid)
    target = await del_session_with_history(data, data.owner.id)
    response = await admin_session_actions.delete_session(
        target, data.admin, data.db, del_request()
    )
    assert response.status_code == 200 and response.body == b""
    assert await admin_users.delete_user(
        data.other.id, data.admin, data.db, del_request()
    ) == {"status": "deleted", "user_id": data.other.id}
    await data.db.commit()
    assert (await del_counts(data, target))[2] is not None
    async with data.factory() as db:
        assert await db.get(User, data.other.id) is None
