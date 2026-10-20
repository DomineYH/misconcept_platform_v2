"""Admission, then the visitor write guard, for every reservation (review r205).

Real #191 visitor authority and #192 lifecycle; the reservations run their
real lock prologue and fail in the DB body (missing session), which is enough
to prove neither one waits on the other.
"""

import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from fastapi import HTTPException
from test_demo_d1_01_database import make_root
from test_demo_d1_01_visitors import ready_context

from src.demo.lifecycle import Lifecycle
from src.demo.visitors import VisitorRegistry
from src.services.analysis_runs import reserve_analysis
from src.services.call_admission import execution_lock
from src.services.generation_runs import DemoBinding, reserve_student

MISSING_SESSION = 999_999


@pytest.fixture
async def lifecycle(tmp_path):
    root = await make_root(tmp_path)
    registry = Lifecycle()
    registry.visitors = VisitorRegistry(root, lifecycle=registry)
    yield registry
    await registry.visitors.shutdown()
    root.close()


async def queued(lock, count):
    # Yield until `count` tasks wait on the lock; no wall-clock sleeps.
    for _ in range(1000):
        if len(lock._waiters or ()) >= count:
            return
        await asyncio.sleep(0)
    raise AssertionError("reservations never queued on admission")


async def test_student_and_analysis_behind_admission_both_finish(lifecycle):
    _, _, ctx = await ready_context(lifecycle.visitors, "teacher")
    binding = DemoBinding(ctx, lifecycle, None, {}, None)
    user = SimpleNamespace(id=2, is_admin=False)
    request = SimpleNamespace(
        request_id=str(uuid4()), content="허구 질문", turn_id=None
    )
    admission = execution_lock()
    await admission.acquire()
    try:
        student = asyncio.create_task(
            reserve_student(
                ctx.factory, MISSING_SESSION, user, request, demo=binding
            )
        )
        await queued(admission, 1)
        analysis = asyncio.create_task(
            reserve_analysis(
                ctx.factory,
                MISSING_SESSION,
                user.id,
                str(uuid4()),
                ctx=ctx,
                registry=lifecycle,
            )
        )
        await queued(admission, 2)
    finally:
        admission.release()
    done, pending = await asyncio.wait({student, analysis}, timeout=5)
    for task in pending:
        task.cancel()
    await asyncio.gather(*pending, return_exceptions=True)
    assert not pending, "student/analysis reservations deadlocked"
    for task in done:
        assert isinstance(task.exception(), HTTPException)
        assert task.exception().status_code == 404
