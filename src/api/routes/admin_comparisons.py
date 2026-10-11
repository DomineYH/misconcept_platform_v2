"""Owner-only student single-panel comparisons and saved-state recovery."""

from uuid import UUID

from fastapi import APIRouter, Depends
from fastapi.responses import JSONResponse
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from src.api.dependencies import get_admin_user, get_db_session
from src.api.routes.comparison_input import ComparisonRoute
from src.api.schemas.comparison import ComparisonRequest
from src.models import User
from src.services.comparison_runs import reserve_comparison
from src.services.comparison_state import comparison_snapshot, owned_comparison
from src.services.comparison_stream import comparison_response

router = APIRouter(tags=["Admin Comparisons"], route_class=ComparisonRoute)


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
