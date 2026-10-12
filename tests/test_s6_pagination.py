"""Administrator pagination through authenticated HTTP and synthetic SQLite."""

import re
from datetime import datetime
from types import SimpleNamespace

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_scenario_api import login

from src.api.dependencies import get_db_session
from src.db.connection import Base
from src.main import app
from src.models import Scenario, ScenarioGroup, Session, User, UserGroup


@pytest.fixture
async def listing(tmp_path):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'lists.db'}")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as db:
        group = UserGroup(id=1, name="Assigned group")
        users = [
            User(
                id=i,
                username=f"teacher_{i}",
                nickname=f"Teacher {i}",
                role="admin" if i == 41 else "teacher",
                group_id=1,
            )
            for i in range(1, 42)
        ]
        scenarios = [
            Scenario(
                id=i,
                title=f"Scenario {i}",
                config_json={"problem": {"public_text": "Synthetic problem"}},
                deleted_at=datetime(2026, 1, 3) if i == 42 else None,
            )
            for i in range(1, 43)
        ]
        db.add_all([group, *users, *scenarios])
        await db.flush()
        db.add_all(
            [ScenarioGroup(scenario_id=i, group_id=1) for i in range(1, 43)]
        )
        db.add_all(
            [
                Session(
                    id=i,
                    scenario_id=41 if i <= 21 else 1,
                    teacher_id=40 if i <= 21 else 1,
                    started_at=datetime(2026, 1, 1),
                    ended_at=datetime(2026, 1, 2),
                    deleted_at=datetime(2026, 1, 3) if i == 42 else None,
                )
                for i in range(1, 43)
            ]
        )
        await db.commit()
    statements = []

    def record(conn, cursor, statement, parameters, context, executemany):
        statements.append((statement, parameters))

    event.listen(engine.sync_engine, "before_cursor_execute", record)

    async def database():
        async with factory() as db:
            yield db
            await db.commit()

    app.dependency_overrides[get_db_session] = database
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            login(client, users[-1])
            yield SimpleNamespace(
                client=client,
                factory=factory,
                statements=statements,
                admin=users[-1],
                teacher=users[-2],
            )
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def test_users_pages_are_bounded_and_keep_group_names(listing):
    for page, expected, offset in [
        (1, list(range(41, 21, -1)), 0),
        (2, list(range(21, 1, -1)), 20),
        (3, [1], 40),
        (9, [], 41),
    ]:
        listing.statements.clear()
        response = await listing.client.get(f"/admin/users?page={page}")
        assert response.status_code == 200
        assert [
            int(i)
            for i in re.findall(r'<tr data-user-id="(\d+)"', response.text)
        ] == expected
        assert f"현재 {page} 페이지 · 20개씩 · 총 41개" in response.text
        if expected:
            assert "Assigned group" in response.text
        else:
            assert "이 페이지에 결과가 없습니다." in response.text
            assert 'href="/admin/users?page=3"' in response.text
        row_queries = [
            (sql, params)
            for sql, params in listing.statements
            if "FROM user " in sql and "ORDER BY" in sql
        ]
        assert len(row_queries) == 1
        assert "LIMIT ? OFFSET ?" in row_queries[0][0]
        assert row_queries[0][1][-2:] == (20, offset)


async def test_scenario_pages_scope_counts_and_assignments(listing):
    for page, expected, offset in [
        (1, list(range(41, 21, -1)), 0),
        (2, list(range(21, 1, -1)), 20),
        (3, [1], 40),
        (9, [], 41),
    ]:
        listing.statements.clear()
        response = await listing.client.get(f"/admin/scenarios?page={page}")
        assert response.status_code == 200
        assert [
            int(i)
            for i in re.findall(r'data-scenario-id="(\d+)"', response.text)
        ] == expected
        assert f"현재 {page} 페이지 · 20개씩 · 총 41개" in response.text
        if page == 1:
            assert 'data-session-count="21"' in response.text
            assert "Assigned group" in response.text
        if page == 3:
            # Original grouped count includes the soft-deleted session 42.
            assert 'data-session-count="21"' in response.text
        if not expected:
            assert "이 페이지에 결과가 없습니다." in response.text
            assert 'href="/admin/scenarios?page=3"' in response.text
        row_queries = [
            (sql, params)
            for sql, params in listing.statements
            if "FROM scenario " in sql and "ORDER BY" in sql
        ]
        assert len(row_queries) == 1
        assert "LIMIT ? OFFSET ?" in row_queries[0][0]
        assert row_queries[0][1][-2:] == (20, offset)
        related = [
            (sql, params)
            for sql, params in listing.statements
            if "GROUP BY session.scenario_id" in sql
            or "FROM scenario_group" in sql
        ]
        assert len(related) == 2
        for sql, params in related:
            assert " IN (" in sql
            assert list(params) == expected


