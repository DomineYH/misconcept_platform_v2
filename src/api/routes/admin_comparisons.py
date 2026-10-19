"""Owner-only comparisons, independent stops and saved-state recovery."""

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse, Response
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes.admin_providers import (
    demo_missing,
    demo_read_only,
    execution_registry,
    execution_scope,
    request_factory,
)
from src.api.routes.comparison_input import ComparisonRoute
from src.api.schemas.comparison import (
    ComparisonRequest,
    ComparisonRetry,
    ComparisonStop,
    ComparisonStopGeneration,
    ComparisonStopTarget,
)
from src.api.schemas.scenario_config import ResolvedModelConfig
from src.config import config
from src.models import User
from src.services.comparison_history import delete_comparison, list_comparisons
from src.services.comparison_runs import reserve_comparison
from src.services.comparison_state import (
    comparison_busy,
    comparison_snapshot,
    find_panel,
    owned_comparison,
)
from src.services.comparison_stop import stop_comparison
from src.services.comparison_stream import ComparisonStreamingResponse
from src.services.invocation_types import InvocationError
from src.services.lesson_connections import resolve_frozen_model

PanelId = Annotated[
    str, Path(min_length=1, max_length=36, pattern=r"^[A-Za-z0-9_-]+$")
]


async def comparison_factory(db):
    if config.APP_MODE == "demo":
        return request_factory(db)
    factory = async_sessionmaker(
        db.bind, expire_on_commit=False, autoflush=False
    )
    await db.close()
    return factory


class DemoComparisonRoute(ComparisonRoute):
    """§20: refuse forbidden demo writes before the bounded body reader."""

    def get_route_handler(self):
        handler = super().get_route_handler()
        if not any(
            dependency.call is demo_read_only
            for dependency in self.dependant.dependencies
        ):
            return handler

        async def refuse(request):
            if config.APP_MODE != "demo":
                return await handler(request)
            # Demo sessions need no cleanup here; the request boundary owns it.
            db = await anext(get_db_session(request))
            await demo_read_only(await get_admin_user(request, db))

        return refuse


router = APIRouter(tags=["Admin Comparisons"], route_class=DemoComparisonRoute)


@router.get("/admin/comparisons")
async def comparison_history(
    cursor: Annotated[str | None, Query(max_length=100)] = None,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    return JSONResponse(
        await list_comparisons(db, user.id, cursor),
        headers={"Cache-Control": "no-store"},
    )


@router.get("/admin/comparisons/{comparison_id}/panels/{panel_id}/model")
async def comparison_model(
    comparison_id: UUID,
    panel_id: PanelId,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    with demo_missing():
        comparison = await owned_comparison(db, str(comparison_id), user.id)
    snapshot = await comparison_snapshot(db, comparison)
    if comparison_busy(snapshot):
        raise HTTPException(409, detail={"code": "comparison_busy"})
    with demo_missing():
        panel = find_panel(snapshot, panel_id)
    try:
        selection = ResolvedModelConfig.model_validate(panel["model_config"])
        await resolve_frozen_model(
            db, selection, comparison.target_role, app_mode="normal"
        )
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
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    if (
        body.source_snapshot.source_kind != "unsaved_draft"
        or body.mentor_trigger is not None
    ):
        raise HTTPException(422, detail={"code": "invalid_source"})
    factory = await comparison_factory(db)
    snapshot, execution = await reserve_comparison(factory, user, body)
    if execution is None:
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})
    return ComparisonStreamingResponse(factory, snapshot, execution)


@router.post("/admin/comparisons/{comparison_id}/panels/{panel_id}/retry")
async def retry_panel(
    comparison_id: UUID,
    panel_id: PanelId,
    body: ComparisonRetry,
    user: User = Depends(demo_read_only),
    db: AsyncSession = Depends(get_db_session),
):
    factory = await comparison_factory(db)
    snapshot, execution = await reserve_comparison(
        factory, user, body, comparison_id=str(comparison_id), panel_id=panel_id
    )
    if execution is None:
        return JSONResponse(snapshot, headers={"Cache-Control": "no-store"})
    return ComparisonStreamingResponse(factory, snapshot, execution)


@router.get("/admin/comparisons/{comparison_id}")
async def get_comparison(
    comparison_id: UUID,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    with demo_missing():
        comparison = await owned_comparison(db, str(comparison_id), user.id)
    return JSONResponse(
        await comparison_snapshot(db, comparison),
        headers={"Cache-Control": "no-store"},
    )


@router.delete("/admin/comparisons/{comparison_id}", status_code=204)
async def remove_comparison(
    request: Request,
    comparison_id: UUID,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    with demo_missing():
        await delete_comparison(
            db,
            str(comparison_id),
            user.id,
            scope=execution_scope(request),
            registry=execution_registry(request),
        )
    return Response(status_code=204, headers={"Cache-Control": "no-store"})


@router.post("/admin/comparisons/{comparison_id}/panels/{panel_id}/stop")
async def stop_panel(
    request: Request,
    comparison_id: UUID,
    panel_id: PanelId,
    body: ComparisonStopGeneration,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    factory = await comparison_factory(db)
    with demo_missing():
        result = await stop_comparison(
            factory,
            str(comparison_id),
            user.id,
            [
                ComparisonStopTarget(
                    panel_id=panel_id, generation_id=body.generation_id
                )
            ],
            scope=execution_scope(request),
            registry=execution_registry(request),
        )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})


@router.post("/admin/comparisons/{comparison_id}/stop")
async def stop_all(
    request: Request,
    comparison_id: UUID,
    body: ComparisonStop,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    factory = await comparison_factory(db)
    with demo_missing():
        result = await stop_comparison(
            factory,
            str(comparison_id),
            user.id,
            body.generations,
            all_panels=True,
            scope=execution_scope(request),
            registry=execution_registry(request),
        )
    return JSONResponse(result, headers={"Cache-Control": "no-store"})
