"""Deleted conversations retain records while hiding their content over HTTP."""

from html import unescape
from uuid import uuid4

import pytest
from s4_record_fixtures import stored_result
from sqlalchemy import select, update
from test_export_deleted_teacher import (
    authenticate,
    csv_rows,
    download,
    export_api,
    export_history,
)

from src.models import (
    GenerationRun,
    Message,
    QuestionAnalysis,
    Session,
    SessionFeedbackReport,
    SessionSummary,
)
from src.services.lesson_snapshots import canonical_hash

__all__ = ["export_api", "export_history", "stored_result"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def forbid_analysis(*args, **kwargs):
    raise AssertionError(
        "Deleted conversation must not load or generate analysis"
    )


@pytest.mark.parametrize("stored_result", ["ok", "no_accepted"], indirect=True)
@pytest.mark.parametrize("origin", ["native", "legacy_reconstructed", None])
@pytest.mark.parametrize("language", ["ko", "en"])
async def test_deleted_admin_detail_shows_only_metadata(
    data, export_api, stored_result, origin, language, monkeypatch
):
    data.session.snapshot_origin = origin
    if origin == "legacy_reconstructed":
        data.session.source_scenario_version = None
        data.session.config_snapshot_json = {
            **data.session.config_snapshot_json,
            "unknown_fields": ["config"],
        }
        data.session.config_hash = canonical_hash(
            data.session.config_snapshot_json
        )
    elif origin is None:
        data.session.config_snapshot_json = None
        data.session.config_hash = None
        data.session.snapshot_created_at = None
        data.session.source_scenario_version = None
    data.session.mark_deleted()
    await data.db.commit()
    monkeypatch.setattr(
        "src.api.routes.admin_session_actions._load_analysis_response",
        forbid_analysis,
    )
    export_api.headers["X-UI-Language"] = language
    response = await export_api.get(f"/admin/sessions/{data.session.id}/detail")

    assert response.status_code == 200, response.text
    notice = {
        "ko": "삭제된 대화 · 내용은 표시하지 않습니다",
        "en": "Deleted conversation · content is not shown",
    }[language]
    assert notice in response.text
    assert "Owner (owner)" in response.text
    assert "2026-01-01 00:00:00" in response.text
    assert "badge-danger" in response.text
    assert ("snapshot-provenance" in response.text) == (origin != "native")
    for message in stored_result[1]["messages"]:
        assert message["content"] not in unescape(response.text)
    assert "data-result-url" not in response.text
    assert f"/admin/sessions/{data.session.id}/analysis" not in response.text


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
async def test_delete_retains_records_and_repeated_delete_keeps_existing_contract(
    data, export_api, stored_result
):
    sid = data.session.id
    async with data.factory() as db:
        before = {
            model: [
                tuple(row)
                for row in (await db.execute(select(model.__table__))).all()
            ]
            for model in (
                Message,
                QuestionAnalysis,
                SessionSummary,
                SessionFeedbackReport,
            )
        }
    assert (
        await export_api.post(f"/admin/sessions/{sid}/delete")
    ).status_code == 200
    async with data.factory() as db:
        retained = await db.get(Session, sid)
        assert retained.deleted_at is not None
        deleted_at = retained.deleted_at
        for model, rows in before.items():
            assert [
                tuple(row)
                for row in (await db.execute(select(model.__table__))).all()
            ] == rows
    repeated = await export_api.post(f"/admin/sessions/{sid}/delete")
    assert repeated.status_code == 400
    async with data.factory() as db:
        assert (await db.get(Session, sid)).deleted_at == deleted_at


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
@pytest.mark.parametrize("path", ["analysis", "analysis_modal"])
async def test_deleted_admin_analysis_is_not_found(
    data, export_api, stored_result, path
):
    data.session.mark_deleted()
    await data.db.commit()
    response = await export_api.get(f"/admin/sessions/{data.session.id}/{path}")
    assert response.status_code == 404, response.text


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
async def test_deleted_session_cannot_reserve_or_generate_analysis(
    data, export_api, stored_result, monkeypatch
):
    data.session.mark_deleted()
    await data.db.commit()
    monkeypatch.setattr(
        "src.services.analysis_runs.start_analysis", forbid_analysis
    )
    response = await export_api.post(
        f"/admin/sessions/{data.session.id}/analyze_regenerate",
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 404, response.text
    async with data.factory() as db:
        assert list(await db.scalars(select(GenerationRun))) == []


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
async def test_admin_end_does_not_mutate_deleted_active_session(
    data, export_api, stored_result, monkeypatch
):
    data.session.ended_at = None
    data.session.mark_deleted()
    await data.db.commit()
    monkeypatch.setattr(
        "src.services.analysis_runs.start_analysis", forbid_analysis
    )
    response = await export_api.post(
        f"/admin/sessions/{data.session.id}/end",
        json={"request_id": str(uuid4())},
    )
    assert response.status_code == 404, response.text
    async with data.factory() as db:
        assert (await db.get(Session, data.session.id)).ended_at is None
        assert list(await db.scalars(select(GenerationRun))) == []


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
@pytest.mark.parametrize("surface", ["list", "detail"])
async def test_deleted_question_analysis_is_hidden_from_admin_list_and_detail(
    data, export_api, stored_result, surface
):
    analysis_id = await data.db.scalar(select(QuestionAnalysis.id))
    await data.db.execute(
        update(QuestionAnalysis).values(
            label="DELETED CLASSIFICATION 169", confidence=0.99
        )
    )
    data.session.mark_deleted()
    await data.db.commit()
    if surface == "list":
        page = await export_api.get("/admin/analysis-page")
        assert page.status_code == 200, page.text
        for message in stored_result[1]["messages"]:
            assert message["content"] not in unescape(page.text)
        assert f"/admin/analysis/{analysis_id}/detail" not in page.text
        assert "DELETED CLASSIFICATION 169" not in page.text
        assert "99%" not in page.text
    else:
        detail = await export_api.get(f"/admin/analysis/{analysis_id}/detail")
        assert detail.status_code == 404, detail.text


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
async def test_deleted_session_stays_out_of_lists_and_every_csv_export(
    data, export_api, export_history, stored_result
):
    sid = data.session.id
    data.session.mark_deleted()
    await data.db.commit()
    listing = await export_api.get("/admin/sessions")
    assert listing.status_code == 200
    assert [row["id"] for row in listing.json()["sessions"]] == [
        export_history.id
    ]
    page = await export_api.get("/admin/sessions-page")
    assert page.status_code == 200
    assert f"/admin/sessions/{sid}/delete" not in page.text
    assert (await download(export_api, "single", [sid])).status_code == 404
    for mode in ("all", "selected"):
        rows = csv_rows(
            await download(export_api, mode, [sid, export_history.id]),
            mode,
            sid,
        )
        assert {row["session_id"] for row in rows} == {str(export_history.id)}


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
async def test_deleted_conversation_content_is_not_reachable_by_its_teacher(
    data, export_api, stored_result
):
    data.session.mark_deleted()
    await data.db.commit()
    await authenticate(export_api, data.owner)
    base = f"/sessions/{data.session.id}"
    for path in (
        "",
        "/analysis",
        "/analysis_page",
        "/analysis_modal",
        "/messages/updates",
        "/export.csv",
    ):
        response = await export_api.get(base + path)
        assert response.status_code == 404, (path, response.text)
    for path in ("/analyze", "/end", "/close", "/messages"):
        response = await export_api.post(
            base + path, json={"request_id": str(uuid4())}
        )
        assert response.status_code == 404, (path, response.text)


@pytest.mark.parametrize("stored_result", ["ok"], indirect=True)
async def test_session_admin_paths_keep_authentication_and_delete_csrf_guards(
    data, export_api, stored_result
):
    sid = data.session.id
    token = export_api.headers.pop("x-csrf-token")
    for invalid in (None, "forged"):
        if invalid:
            export_api.headers["x-csrf-token"] = invalid
        denied = await export_api.post(f"/admin/sessions/{sid}/delete")
        assert denied.status_code == 403
        assert denied.text == "CSRF token verification failed"
    export_api.headers["x-csrf-token"] = token
    for user, expected in ((data.other, 403), (None, 303)):
        export_api.cookies.clear()
        await export_api.get("/health")
        export_api.headers["x-csrf-token"] = export_api.cookies["csrftoken"]
        if user:
            await authenticate(export_api, user)
        for path in ("detail", "analysis", "analysis_modal"):
            response = await export_api.get(f"/admin/sessions/{sid}/{path}")
            assert response.status_code == expected, response.text
            if expected == 303:
                assert response.headers["location"] == "/login"
        for path in ("delete", "end", "analyze_regenerate"):
            response = await export_api.post(
                f"/admin/sessions/{sid}/{path}",
                json={"request_id": str(uuid4())},
            )
            assert response.status_code == expected, response.text
    async with data.factory() as db:
        assert (await db.get(Session, sid)).deleted_at is None
