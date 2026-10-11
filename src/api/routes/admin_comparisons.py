"""Owner-only comparisons, independent stops and saved-state recovery."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes.comparison_input import ComparisonRoute
from src.api.schemas.comparison import (
    ComparisonRequest,
    ComparisonRetry,
    ComparisonStop,
    ComparisonStopGeneration,
    ComparisonStopTarget,
)
from src.api.schemas.scenario_config import ResolvedModelConfig
from src.models import User
from src.services.comparison_runs import reserve_comparison
from src.services.comparison_state import comparison_snapshot, owned_comparison
from src.services.comparison_stop import stop_comparison
from src.services.comparison_stream import comparison_response
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model

router = APIRouter(tags=["Admin Comparisons"], route_class=ComparisonRoute)


@router.get("/admin/comparisons/{comparison_id}/panels/{panel_id}/model")
async def comparison_model(
    comparison_id: UUID,
    panel_id: Annotated[
        str, Path(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$")
    ],
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    comparison = await owned_comparison(db, str(comparison_id), user.id)
    snapshot = await comparison_snapshot(db, comparison)
    if comparison.status == "running" or any(
        panel["status"] == "running" for panel in snapshot["panels"]
    ):
        raise HTTPException(409, detail={"code": "comparison_busy"})
    panel = next(
        (p for p in snapshot["panels"] if p["panel_id"] == panel_id), None
    )
    if panel is None:
        raise HTTPException(404, detail={"code": "panel_not_found"})
    try:
        selection = ResolvedModelConfig.model_validate(panel["model_config"])
        await resolve_frozen_model(db, selection, comparison.target_role)
    except (InvocationError, ValueError):
        raise HTTPException(
            422, detail={"code": "configuration_unavailable"}
        ) from None
    return JSONResponse(
        dict(
            target_role=comparison.target_role,
            model_config=selection.model_dump(),
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/comparisons")
async def start_comparison(
    body: ComparisonRequest,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    factory = async_sessionmaker(
        db.bind, expire_on_commit=False, autoflush=False
    )
    await db.close()
    snapshot, execution = await reserve_comparison(factory, user, body)
    if execution is None:
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})
    return comparison_response(factory, snapshot, execution)


@router.post("/admin/comparisons/{comparison_id}/panels/{panel_id}/retry")
async def retry_panel(
    comparison_id: UUID,
    panel_id: Annotated[
        str, Path(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$")
    ],
    body: ComparisonRetry,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    factory = async_sessionmaker(
        db.bind, expire_on_commit=False, autoflush=False
    )
    await db.close()
    snapshot, execution = await reserve_comparison(
        factory, user, body, comparison_id=str(comparison_id), panel_id=panel_id
    )
    if execution is None:
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})
    return comparison_response(factory, snapshot, execution)


@router.get("/admin/comparisons/{comparison_id}")
async def get_comparison(
    comparison_id: UUID,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    comparison = await owned_comparison(db, str(comparison_id), user.id)
    return JSONResponse(
        await comparison_snapshot(db, comparison),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/comparisons/{comparison_id}/panels/{panel_id}/stop")
async def stop_panel(
    comparison_id: UUID,
    panel_id: Annotated[
        str, Path(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$")
    ],
    body: ComparisonStopGeneration,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    factory = async_sessionmaker(
        db.bind, expire_on_commit=False, autoflush=False
    )
    await db.close()
    return JSONResponse(
        await stop_comparison(
            factory,
            str(comparison_id),
            user.id,
            [
                ComparisonStopTarget(
                    panel_id=panel_id, generation_id=body.generation_id
                )
            ],
        ),
        headers={"Cache-Control": "no-store"},
    )


@router.post("/admin/comparisons/{comparison_id}/stop")
async def stop_all(
    comparison_id: UUID,
    body: ComparisonStop,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    factory = async_sessionmaker(
        db.bind, expire_on_commit=False, autoflush=False
    )
    await db.close()
    return JSONResponse(
        await stop_comparison(
            factory,
            str(comparison_id),
            user.id,
            body.generations,
            all_panels=True,
        ),
        headers={"Cache-Control": "no-store"},
    )
