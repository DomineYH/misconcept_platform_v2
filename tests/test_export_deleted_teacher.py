"""Preserved administrator CSV downloads after actual teacher deletion."""

import csv
import io
from datetime import datetime

import httpx
import pytest
from s4_record_fixtures import stored_result
from sqlalchemy import delete
from starlette_csrf import CSRFMiddleware
from test_s4_csv import ADMIN_COLUMNS, NEW_COLUMNS
from test_scenario_api import login

from src.api.dependencies import get_db_session
from src.config import config
from src.main import app
from src.models import Message, Session, SessionSummary

__all__ = ["stored_result"]
pytestmark = [
    pytest.mark.parametrize("data", ["baseline"], indirect=True),
    pytest.mark.parametrize("stored_result", ["ok"], indirect=True),
]
IDENTITY_COLUMNS = {"teacher_id", "teacher_username", "teacher_nickname"}


@pytest.fixture
async def export_history(data, stored_result):
    data.other.nickname = '=Other, "교사"\n보존'
    normal = Session(
        scenario_id=data.scenario.id,
        teacher_id=data.other.id,
        started_at=datetime(2026, 10, 2),
        ended_at=datetime(2026, 10, 2, 1),
    )
    data.db.add(normal)
    await data.db.flush()
    data.db.add_all(
        [
            Message(
                session_id=normal.id,
                created_at=datetime(2026, 10, 2, 0, 1),
                role="teacher",
                content='정상 교사, "질문"\n보존',
            ),
            SessionSummary(
                session_id=normal.id,
                created_at=datetime(2026, 10, 2, 1),
                distribution_json="{}",
                feedback="Normal teacher feedback",
            ),
        ]
    )
    await data.db.commit()
    return normal


@pytest.fixture
async def export_api(data, export_history):
    async def database():
        async with data.factory() as db:
            try:
                yield db
                await db.commit()
            except Exception:
                await db.rollback()
                raise

    app.dependency_overrides[get_db_session] = database
    secured = CSRFMiddleware(
        app,
        secret=config.SESSION_SECRET,
        header_name="x-csrf-token",
        cookie_name="csrftoken",
    )
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=secured), base_url="http://test"
        ) as client:
            await authenticate(client, data.admin)
            yield client
    finally:
        app.dependency_overrides.clear()


async def authenticate(client, user):
    login(client, user)
    await client.get("/health")
    client.headers["x-csrf-token"] = client.cookies["csrftoken"]


async def download(client, mode, session_ids):
    if mode == "single":
        return await client.get(f"/admin/sessions/{session_ids[0]}/download")
    if mode == "all":
        return await client.get("/admin/sessions/export")
    return await client.post(
        "/admin/sessions/export-selected", data={"session_ids": session_ids}
    )


def csv_rows(response, mode, session_id):
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\xef\xbb\xbf")
    assert response.headers["content-type"].startswith("text/csv")
    assert "charset=utf-8" in response.headers["content-type"]
    filename = {
        "single": f"session_{session_id}.csv",
        "all": "sessions_export.csv",
        "selected": "selected_sessions.csv",
    }[mode]
    assert response.headers["content-disposition"] == (
        f"attachment; filename={filename}"
    )
    reader = csv.DictReader(io.StringIO(response.content.decode("utf-8-sig")))
    rows = list(reader)
    assert reader.fieldnames == ADMIN_COLUMNS + NEW_COLUMNS
    return rows


def assert_only_deleted_identity_changes(before, after, session_id):
    assert after == [
        (
            {
                key: "" if key in IDENTITY_COLUMNS else value
                for key, value in row.items()
            }
            if row["session_id"] == str(session_id)
            else row
        )
        for row in before
    ]


@pytest.mark.parametrize("mode", ["single", "all", "selected"])
async def test_download_preserves_message_summary_and_normal_teacher_rows_after_deletion(
    data, export_history, export_api, stored_result, mode
):
    ids = [data.session.id, export_history.id]
    before = csv_rows(
        await download(export_api, mode, ids), mode, data.session.id
    )
    owner_rows = [r for r in before if r["session_id"] == str(data.session.id)]
    assert [r["message_id"] for r in owner_rows] == [
        "101",
        "102",
        "103",
        "104",
        "105",
        "106",
        "107",
        "",
    ]
    assert [r["content"] for r in owner_rows[:-1]] == [
        message["content"] for message in stored_result[1]["messages"]
    ]
    assert owner_rows[-1]["role"] == "summary"
    assert owner_rows[-1]["analysis_coverage_json"]
    assert owner_rows[-1]["misconception_findings_json"]
    assert {r["snapshot_origin"] for r in owner_rows} == {"native"}
    assert {r["source_scenario_version"] for r in owner_rows} == {"1"}
    assert {r["teacher_username"] for r in owner_rows} == {"owner"}
    if mode != "single":
        assert len(before) == 10
        assert {r["teacher_username"] for r in before} == {"owner", "other"}
        normal_rows = [r for r in before if r["teacher_username"] == "other"]
        assert normal_rows[0]["content"] == '정상 교사, "질문"\n보존'
        assert {r["teacher_nickname"] for r in normal_rows} == {
            '\'=Other, "교사"\n보존'
        }

    deleted = await export_api.post(f"/admin/users/{data.owner.id}/delete")
    assert deleted.status_code == 200, deleted.text
    assert deleted.json() == {"status": "deleted", "user_id": data.owner.id}

    after = csv_rows(
        await download(export_api, mode, ids), mode, data.session.id
    )
    assert_only_deleted_identity_changes(before, after, data.session.id)


