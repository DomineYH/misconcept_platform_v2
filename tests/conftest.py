"""All tests use isolated SQLite and reject outbound network access."""

import os
import socket
from datetime import datetime
from types import SimpleNamespace

os.environ.update(
    TESTING="true",
    DATABASE_URL="sqlite+aiosqlite:///:memory:",
    OPENAI_API_KEY="test-only",
    SESSION_SECRET="test-only",
)

import pytest
from legacy_models import AnalysisFramework, Scenario
from sqlalchemy import MetaData, Table, event, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import load_only

from src.db.connection import Base, set_sqlite_pragma
from src.models import Session, User
from src.models.scenario_group import ScenarioGroup
from src.models.user_group import UserGroup


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    def reject(*args, **kwargs):
        raise AssertionError("Tests must not access the network")

    monkeypatch.setattr(socket.socket, "connect", reject)


@pytest.fixture
async def data(tmp_path, request):
    engine = create_async_engine(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    async with engine.begin() as conn:
        if getattr(request, "param", None) == "baseline":
            from src.db.migrations import migrate

            for statement in migrate.statements(
                (migrate.DIRECTORY / "baseline.sql").read_text()
            ):
                await conn.exec_driver_sql(statement)
        else:
            from legacy_models import PromptTemplate

            metadata = MetaData()
            for table in Base.metadata.tables.values():
                if table.name != "scenario":
                    table.to_metadata(metadata)
            for table in (
                AnalysisFramework.__table__,
                PromptTemplate.__table__,
                Scenario.__table__,
            ):
                table.to_metadata(metadata)
            await conn.run_sync(metadata.create_all)
    factory = async_sessionmaker(
        engine, expire_on_commit=False, autoflush=False
    )
    async with factory() as db:
        group = UserGroup(name="test")
        db.add(group)
        await db.flush()
        owner = User(username="owner", nickname="Owner", group_id=group.id)
        other = User(username="other", nickname="Other")
        admin = User(username="admin", nickname="Admin", role="admin")
        framework = AnalysisFramework(name="Test", labels_json='["A", "B"]')
        db.add_all([owner, other, admin, framework])
        await db.flush()
        scenario = Scenario(
            title="Test", prompt="Test misconception", framework_id=framework.id
        )
        if getattr(request, "param", None) == "baseline":
            scenario = await legacy_record(
                db,
                Scenario,
                dict(
                    title="Test",
                    prompt="Test misconception",
                    framework_id=framework.id,
                    is_active=1,
                    tutor_sensitivity="medium",
                    created_at=datetime(2026, 1, 1),
                ),
            )
        else:
            db.add(scenario)
            await db.flush()
        session = Session(
            scenario_id=scenario.id,
            teacher_id=owner.id,
            ended_at=datetime(2026, 1, 2),
            started_at=datetime(2026, 1, 1),
        )
        if getattr(request, "param", None) == "baseline":
            session = await legacy_record(
                db,
                Session,
                dict(
                    scenario_id=scenario.id,
                    teacher_id=owner.id,
                    ended_at=datetime(2026, 1, 2),
                    started_at=datetime(2026, 1, 1),
                    tutor_intervention_count=0,
                    tutor_question_count=0,
                ),
            )
        else:
            db.add(session)
        db.add(ScenarioGroup(scenario_id=scenario.id, group_id=group.id))
        await db.commit()
        yield SimpleNamespace(
            db=db,
            factory=factory,
            engine=engine,
            owner=owner,
            other=other,
            admin=admin,
            framework=framework,
            scenario=scenario,
            session=session,
        )
    await engine.dispose()


async def legacy_record(db, model, values):
    """Seed the immutable deployed schema without new ORM expansion columns."""
    conn = await db.connection()
    table = await conn.run_sync(
        lambda conn: Table(model.__tablename__, MetaData(), autoload_with=conn)
    )
    result = await db.execute(table.insert().values(**values))
    return await db.scalar(
        select(model)
        .where(model.id == result.inserted_primary_key[0])
        .options(
            load_only(
                *(getattr(model, column.name) for column in table.columns)
            )
        )
    )
