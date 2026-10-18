"""Physical demo DB trust and generation lifetime boundaries."""

from uuid import uuid4

import pytest

from src.demo.database import DatabaseRoot, DemoError


def test_root_requires_explicit_empty_initialization_and_exclusive_lock(
    tmp_path,
):
    path = tmp_path / "demo"
    path.mkdir(mode=0o700)
    with pytest.raises(DemoError, match="DEMO_CONFIGURATION_INVALID"):
        DatabaseRoot(path).open()
    root = DatabaseRoot(path, initialize=True).open()
    try:
        assert root.root_id.version == 4
        with pytest.raises(DemoError, match="DEMO_CONFIGURATION_INVALID"):
            DatabaseRoot(path).open()
    finally:
        root.close()
    reopened = DatabaseRoot(path).open()
    assert reopened.root_id == root.root_id
    reopened.close()


def test_root_rejects_existing_data_and_symlink(tmp_path):
    path = tmp_path / "existing"
    path.mkdir()
    (path / "ordinary.db").touch()
    with pytest.raises(DemoError, match="DEMO_CONFIGURATION_INVALID"):
        DatabaseRoot(path, initialize=True).open()
    link = tmp_path / "link"
    link.symlink_to(path, target_is_directory=True)
    with pytest.raises(DemoError, match="DEMO_CONFIGURATION_INVALID"):
        DatabaseRoot(link).open()


async def test_clone_requires_verified_seed(tmp_path):
    root = DatabaseRoot(tmp_path / "demo", initialize=True).open()
    try:
        with pytest.raises(DemoError, match="DEMO_CONFIGURATION_INVALID"):
            await root.clone(str(uuid4()), 1)
    finally:
        root.close()


async def make_root(tmp_path, *, max_db_bytes=None):
    """A tiny synthetic seed, using the official explicit-engine installer."""
    import hashlib
    import json
    import sqlite3

    from sqlalchemy.ext.asyncio import create_async_engine

    from src.db.migrations.migrate import run_all_migrations
    from src.demo.database import SeedSpec

    root = DatabaseRoot(
        tmp_path / "demo", initialize=True, max_db_bytes=max_db_bytes
    ).open()
    seed = root.path / "seed.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{seed}")
    await run_all_migrations(db_engine=engine)
    await engine.dispose()
    manifest = dict(
        manifest_version=1,
        contract_version="d0-03-v1-r3",
        content_version="d0-02-v1",
        fixture_version="demo-fixture-v1",
        db_schema_version="unit_demo_metadata.sql",
        migration_set_sha256="a" * 64,
        scenario_config_schema_version=1,
        analysis_schema_version=2,
        provider="demo",
        model_id="demo-classroom-v1",
        capability_definition_version="demo-cap-v1",
        role_contract_versions=dict(
            student="s1-v1", mentor="s3-v1", analysis="s4-v2"
        ),
        scenario_ref="unit-fraction-v1",
        supported_config_sha256="b" * 64,
        seed_version="demo-seed-v1",
        seed_timestamp="2026-10-18T00:00:00Z",
        seed_refs=dict(
            admin=1,
            teacher=2,
            group=1,
            scenario=1,
            completed_session=1,
            model=1,
            connection=1,
        ),
        files=[],
        lookup_version=1,
        responses={},
        seed_provenance=dict(
            source="synthetic",
            generator="src/demo/seed.py",
            content_version="d0-02-v1",
        ),
    )
    manifest_path = root.path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    digest = hashlib.sha256(manifest_path.read_bytes()).hexdigest()
    with sqlite3.connect(seed) as db:
        db.executemany(
            "INSERT INTO user(id,username,nickname,password_hash,role,created_at) VALUES(?,?,?,?,?,?)",
            [
                (
                    1,
                    "fictional-admin",
                    "Fictional admin",
                    "!login-disabled",
                    "admin",
                    "2026-10-18 00:00:00",
                ),
                (
                    2,
                    "fictional-teacher",
                    "Fictional teacher",
                    "!login-disabled",
                    "teacher",
                    "2026-10-18 00:00:00",
                ),
            ],
        )
        # G2 owns production metadata DDL; this models that explicit boundary only.
        db.execute(
            "CREATE TABLE demo_metadata (id INTEGER PRIMARY KEY, kind TEXT, contract_version TEXT, content_version TEXT, fixture_version TEXT, db_schema_version TEXT, manifest_sha256 TEXT, visitor_id TEXT, generation INTEGER)"
        )
        db.execute(
            "INSERT INTO demo_metadata VALUES (1,'demo_seed','d0-03-v1-r3','d0-02-v1','demo-fixture-v1','unit_demo_metadata.sql',?,NULL,NULL)",
            (digest,),
        )
        db.execute(
            "INSERT INTO _migrations(filename) VALUES ('unit_demo_metadata.sql')"
        )
        db.execute(
            "CREATE TABLE synthetic_record(id INTEGER PRIMARY KEY, body TEXT NOT NULL)"
        )
        db.execute("INSERT INTO synthetic_record VALUES(1,'fictional')")
        history = tuple(
            row[0]
            for row in db.execute(
                "SELECT filename FROM _migrations ORDER BY id"
            )
        )

    def verify_content(db, manifest):
        if (
            db.execute(
                "SELECT body FROM synthetic_record WHERE id=1"
            ).fetchone()[0]
            != "fictional"
        ):
            raise DemoError("VERSION_MISMATCH")

    root.validate_seed(
        SeedSpec(
            seed,
            manifest_path,
            "unit_demo_metadata.sql",
            "a" * 64,
            history,
            verify_content,
        )
    )
    return root


