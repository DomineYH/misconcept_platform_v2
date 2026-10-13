"""Real login, CSRF, historical readers and CSV on a disposable candidate."""

import re
from datetime import datetime, timedelta

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette_csrf import CSRFMiddleware
from test_s5_cutover import exports
from test_s6_cutover import current_source, legacy_source

from src.api.dependencies import get_db_session
from src.config import config
from src.db.connection import set_sqlite_pragma
from src.db.s2_verification import consistent_backup
from src.db.s6_cutover import rehearse
from src.main import app
from src.models import ApiUsageLog, ComparisonRun, GenerationRun, User
from src.models.user_group import UserGroup

__all__ = ["current_source", "legacy_source"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)
PASSWORD = "SYNTHETIC-REHEARSAL-PASSWORD"
COMPARISON_ID = "00000000-0000-4000-8000-000000000001"


async def sign_in(client, username):
    client.cookies.clear()
    await client.get("/login")
    response = await client.post(
        "/login", data=dict(username=username, password=PASSWORD)
    )
    assert response.status_code == 303, response.text


@pytest.mark.parametrize("current_source", [36, "legacy"], indirect=True)
async def test_authenticated_history_acl_csv_and_comparison_isolation_leave_candidate_immutable(
    current_source, tmp_path
):
    source_engine = create_async_engine(f"sqlite+aiosqlite:///{current_source}")
    event.listen(source_engine.sync_engine, "connect", set_sqlite_pragma)
    try:
        original_csv = await exports(source_engine)
        async with async_sessionmaker(source_engine)() as db:
            now = datetime(2026, 1, 1)
            db.add(
                ComparisonRun(
                    id=COMPARISON_ID,
                    admin_id=3,
                    target_role="student",
                    mode="same_context",
                    source_snapshot={"private": "COMPARISON_PRIVATE_SENTINEL"},
                    source_hash="source",
                    canonical_context=[],
                    context_hash="context",
                    panel_configs=[],
                    request_id="request",
                    input_hash="input",
                    created_at=now,
                    expires_at=datetime.now() + timedelta(days=30),
                    status="finished",
                )
            )
            await db.flush()
            db.add(
                GenerationRun(
                    id="comparison-generation",
                    owner_id=3,
                    comparison_run_id=COMPARISON_ID,
                    panel_id="panel",
                    turn_id="turn",
                    operation="student",
                    request_id="request",
                    input_hash="input",
                    config_hash="config",
                    provider="openai",
                    model="synthetic",
                    status="failed",
                    started_at=now,
                    finished_at=now,
                    partial_text="COMPARISON_PRIVATE_SENTINEL",
                )
            )
            await db.flush()
            db.add(
                ApiUsageLog(
                    run_id="comparison-generation",
                    usage_scope="comparison",
                    timestamp=now,
                    status="failed",
                    total_tokens=None,
                    estimated_cost_usd=0.125,
                )
            )
            await db.commit()
        assert await exports(source_engine) == original_csv
    finally:
        await source_engine.dispose()
    workspace = tmp_path / "s6"
    report = await rehearse(current_source, workspace, apply=True)
    assert report["preserved"]["comparison_run"]["count"] == 1
    immutable = {
        path.name: path.read_bytes()
        for path in workspace.iterdir()
        if path.suffix in {".db", ".json"}
    }
    disposable = tmp_path / "readers.db"
    consistent_backup(workspace / "candidate.db", disposable)
    engine = create_async_engine(f"sqlite+aiosqlite:///{disposable}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        for user_id in (1, 2, 3):
            user = await db.get(User, user_id)
            user.set_password(PASSWORD)
        second_admin = User(
            username="second_admin", nickname="Second", role="admin"
        )
        second_admin.set_password(PASSWORD)
        db.add(second_admin)
        group_teacher = User(
            username="group_teacher",
            nickname="Group teacher",
            role="teacher",
            group_id=1,
        )
        group_teacher.set_password(PASSWORD)
        unassigned_group = UserGroup(name="Unassigned group")
        db.add(unassigned_group)
        await db.flush()
        unassigned_teacher = User(
            username="unassigned_teacher",
            nickname="Unassigned teacher",
            role="teacher",
            group_id=unassigned_group.id,
        )
        unassigned_teacher.set_password(PASSWORD)
        db.add_all([group_teacher, unassigned_teacher])
        await db.commit()

    async def database():
        async with factory() as db:
            try:
                yield db
                await db.commit()
            except BaseException:
                await db.rollback()
                raise

    app.dependency_overrides[get_db_session] = database
    protected = CSRFMiddleware(
        app,
        secret=config.SESSION_SECRET,
        header_name="x-csrf-token",
        exempt_urls=[re.compile(r"/login")],
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=protected), base_url="http://test"
        ) as client:
            unauthenticated = await client.get("/sessions/1/analysis")
            assert unauthenticated.status_code == 303
            assert unauthenticated.headers["location"] == "/login"
            await sign_in(client, "owner")
            history = await client.get("/sessions/1")
            assert history.status_code == 200
            assert "읽기 전용" in history.text
            result = await client.get("/sessions/1/analysis")
            assert result.status_code == 200
            assert [
                message["content"] for message in result.json()["messages"]
            ] == ["Original {text}"]
            teacher_csv = await client.get("/sessions/1/export.csv")
            assert teacher_csv.status_code == 200
            assert teacher_csv.text == "\ufeff" + original_csv[0]
            assert "Original feedback" in teacher_csv.text
            assert (await client.post("/sessions/1/end")).status_code == 403
            csrf = {"x-csrf-token": client.cookies["csrftoken"]}
            for action in ("end", "analyze"):
                response = await client.post(
                    f"/sessions/1/{action}",
                    headers=csrf,
                    json={"request_id": "00000000-0000-4000-8000-000000000002"},
                )
                assert response.status_code == 409
                assert response.json()["detail"]["code"] == "legacy_read_only"
            assert (
                await client.get("/admin/sessions/export")
            ).status_code == 403
            assert (
                await client.get(f"/admin/comparisons/{COMPARISON_ID}")
            ).status_code == 403
            await sign_in(client, "other")
            for path in (
                "/sessions/1",
                "/sessions/1/analysis",
                "/sessions/1/export.csv",
                "/scenarios/1",
            ):
                denied = await client.get(path)
                assert denied.status_code == 403
                assert "Original {text}" not in denied.text
            await sign_in(client, "admin")
            for path in (
                "/admin/sessions/1/detail",
                "/admin/sessions/1/analysis",
                "/admin/scenarios/1",
            ):
                assert (await client.get(path)).status_code == 200
            admin_csv = await client.get("/admin/sessions/export")
            assert admin_csv.status_code == 200
            assert admin_csv.text == "\ufeff" + original_csv[3]
            assert "COMPARISON_PRIVATE_SENTINEL" not in admin_csv.text
            single_admin_csv = await client.get("/admin/sessions/1/download")
            assert single_admin_csv.status_code == 200
            assert single_admin_csv.text == "\ufeff" + original_csv[1]
            statistics = await client.get("/admin/stats")
            assert statistics.status_code == 200
            assert statistics.json()["total_sessions"] == 1
            assert statistics.json()["total_teachers"] == 1
            usage = await client.get("/admin/api-usage")
            assert usage.status_code == 200
            assert "0.137345" in usage.text
            comparison = await client.get(f"/admin/comparisons/{COMPARISON_ID}")
            assert comparison.status_code == 200
            assert (
                comparison.json()["source_snapshot"]["private"]
                == "COMPARISON_PRIVATE_SENTINEL"
            )
            await sign_in(client, "second_admin")
            denied = await client.get(f"/admin/comparisons/{COMPARISON_ID}")
            assert denied.status_code == 404
            assert "COMPARISON_PRIVATE_SENTINEL" not in denied.text
            await sign_in(client, "group_teacher")
            assigned = await client.get("/scenarios/1")
            assert assigned.status_code == 400
            assert assigned.json()["detail"]["code"] == "scenario_not_published"
            assert (await client.get("/sessions/1/analysis")).status_code == 403
            await sign_in(client, "unassigned_teacher")
            assert (await client.get("/scenarios/1")).status_code == 403
        assert await exports(engine) == original_csv
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()
    assert {
        name: (workspace / name).read_bytes() for name in immutable
    } == immutable
    assert await rehearse(current_source, workspace, apply=True) == report
    assert PASSWORD not in (workspace / "report.json").read_text()
    assert "Original {text}" not in (workspace / "report.json").read_text()