@pytest.mark.parametrize("state", ["no-summary", "summary-only", "empty"])
async def test_deleted_teacher_downloads_keep_optional_summary_and_empty_messages(
    data, export_history, export_api, state
):
    if state in {"no-summary", "empty"}:
        await data.db.execute(
            delete(SessionSummary).where(
                SessionSummary.session_id == data.session.id
            )
        )
    if state in {"summary-only", "empty"}:
        await data.db.execute(
            delete(Message).where(Message.session_id == data.session.id)
        )
    await data.db.commit()
    ids = [data.session.id, export_history.id]
    before = {}
    for mode in ("single", "all", "selected"):
        before[mode] = csv_rows(
            await download(export_api, mode, ids), mode, data.session.id
        )
        owner_rows = [
            r for r in before[mode] if r["session_id"] == str(data.session.id)
        ]
        expected_roles = {
            "no-summary": [
                "teacher",
                "teacher",
                "student",
                "teacher",
                "student",
                "teacher",
                "teacher",
            ],
            "summary-only": ["summary"],
            "empty": [],
        }[state]
        assert [r["role"] for r in owner_rows] == expected_roles
        assert len(before[mode]) == len(expected_roles) + (
            0 if mode == "single" else 2
        )
    assert (
        await export_api.post(f"/admin/users/{data.owner.id}/delete")
    ).status_code == 200
    for mode in ("single", "all", "selected"):
        after = csv_rows(
            await download(export_api, mode, ids), mode, data.session.id
        )
        assert_only_deleted_identity_changes(
            before[mode], after, data.session.id
        )


async def test_exports_require_admin_and_deleted_teacher_cookie_is_rejected(
    data, export_history, export_api
):
    ids = [data.session.id, export_history.id]
    export_api.cookies.clear()
    await export_api.get("/health")
    export_api.headers["x-csrf-token"] = export_api.cookies["csrftoken"]
    for mode in ("single", "all", "selected"):
        response = await download(export_api, mode, ids)
        assert response.status_code == 303
        assert response.headers["location"] == "/login"
    await authenticate(export_api, data.other)
    for mode in ("single", "all", "selected"):
        assert (await download(export_api, mode, ids)).status_code == 403
    assert (
        await export_api.post(f"/admin/users/{data.owner.id}/delete")
    ).status_code == 403

    await authenticate(export_api, data.admin)
    assert (
        await export_api.post(f"/admin/users/{data.owner.id}/delete")
    ).status_code == 200
    assert (
        await export_api.get(
            f"/admin/user-conversations/{data.owner.id}/export"
        )
    ).status_code == 404
    await authenticate(export_api, data.owner)
    assert (
        await export_api.get(f"/sessions/{data.session.id}/messages/updates")
    ).status_code == 401
    for mode in ("single", "all", "selected"):
        assert (await download(export_api, mode, ids)).status_code == 401


async def test_account_deletion_and_selected_export_require_valid_csrf(
    data, export_api
):
    token = export_api.headers.pop("x-csrf-token")
    for invalid_token in (None, "forged"):
        if invalid_token is not None:
            export_api.headers["x-csrf-token"] = invalid_token
        for response in (
            await export_api.post(f"/admin/users/{data.owner.id}/delete"),
            await download(export_api, "selected", [data.session.id]),
        ):
            assert response.status_code == 403
            assert response.text == "CSRF token verification failed"
    export_api.headers["x-csrf-token"] = token
    assert (
        await download(export_api, "selected", [data.session.id])
    ).status_code == 200
    assert (
        await export_api.post(f"/admin/users/{data.owner.id}/delete")
    ).status_code == 200


async def test_active_session_owner_cannot_be_deleted(data, export_api):
    data.session.ended_at = None
    await data.db.commit()
    response = await export_api.post(f"/admin/users/{data.owner.id}/delete")
    assert response.status_code == 400
    assert (
        await export_api.get(
            f"/admin/user-conversations/{data.owner.id}/export"
        )
    ).status_code == 200
    await authenticate(export_api, data.owner)
    assert (
        await export_api.get(f"/sessions/{data.session.id}/messages/updates")
    ).status_code == 200


async def test_soft_deleted_sessions_stay_hidden_and_empty_exports_keep_contract(
    data, export_history, export_api
):
    data.session.mark_deleted()
    await data.db.commit()
    assert (
        await export_api.post(f"/admin/users/{data.owner.id}/delete")
    ).status_code == 200
    assert (
        await download(export_api, "single", [data.session.id])
    ).status_code == 404
    for mode in ("all", "selected"):
        rows = csv_rows(
            await download(
                export_api, mode, [data.session.id, export_history.id]
            ),
            mode,
            data.session.id,
        )
        assert len(rows) == 2
        assert {r["session_id"] for r in rows} == {str(export_history.id)}
        assert {r["teacher_username"] for r in rows} == {"other"}

    export_history.mark_deleted()
    await data.db.commit()
    export_api.headers["X-UI-Language"] = "en"
    for response, expected in (
        (await download(export_api, "all", []), "No sessions found"),
        (
            await download(export_api, "selected", [data.session.id, 999999]),
            "No valid sessions",
        ),
        (
            await download(export_api, "selected", [999999]),
            "No valid sessions",
        ),
    ):
        assert response.status_code == 200
        assert response.headers["content-type"].startswith("text/plain")
        assert "content-disposition" not in response.headers
        assert response.text == expected