async def test_backup_includes_committed_wal_and_isolates_factories(tmp_path):
    import sqlite3

    from sqlalchemy import text

    root = await make_root(tmp_path)
    writer = sqlite3.connect(root.path / "seed.db")
    try:
        writer.execute("PRAGMA journal_mode=WAL")
        writer.execute("PRAGMA wal_autocheckpoint=0")
        writer.execute(
            "INSERT INTO synthetic_record VALUES (2,'committed WAL')"
        )
        writer.commit()
        a = await root.clone(str(uuid4()), 1)
        b = await root.clone(str(uuid4()), 1)
        async with a.factory() as session:
            assert (
                await session.scalar(
                    text("SELECT body FROM synthetic_record WHERE id=2")
                )
                == "committed WAL"
            )
            await session.execute(
                text("UPDATE synthetic_record SET body='A' WHERE id=2")
            )
            await session.commit()
            for pragma, expected in (
                ("foreign_keys", 1),
                ("journal_mode", "wal"),
                ("synchronous", 1),
                ("busy_timeout", 1000),
            ):
                assert (
                    await session.scalar(text("PRAGMA " + pragma)) == expected
                )
        async with b.factory() as session:
            assert (
                await session.scalar(
                    text("SELECT body FROM synthetic_record WHERE id=2")
                )
                == "committed WAL"
            )
        assert a.factory.engine is not b.factory.engine
        assert root.owns(a) and root.owns(b)
        assert (
            root.bytes_used()
            >= root.generation_bytes(a)
            + root.generation_bytes(b)
            + root.seed_bytes
        )
        a.factory.retire()
        b.factory.retire()
        await root.cleanup(a)
        await root.cleanup(b)
    finally:
        writer.close()
        root.close()


async def test_retired_factory_rejects_new_sessions_and_connections_without_recreation(
    tmp_path,
):
    from sqlalchemy import text

    root = await make_root(tmp_path)
    ref = await root.clone(str(uuid4()), 1)
    unused_session = ref.factory()
    async with ref.factory() as session:
        assert await session.scalar(text("SELECT 1")) == 1
    ref.factory.retire()
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        ref.factory()
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        await unused_session.execute(text("SELECT 1"))
    await unused_session.close()
    with pytest.raises(DemoError, match="DEMO_STALE_GENERATION"):
        async with ref.factory.engine.connect():
            pass
    await root.cleanup(ref)
    assert not ref.path.exists()
    with pytest.raises(DemoError):
        async with ref.factory.engine.connect():
            pass
    assert not ref.path.exists()
    root.close()


