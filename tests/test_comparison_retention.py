"""Retention CLI at the process boundary against temporary SQLite."""

import asyncio
import json
import sys
from datetime import timedelta
from uuid import UUID, uuid4

from sqlalchemy import select
from test_comparison_student import (
    client,
    comparison,
    scenario_payload,
    student,
)
from test_scenario_api import login
from test_student_generation import frames

from src.models import ApiUsageLog, ComparisonRun, GenerationRun
from src.services.comparison_state import utcnow

__all__ = ["client", "comparison", "scenario_payload", "student"]


async def cleanup(data, mode, *, database=None):
    process = await asyncio.create_subprocess_exec(
        sys.executable,
        "-m",
        "src.cli.cleanup_comparisons",
        "--database",
        str(database or data.engine.url.database),
        mode,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await process.communicate()
    return process.returncode, stdout.decode(), stderr.decode()


async def test_cleanup_dry_run_execute_batches_and_repeat_preserve_unknown_usage(
    data, client, student, comparison
):
    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    async with data.factory() as db:
        original = await db.get(ComparisonRun, accepted["comparison_run_id"])
        original.expires_at = utcnow() - timedelta(seconds=1)
        values = {
            column.name: getattr(original, column.name)
            for column in ComparisonRun.__table__.columns
        }
        for index in range(100):
            db.add(
                ComparisonRun(
                    **{
                        **values,
                        "id": str(UUID(int=index + 1)),
                        "request_id": str(uuid4()),
                    }
                )
            )
        running_id = str(uuid4())
        db.add(
            ComparisonRun(
                **{
                    **values,
                    "id": running_id,
                    "request_id": str(uuid4()),
                    "status": "running",
                }
            )
        )
        usage = (await db.scalars(select(ApiUsageLog))).one()
        usage.input_tokens = usage.output_tokens = usage.estimated_cost_usd = (
            None
        )
        await db.commit()
        before = {
            column.name: getattr(usage, column.name)
            for column in ApiUsageLog.__table__.columns
        }
    code, output, errors = await cleanup(data, "--dry-run")
    assert code == 0, errors
    summary = json.loads(output)
    assert (
        summary["targets"] == 101
        and summary["deleted"] == 0
        and summary["failed"] == 0
    )
    assert summary["skipped_running"] == 1
    async with data.factory() as db:
        assert (
            await db.get(ComparisonRun, accepted["comparison_run_id"])
            is not None
        )
        assert (await db.scalars(select(ApiUsageLog))).one().run_id == accepted[
            "run_id"
        ]
    code, output, errors = await cleanup(data, "--execute")
    assert code == 0, errors
    assert json.loads(output)["deleted"] == 101
    assert "deleted=100" in errors and "deleted=1" in errors
    assert (
        "Current question" not in output + errors
        and "PRIVATE" not in output + errors
    )
    async with data.factory() as db:
        assert (await db.scalars(select(ComparisonRun))).one().id == running_id
        assert await db.get(GenerationRun, accepted["run_id"]) is None
        usage = (await db.scalars(select(ApiUsageLog))).one()
        assert {
            column.name: getattr(usage, column.name)
            for column in ApiUsageLog.__table__.columns
        } == {**before, "run_id": None}
        assert usage.usage_scope == "comparison" and usage.status == "completed"
    code, output, errors = await cleanup(data, "--execute")
    assert code == 0 and json.loads(output)["deleted"] == 0


async def test_cleanup_failure_rolls_back_usage_detachment_and_can_rerun(
    data, client, student, comparison
):
    from sqlalchemy import text

    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    async with data.factory() as db:
        record = await db.get(ComparisonRun, accepted["comparison_run_id"])
        record.expires_at = utcnow() - timedelta(seconds=1)
        await db.execute(
            text(
                "CREATE TRIGGER reject_cleanup BEFORE DELETE ON comparison_run BEGIN SELECT RAISE(ABORT, 'PRIVATE_CONTENT'); END"
            )
        )
        await db.commit()
    code, output, errors = await cleanup(data, "--execute")
    assert code == 1
    assert (
        json.loads(output)["failed"] == 1 and json.loads(output)["deleted"] == 0
    )
    assert "PRIVATE_CONTENT" not in output + errors
    async with data.factory() as db:
        assert (
            await db.get(ComparisonRun, accepted["comparison_run_id"])
            is not None
        )
        assert (
            await db.get(GenerationRun, accepted["run_id"])
        ).outcome_json is not None
        assert (await db.scalars(select(ApiUsageLog))).one().run_id == accepted[
            "run_id"
        ]
        await db.execute(text("DROP TRIGGER reject_cleanup"))
        await db.commit()
    code, output, errors = await cleanup(data, "--execute")
    assert code == 0 and json.loads(output)["deleted"] == 1


async def test_cleanup_ownerless_candidates_skip_live_generations_and_usage(
    data, client, student, comparison
):
    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    async with data.factory() as db:
        record = await db.get(ComparisonRun, accepted["comparison_run_id"])
        record.admin_id = None
        run = await db.get(GenerationRun, accepted["run_id"])
        run.status = "running"
        await db.commit()
    code, output, errors = await cleanup(data, "--execute")
    assert code == 0 and json.loads(output)["targets"] == 0
    assert json.loads(output)["skipped_running"] == 1
    async with data.factory() as db:
        run = await db.get(GenerationRun, accepted["run_id"])
        run.status = "completed"
        usage = (await db.scalars(select(ApiUsageLog))).one()
        usage.status = "running"
        await db.commit()
    code, output, errors = await cleanup(data, "--execute")
    assert code == 0 and json.loads(output)["deleted"] == 0
    assert json.loads(output)["skipped_running"] == 1
    async with data.factory() as db:
        usage = (await db.scalars(select(ApiUsageLog))).one()
        usage.status = "interrupted"
        await db.commit()
    code, output, errors = await cleanup(data, "--execute")
    assert code == 0 and json.loads(output)["deleted"] == 1


async def test_orphan_recovery_finishes_comparison_usage_and_allows_ownerless_cleanup(
    data, client, student, comparison
):
    from src.services.generation_lifecycle import interrupt_orphans

    login(client, data.admin)
    result = await client.post("/admin/comparisons", json=comparison)
    accepted = frames(result)[0][1]
    async with data.factory() as db:
        record = await db.get(ComparisonRun, accepted["comparison_run_id"])
        record.admin_id, record.status = None, "running"
        run = await db.get(GenerationRun, accepted["run_id"])
        run.status = "running"
        usage = (await db.scalars(select(ApiUsageLog))).one()
        usage.status, usage.finished_at = "running", None
        cost = usage.estimated_cost_usd
        await db.commit()
    await interrupt_orphans(data.factory)
    code, output, errors = await cleanup(data, "--execute")
    assert code == 0 and json.loads(output)["deleted"] == 1
    async with data.factory() as db:
        usage = (await db.scalars(select(ApiUsageLog))).one()
        assert usage.status == "interrupted" and usage.finished_at is not None
        assert usage.run_id is None and usage.usage_scope == "comparison"
        assert usage.estimated_cost_usd == cost and usage.input_tokens == 10
    student.responses.create.assert_awaited_once()


async def test_owner_account_deletion_cancels_call_cleanup_ignores_late_completion(
    data, client, student, comparison
):
    from src.services.call_admission import active_calls
    from src.services.comparison_runs import finish_comparison
    from src.services.generation_lifecycle import active_runs

    data.other.role = "admin"
    await data.db.commit()
    login(client, data.admin)
    gate = asyncio.Event()
    student.stream.events.insert(0, gate)
    task = asyncio.create_task(
        client.post("/admin/comparisons", json=comparison)
    )
    try:
        await asyncio.wait_for(student.stream.read_started.wait(), 10)
        login(client, data.other)
        response = await client.post(f"/admin/users/{data.admin.id}/delete")
        assert response.status_code == 200, response.text
        result = await asyncio.wait_for(task, 3)
    finally:
        gate.set()
        if not task.done():
            await task
    events = frames(result)
    assert events[-1][0] == "run.interrupted"
    assert not active_calls and not active_runs
    accepted = events[0][1]
    login(client, data.admin)
    assert (await client.get("/admin/comparisons")).status_code == 401
    login(client, data.other)
    assert (
        await client.get(f"/admin/comparisons/{accepted['comparison_run_id']}")
    ).status_code == 404
    code, output, errors = await cleanup(data, "--execute")
    assert code == 0 and json.loads(output)["deleted"] == 1
    assert (
        await finish_comparison(
            data.factory,
            accepted["run_id"],
            status="completed",
            content="PRIVATE LATE",
        )
        is None
    )
    async with data.factory() as db:
        usage = (await db.scalars(select(ApiUsageLog))).one()
        assert usage.owner_id is None and usage.run_id is None
        assert usage.usage_scope == "comparison" and usage.status != "running"


async def test_cleanup_missing_database_is_not_created_and_bad_arguments_exit_two(
    data, tmp_path
):
    missing = tmp_path / "missing.db"
    for mode in ("--dry-run", "--execute"):
        code, output, errors = await cleanup(data, mode, database=missing)
        assert code == 1 and json.loads(output)["failed"] == 1
        assert not missing.exists()
        assert "Traceback" not in errors
    code, output, errors = await cleanup(data, "--unknown")
    assert code == 2 and output == ""