async def test_sessions_json_pages_keep_rows_and_stable_order(listing):
    listing.statements.clear()
    response = await listing.client.get("/admin/sessions")
    assert response.status_code == 200
    body = response.json()
    assert [row["id"] for row in body["sessions"]] == list(range(41, 21, -1))
    assert {
        key: body[key] for key in ("page", "page_size", "total", "has_next")
    } == {
        "page": 1,
        "page_size": 20,
        "total": 41,
        "has_next": True,
    }
    assert set(body["sessions"][0]) == {
        "id",
        "scenario_id",
        "scenario_title",
        "student_name",
        "snapshot_provenance",
        "teacher_id",
        "teacher_nickname",
        "started_at",
        "ended_at",
        "status",
    }
    row_queries = [
        (sql, params)
        for sql, params in listing.statements
        if "FROM session " in sql and "JOIN" in sql
    ]
    assert len(row_queries) == 1
    assert "LIMIT ? OFFSET ?" in row_queries[0][0]
    assert row_queries[0][1][-2:] == (20, 0)
    for page, expected, has_next in [
        (2, list(range(21, 1, -1)), True),
        (3, [1], False),
        (9, [], False),
    ]:
        body = (await listing.client.get(f"/admin/sessions?page={page}")).json()
        assert [row["id"] for row in body["sessions"]] == expected
        assert body["total"] == 41
        assert body["page"] == page
        assert body["has_next"] is has_next
    body = (await listing.client.get("/admin/sessions?page_size=100")).json()
    assert len(body["sessions"]) == 41
    assert body["has_next"] is False
    async with listing.factory() as db:
        first = await db.get(Session, 1)
        first.started_at = datetime(2026, 1, 2)
        await db.commit()
    body = (await listing.client.get("/admin/sessions?page_size=1")).json()
    assert [row["id"] for row in body["sessions"]] == [1]
    assert body["page_size"] == 1
    assert body["has_next"] is True


@pytest.mark.parametrize(
    "path",
    [
        f"/admin/{kind}?page={value}"
        for kind in ("users", "scenarios", "sessions")
        for value in ("0", "-1", "text")
    ]
    + [
        f"/admin/sessions?page_size={value}"
        for value in ("0", "-1", "101", "text")
    ],
)
async def test_pagination_rejects_invalid_bounds(listing, path):
    assert (await listing.client.get(path)).status_code == 422


@pytest.mark.parametrize(
    "filters, total, ids",
    [
        ("scenario_id=41", 21, [21]),
        ("teacher_id=40", 21, [21]),
        ("scenario_id=1", 20, [41]),
        ("scenario_id=41&teacher_id=1", 0, []),
        ("scenario_id=41&teacher_id=40&page=2", 21, [20]),
        (
            "date_from=2026-01-01T00:00:00Z&date_to=2026-01-01T00:00:00",
            41,
            [41],
        ),
        ("date_from=2026-01-02", 0, []),
        ("date_to=2025-12-31", 0, []),
        ("teacher_id=invalid&scenario_id=invalid", 41, [41]),
    ],
)
async def test_sessions_filters_apply_to_total_and_page(
    listing, filters, total, ids
):
    response = await listing.client.get(
        f"/admin/sessions?page_size=1&{filters}"
    )
    assert response.status_code == 200
    body = response.json()
    assert body["total"] == total
    assert [row["id"] for row in body["sessions"]] == ids
    assert body["has_next"] is (total > 1)


@pytest.mark.parametrize("field", ["date_from", "date_to"])
async def test_sessions_invalid_dates_keep_error_contract(listing, field):
    response = await listing.client.get(f"/admin/sessions?{field}=invalid")
    assert response.status_code == 400
    assert f"Invalid {field} format" in response.json()["detail"]


async def test_paginated_lists_require_admin(listing):
    login(listing.client, listing.teacher)
    for kind in ("users", "scenarios", "sessions"):
        assert (
            await listing.client.get(f"/admin/{kind}?page=2")
        ).status_code == 403
    listing.client.cookies.clear()
    for kind in ("users", "scenarios", "sessions"):
        response = await listing.client.get(f"/admin/{kind}")
        assert response.status_code == 303
        assert response.headers["location"] == "/login"


async def test_zero_scenario_page_one_has_empty_text(listing):
    from sqlalchemy import update

    async with listing.factory() as db:
        await db.execute(
            update(Scenario).values(deleted_at=datetime(2026, 1, 3))
        )
        await db.commit()
    response = await listing.client.get("/admin/scenarios")
    assert response.status_code == 200
    assert "시나리오가 없습니다" in response.text
    assert "현재 1 페이지 · 20개씩 · 총 0개" in response.text
    assert 'rel="prev"' not in response.text
    assert 'rel="next"' not in response.text


async def test_research_csv_still_exports_all_sessions(listing):
    import csv
    import io

    from src.models import Message

    async with listing.factory() as db:
        db.add_all(
            Message(session_id=i, role="teacher", content=f"Question {i}")
            for i in range(1, 43)
        )
        await db.commit()
    response = await listing.client.get(
        "/admin/sessions/export?page=9&page_size=1"
    )
    assert response.status_code == 200
    rows = list(csv.DictReader(io.StringIO(response.text.lstrip("\ufeff"))))
    assert len(rows) == 41
    assert {int(row["session_id"]) for row in rows} == set(range(1, 42))
    filtered = await listing.client.get(
        "/admin/sessions/export?scenario_id=41&page_size=1"
    )
    rows = list(csv.DictReader(io.StringIO(filtered.text.lstrip("\ufeff"))))
    assert len(rows) == 21
    assert {int(row["session_id"]) for row in rows} == set(range(1, 22))


async def test_positive_pages_beyond_sqlite_integer_range_are_empty(listing):
    for kind in ("users", "scenarios", "sessions"):
        response = await listing.client.get(
            f"/admin/{kind}?page=9223372036854775808"
        )
        assert response.status_code == 200
        if kind == "sessions":
            assert response.json()["sessions"] == []
            assert response.json()["total"] == 41
            assert response.json()["has_next"] is False
        else:
            assert "이 페이지에 결과가 없습니다." in response.text
            assert f'href="/admin/{kind}?page=3"' in response.text