async def test_publication_write_check_guards_raw_factory_and_commit(tmp_path):
    from sqlalchemy import text

    root = await make_root(tmp_path, max_db_bytes=1048576)
    ref = await root.clone(str(uuid4()), 1)
    allowed = False

    def check():
        if not allowed:
            raise DemoError("STALE_ROLE")

    ref.factory.set_write_check(check)
    try:
        async with ref.factory() as session:
            assert (
                await session.scalar(
                    text("SELECT body FROM synthetic_record WHERE id=1")
                )
                == "fictional"
            )
            assert (
                0 < await session.scalar(text("PRAGMA max_page_count")) <= 256
            )
            with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
                await session.execute(
                    text("UPDATE synthetic_record SET body='bypass' WHERE id=1")
                )
            await session.rollback()
            allowed = True
            await session.execute(
                text(
                    "UPDATE synthetic_record SET body='uncommitted' WHERE id=1"
                )
            )
            allowed = False
            with pytest.raises(DemoError, match="DEMO_STALE_ROLE"):
                await session.commit()
            await session.rollback()
        async with ref.factory() as session:
            assert (
                await session.scalar(
                    text("SELECT body FROM synthetic_record WHERE id=1")
                )
                == "fictional"
            )
            allowed = True
            with pytest.raises(DemoError, match="DEMO_ACTION_FORBIDDEN"):
                await session.execute(text("PRAGMA foreign_keys=OFF"))
    finally:
        ref.factory.retire()
        await root.cleanup(ref)
        root.close()


async def test_cleanup_rejects_forged_refs_replaced_files_and_symlink_sidecars(
    tmp_path,
):
    from dataclasses import replace

    root = await make_root(tmp_path)
    ref = await root.clone(str(uuid4()), 1)
    ref.factory.retire()
    try:
        with pytest.raises(DemoError, match="DEMO_INVALID_REQUEST"):
            await root.cleanup(replace(ref))
        target = tmp_path / "keep.db"
        target.write_text("keep")
        sidecar = ref.path.with_name(ref.path.name + "-wal")
        sidecar.symlink_to(target)
        with pytest.raises(DemoError, match="DEMO_CONFIGURATION_INVALID"):
            await root.cleanup(ref)
        assert target.read_text() == "keep"
        assert ref.path.exists()
        sidecar.unlink()
        parked = ref.path.with_suffix(".parked")
        ref.path.rename(parked)
        ref.path.write_text("different inode")
        with pytest.raises(DemoError, match="DEMO_CONFIGURATION_INVALID"):
            await root.cleanup(ref)
        ref.path.unlink()
        parked.rename(ref.path)
        await root.cleanup(ref)
        assert not root.owns(ref)
    finally:
        root.close()


async def test_changed_manifest_and_invalid_seed_do_not_publish_or_leave_candidates(
    tmp_path,
):
    import sqlite3

    root = await make_root(tmp_path)
    manifest = root.path / "manifest.json"
    original = manifest.read_bytes()
    try:
        manifest.write_bytes(original + b" ")
        with pytest.raises(DemoError, match="DEMO_VERSION_MISMATCH"):
            await root.clone(str(uuid4()), 1)
        assert not list(root.path.glob("*.staging"))
        assert list(root.path.glob("*.db")) == [root.path / "seed.db"]
        manifest.write_bytes(original)
        with sqlite3.connect(root.path / "seed.db") as db:
            db.execute("UPDATE demo_metadata SET kind='ordinary'")
        with pytest.raises(DemoError, match="DEMO_VERSION_MISMATCH"):
            await root.clone(str(uuid4()), 1)
        assert not list(root.path.glob("*.staging"))
    finally:
        root.close()


def _revalidate(root, verify_content):
    import json
    import sqlite3

    from src.demo.database import SeedSpec

    manifest_path = root.path / "manifest.json"
    manifest = json.loads(manifest_path.read_bytes())
    with sqlite3.connect(root.path / "seed.db") as db:
        history = tuple(
            row[0]
            for row in db.execute(
                "SELECT filename FROM _migrations ORDER BY id"
            )
        )
    root.validate_seed(
        SeedSpec(
            root.path / "seed.db",
            manifest_path,
            manifest["db_schema_version"],
            manifest["migration_set_sha256"],
            history,
            verify_content,
        )
    )


