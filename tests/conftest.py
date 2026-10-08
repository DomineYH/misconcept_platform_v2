"""All tests use isolated SQLite and reject outbound network access."""
import os
import socket
from datetime import datetime
from types import SimpleNamespace

os.environ.update(TESTING="true", DATABASE_URL="sqlite+aiosqlite:///:memory:",
                  OPENAI_API_KEY="test-only", SESSION_SECRET="test-only")

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from src.db.connection import Base, set_sqlite_pragma
from src.models import AnalysisFramework, Scenario, Session, User
from src.models.user_group import UserGroup
from src.models.scenario_group import ScenarioGroup


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
            await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False, autoflush=False)
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
        scenario = Scenario(title="Test", prompt="Test misconception", framework_id=framework.id)
        db.add(scenario)
        await db.flush()
        session = Session(scenario_id=scenario.id, teacher_id=owner.id,
                          ended_at=datetime(2026, 1, 2), started_at=datetime(2026, 1, 1))
        db.add_all([session, ScenarioGroup(scenario_id=scenario.id, group_id=group.id)])
        await db.commit()
        yield SimpleNamespace(db=db, factory=factory, engine=engine, owner=owner,
                              other=other, admin=admin, framework=framework,
                              scenario=scenario, session=session)
    await engine.dispose()
