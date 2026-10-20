"""#202 D2: the seed completed example carries §19 provenance seed_prepared."""

import asyncio
import json
from shutil import copyfile
from types import SimpleNamespace

import pytest
import pytest_asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from starlette.requests import Request

from src.api.routes.admin_session_actions import analysis_modal, session_detail
from src.demo import seed
from src.demo.session import Provenance
from src.models import SessionFeedbackReport, User
from src.services import analysis_plan
from src.services.analysis_results import load_analysis_response

SEED_PREPARED = Provenance(record_origin="seed_prepared").model_dump()


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    out = tmp_path_factory.mktemp("seed")
    asyncio.run(seed.build(out))
    return out


@pytest_asyncio.fixture
async def db(built, tmp_path, monkeypatch):
    # ponytail: admin responses plan the analysis, which raises KeyError "demo"
    # until #198 fixes estimate_request (D1); drop this stub with that fix.
    monkeypatch.setattr(analysis_plan, "estimate_request", lambda request: 0)
    copy = tmp_path / "seed.db"
    copyfile(built / seed.SEED_NAME, copy)
    engine = create_async_engine(f"sqlite+aiosqlite:///{copy}")
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


def request():
    return Request(
        {
            "type": "http",
            "method": "GET",
            "path": "/",
            "headers": [],
            "query_string": b"",
            "state": {},
            "app": SimpleNamespace(state=SimpleNamespace()),
        }
    )


async def detail(db):
    admin = await db.get(User, 1)
    response = await session_detail(request(), 1, admin, db)
    return response.body.decode()


@pytest.mark.parametrize("admin", [True, False])
async def test_seed_analysis_json_is_seed_prepared(db, admin):
    response = await load_analysis_response(1, db, admin=admin)
    assert response["provenance"] == SEED_PREPARED


async def test_admin_detail_and_modal_show_seed_prepared(db):
    assert 'data-record-origin="seed_prepared"' in await detail(db)
    admin = await db.get(User, 1)
    modal = (await analysis_modal(request(), 1, admin, db)).body.decode()
    assert 'data-record-origin="seed_prepared"' in modal


async def test_report_without_provenance_renders_no_origin(db):
    # Normal-mode reports never carry provenance; nothing may be added.
    report = await db.scalar(select(SessionFeedbackReport))
    payload = json.loads(report.payload_json)
    payload["metadata"].pop("provenance", None)
    report.payload_json = json.dumps(payload)
    await db.commit()
    assert "provenance" not in await load_analysis_response(1, db, admin=True)
    assert "data-record-origin" not in await detail(db)