async def test_repeated_cancellation_waits_for_builder_before_removing_staging(
    tmp_path,
):
    import asyncio
    import threading

    root = await make_root(tmp_path)
    entered = threading.Event()
    resume = threading.Event()

    def verify(db, manifest):
        if (
            db.execute("SELECT kind FROM demo_metadata").fetchone()[0]
            == "demo_visitor"
        ):
            entered.set()
            assert resume.wait(5)

    _revalidate(root, verify)
    task = asyncio.create_task(root.clone(str(uuid4()), 1))
    try:
        assert await asyncio.to_thread(entered.wait, 5)
        assert list(root.path.glob("*.staging"))
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        assert not task.done()
        assert list(root.path.glob("*.staging"))
        resume.set()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert not list(root.path.glob("*.staging"))
        assert list(root.path.glob("*.db")) == [root.path / "seed.db"]
    finally:
        resume.set()
        await asyncio.gather(task, return_exceptions=True)
        root.close()


async def test_locked_retired_generation_keeps_files_for_retry(tmp_path):
    import sqlite3

    root = await make_root(tmp_path)
    ref = await root.clone(str(uuid4()), 1)
    writer = sqlite3.connect(ref.path)
    writer.execute("BEGIN IMMEDIATE")
    ref.factory.retire()
    try:
        with pytest.raises(DemoError, match="DEMO_DB_LOCKED") as error:
            await root.cleanup(ref)
        assert error.value.retry_after == 1
        assert root.owns(ref) and root.generation_bytes(ref) > 0
        writer.rollback()
        writer.close()
        await root.cleanup(ref)
        assert not ref.path.exists()
    finally:
        writer.close()
        root.close()


async def test_failed_destination_verification_removes_staging_and_retry_is_unique(
    tmp_path,
):
    root = await make_root(tmp_path)

    def verify(db, manifest):
        if (
            db.execute("SELECT kind FROM demo_metadata").fetchone()[0]
            == "demo_visitor"
        ):
            raise DemoError("VERSION_MISMATCH")

    _revalidate(root, verify)
    try:
        visitor_id = str(uuid4())
        with pytest.raises(DemoError, match="DEMO_VERSION_MISMATCH"):
            await root.clone(visitor_id, 1)
        assert not list(root.path.glob("*.staging"))
        _revalidate(
            root, lambda db, manifest: db.execute("SELECT 1").fetchone()
        )
        a = await root.clone(visitor_id, 1)
        b = await root.clone(visitor_id, 1)
        assert a.path != b.path
        for ref in (a, b):
            ref.factory.retire()
            await root.cleanup(ref)
    finally:
        root.close()


async def test_restart_verifies_and_cleans_orphans_but_refuses_unknown_files(
    tmp_path,
):
    from src.demo.database import DatabaseRoot

    first = await make_root(tmp_path)
    old = await first.clone(str(uuid4()), 1)
    old.factory.retire()
    await old.factory.dispose()
    first.close()
    restarted = DatabaseRoot(first.path).open()
    try:
        _revalidate(
            restarted, lambda db, manifest: db.execute("SELECT 1").fetchone()
        )
        refs = restarted.orphan_refs()
        assert len(refs) == 1
        assert refs[0].path == old.path and refs[0].factory.retired
        await restarted.cleanup(refs[0])
        assert not old.path.exists()
        unknown = restarted.path / "ordinary.db"
        unknown.write_text("not demo")
        with pytest.raises(DemoError, match="DEMO_CONFIGURATION_INVALID"):
            restarted.orphan_refs()
        assert unknown.read_text() == "not demo"
    finally:
        restarted.close()


async def test_open_session_blocks_dispose_and_cleanup_until_connection_returned(
    tmp_path,
):
    from sqlalchemy import text

    root = await make_root(tmp_path)
    ref = await root.clone(str(uuid4()), 1)
    session = ref.factory()
    await session.execute(text("SELECT 1"))
    ref.factory.retire()
    try:
        with pytest.raises(DemoError, match="DEMO_DB_LOCKED"):
            await root.cleanup(ref)
        assert ref.path.exists() and not ref.factory.disposed
        await session.close()
        await root.cleanup(ref)
        assert ref.factory.disposed and not ref.path.exists()
    finally:
        await session.close()
        root.close()


