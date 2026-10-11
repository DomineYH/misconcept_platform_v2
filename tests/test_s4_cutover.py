"""S3-final to S4 on a committed WAL backup, with restoration and readers."""

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from test_s2_cutover import source
from test_scenario_api import client, login

from src.db.connection import set_sqlite_pragma
from src.db.migrations import migrate
from src.db.s2_cutover import rehearse

__all__ = ["source", "client"]
pytestmark = pytest.mark.parametrize("data", ["baseline"], indirect=True)


def digest(rows):
    return hashlib.sha256(repr(rows).encode("utf-8")).hexdigest()


async def test_s4_wal_copy_migration_restore_preserves_hashes_readers_and_acl(
    data, source, tmp_path, client
):
    numbers = [
        int(path.name.split("_", 1)[0])
        for path in migrate.DIRECTORY.glob("*.sql")
        if path.name[0].isdigit() and int(path.name.split("_", 1)[0]) >= 24
    ]
    assert max(numbers) == 34 and len(numbers) == len(set(numbers))
    workspace = tmp_path / "s3"
    await rehearse(
        source,
        workspace,
        Path("tests/fixtures/s2_legacy_effective.json"),
        apply=True,
    )
    source_path = workspace / "rehearsal.db"
    source_engine = create_async_engine(f"sqlite+aiosqlite:///{source_path}")
    try:
        await migrate.run_all_migrations(through=33, db_engine=source_engine)
    finally:
        await source_engine.dispose()
    backup_path, candidate_path = (
        tmp_path / "backup.db",
        tmp_path / "candidate.db",
    )
    with sqlite3.connect(source_path) as original:
        original.execute("PRAGMA foreign_keys=ON")
        assert original.execute("PRAGMA journal_mode=WAL").fetchone() == (
            "wal",
        )
        original.execute("PRAGMA wal_autocheckpoint=0")
        original.execute(
            "INSERT INTO generation_run(id,owner_id,session_id,turn_id,operation,request_id,input_hash,config_hash,provider,model,status,result_kind,started_at,finished_at) "
            "VALUES('completed-s3',1,1,'s3-turn','student','s3-request','s3-input','s3-config','openai','old-model','completed','message','2026-01-01','2026-01-01')"
        )
        original.execute(
            "INSERT INTO message(id,session_id,role,content,created_at,turn_id,turn_index,generation_run_id) "
            "VALUES(50,1,'student','Original 한글, \"quote\"\nanswer','2026-01-01','s3-turn',1,'completed-s3')"
        )
        original.execute(
            "INSERT INTO api_usage_log(id,session_id,run_id,operation,status,timestamp,total_tokens,estimated_cost_usd,raw_usage_json,context_budget_json) "
            "VALUES(51,1,'completed-s3','student','completed','2026-01-01',17,0.123456,'{\"original\":17}','{\"estimator\":\"utf8-v1\"}'),"
            "(52,1,'completed-s3','mentor_judgment','failed','2026-01-01',NULL,NULL,NULL,NULL)"
        )
        original.commit()
        assert Path(str(source_path) + "-wal").stat().st_size > 0
        before = {
            table: (
                [
                    row[1]
                    for row in original.execute(f'PRAGMA table_info("{table}")')
                ],
                original.execute(
                    f'SELECT * FROM "{table}" ORDER BY rowid'
                ).fetchall(),
            )
            for (table,) in original.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        original_schema = original.execute(
            "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
        ).fetchall()
        with sqlite3.connect(backup_path) as backup:
            original.backup(backup)
            with sqlite3.connect(candidate_path) as restored:
                backup.backup(restored)
                assert restored.execute(
                    "PRAGMA integrity_check"
                ).fetchone() == ("ok",)
                assert (
                    restored.execute("PRAGMA foreign_key_check").fetchall()
                    == []
                )
                assert (
                    restored.execute(
                        "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                    ).fetchall()
                    == original_schema
                )
                for table, (_, rows) in before.items():
                    assert (
                        restored.execute(
                            f'SELECT * FROM "{table}" ORDER BY rowid'
                        ).fetchall()
                        == rows
                    )

    engine = create_async_engine(f"sqlite+aiosqlite:///{candidate_path}")
    event.listen(engine.sync_engine, "connect", set_sqlite_pragma)
    data.factory = async_sessionmaker(engine, expire_on_commit=False)
    try:
        for _ in range(2):
            await migrate.run_all_migrations(through=34, db_engine=engine)
        with sqlite3.connect(candidate_path) as candidate:
            assert candidate.execute("PRAGMA integrity_check").fetchone() == (
                "ok",
            )
            assert (
                candidate.execute("PRAGMA foreign_key_check").fetchall() == []
            )
            hashes = {}
            for table, (columns, rows) in before.items():
                fields = ",".join(f'"{column}"' for column in columns)
                actual = candidate.execute(
                    f'SELECT {fields} FROM "{table}" ORDER BY rowid'
                ).fetchall()
                if table == "_migrations":
                    actual = actual[: len(rows)]
                assert actual == rows
                hashes[table] = digest(actual)
                assert hashes[table] == digest(rows)
            assert candidate.execute(
                "SELECT count(*) FROM _migrations WHERE filename='034_analysis_run.sql'"
            ).fetchone() == (1,)
            assert candidate.execute(
                "SELECT plan_json,outcome_json,accepted_report_id,accepted_report_version FROM generation_run"
            ).fetchall() == [(None, None, None, None)] * len(
                before["generation_run"][1]
            )
            with sqlite3.connect(tmp_path / "restored-s4.db") as restored:
                candidate.backup(restored)
                assert restored.execute(
                    "PRAGMA integrity_check"
                ).fetchone() == ("ok",)
                assert (
                    restored.execute("PRAGMA foreign_key_check").fetchall()
                    == []
                )
                for (table,) in candidate.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
                ).fetchall():
                    assert (
                        restored.execute(
                            f'SELECT * FROM "{table}" ORDER BY rowid'
                        ).fetchall()
                        == candidate.execute(
                            f'SELECT * FROM "{table}" ORDER BY rowid'
                        ).fetchall()
                    )
        (tmp_path / "preservation-hashes.json").write_text(
            json.dumps(hashes), encoding="utf-8"
        )
        login(client, data.owner)
        result = await client.get("/sessions/1/analysis")
        assert result.status_code == 200
        assert result.json()["accepted_report"]["status"] == "legacy"
        assert result.json()["accepted_report"]["coverage"] is None
        assert [m["content"] for m in result.json()["messages"]] == [
            "Original {text}",
            'Original 한글, "quote"\nanswer',
        ]
        for path in (
            "/sessions/1",
            "/sessions/1/analysis_modal",
            "/sessions/1/export.csv",
        ):
            assert (await client.get(path)).status_code == 200
        csv = await client.get("/sessions/1/export.csv")
        assert "Original feedback" in csv.text and "Original label" in csv.text
        assert "analysis_schema_version" in csv.text and "legacy" in csv.text
        for action in ("analyze", "end"):
            blocked = await client.post(
                f"/sessions/1/{action}",
                json=dict(request_id="00000000-0000-4000-8000-000000000001"),
            )
            assert (
                blocked.status_code == 409
                and blocked.json()["detail"]["code"] == "legacy_read_only"
            )
        login(client, data.other)
        for path in (
            "/sessions/1",
            "/sessions/1/analysis",
            "/sessions/1/export.csv",
        ):
            assert (await client.get(path)).status_code == 403
        login(client, data.admin)
        assert (await client.get("/sessions/1/export.csv")).status_code == 403
        assert (
            await client.get("/admin/sessions/1/analysis")
        ).status_code == 200
        assert (
            "Original feedback"
            in (await client.get("/admin/sessions/export")).text
        )
        with sqlite3.connect(source_path) as original:
            assert (
                original.execute(
                    "SELECT type,name,sql FROM sqlite_master ORDER BY type,name"
                ).fetchall()
                == original_schema
            )
            for table, (_, rows) in before.items():
                assert (
                    original.execute(
                        f'SELECT * FROM "{table}" ORDER BY rowid'
                    ).fetchall()
                    == rows
                )
    finally:
        await engine.dispose()