async def test_page_cap_and_wal_headroom_reject_large_write_without_durable_growth(
    tmp_path,
):
    from sqlalchemy import text

    limit = 33554432
    root = await make_root(tmp_path, max_db_bytes=limit)
    ref = await root.clone(str(uuid4()), 1)
    try:
        assert ref.factory.write_headroom_bytes > 0

        def check_budget():
            if (
                root.generation_bytes(ref) + ref.factory.write_headroom_bytes
                > limit
            ):
                raise DemoError("STORAGE_LIMIT")

        ref.factory.set_write_check(check_budget)
        async with ref.factory() as session:
            await session.execute(
                text("INSERT INTO synthetic_record VALUES(2,zeroblob(1048576))")
            )
            await session.commit()
            assert root.generation_bytes(ref) <= limit
            assert await session.scalar(text("PRAGMA cache_spill")) == 0
            with pytest.raises(DemoError, match="DEMO_STORAGE_LIMIT"):
                await session.execute(
                    text(
                        "INSERT INTO synthetic_record VALUES(3,zeroblob(33554432))"
                    )
                )
            await session.rollback()
            assert (
                await session.scalar(
                    text("SELECT count(*) FROM synthetic_record")
                )
                == 2
            )
        assert root.generation_bytes(ref) <= limit
    finally:
        ref.factory.retire()
        await root.cleanup(ref)
        root.close()


async def test_failed_staging_unlink_retains_operation_and_retries_only_owned_inode(
    tmp_path, monkeypatch
):
    from pathlib import Path

    root = await make_root(tmp_path)

    def verify(db, manifest):
        if (
            db.execute("SELECT kind FROM demo_metadata").fetchone()[0]
            == "demo_visitor"
        ):
            raise DemoError("VERSION_MISMATCH")

    _revalidate(root, verify)
    operation_id = str(uuid4())
    original_unlink = Path.unlink

    def locked_unlink(path, *args, **kwargs):
        if path.suffix == ".staging":
            raise PermissionError("simulated locked file")
        return original_unlink(path, *args, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(Path, "unlink", locked_unlink)
            with pytest.raises(DemoError):
                await root.clone(str(uuid4()), 1, operation_id=operation_id)
        assert root.pending_operations == {operation_id}
        staged = next(root.path.glob("*.staging"))
        parked = staged.with_suffix(".parked")
        staged.rename(parked)
        staged.write_text("replacement must survive")
        result = await root.cleanup_pending()
        assert result["pending"] == 1
        assert result["codes"] == ("DEMO_CONFIGURATION_INVALID",)
        assert staged.read_text() == "replacement must survive"
        staged.unlink()
        parked.rename(staged)
        result = await root.cleanup_pending()
        assert result["removed"] == 1 and result["pending"] == 0
        assert not root.pending_operations and not staged.exists()
    finally:
        root.close()


async def test_cancelled_after_rename_keeps_operation_when_candidate_cleanup_is_locked(
    tmp_path, monkeypatch
):
    import asyncio
    import os
    import threading
    from pathlib import Path

    root = await make_root(tmp_path)
    renamed = threading.Event()
    resume = threading.Event()
    original_rename = os.rename
    original_unlink = Path.unlink
    operation_id = str(uuid4())

    def pause_rename(source, destination):
        original_rename(source, destination)
        renamed.set()
        assert resume.wait(5)

    def locked_unlink(path, *args, **kwargs):
        if path.suffix == ".db" and path.name != "seed.db":
            raise PermissionError("simulated locked candidate")
        return original_unlink(path, *args, **kwargs)

    task = None
    try:
        with monkeypatch.context() as patch:
            patch.setattr(os, "rename", pause_rename)
            patch.setattr(Path, "unlink", locked_unlink)
            task = asyncio.create_task(
                root.clone(str(uuid4()), 1, operation_id=operation_id)
            )
            assert await asyncio.to_thread(renamed.wait, 5)
            task.cancel()
            resume.set()
            with pytest.raises(asyncio.CancelledError):
                await task
        assert root.pending_operations == {operation_id}
        assert len(list(root.path.glob("*.db"))) == 2
        assert (await root.cleanup_pending())["pending"] == 0
        assert list(root.path.glob("*.db")) == [root.path / "seed.db"]
    finally:
        resume.set()
        if task is not None:
            await asyncio.gather(task, return_exceptions=True)
        root.close()
