"""Admin API usage routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.models.api_usage import ApiUsageLog
from src.models.user import User

router = APIRouter()


@router.get("/admin/api-usage", response_class=HTMLResponse)
async def api_usage_dashboard(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Admin dashboard for API usage stats."""

    # Get recent logs
    query = select(ApiUsageLog).order_by(desc(ApiUsageLog.timestamp)).limit(100)
    result = await db.execute(query)
    logs = result.scalars().all()

    # Preserve recorded generation estimates; list calls are not generation.
    total_cost_query = select(func.sum(ApiUsageLog.estimated_cost_usd)).where(
        ApiUsageLog.operation.is_(None)
        | (ApiUsageLog.operation != "model_list")
    )
    total_cost = await db.scalar(total_cost_query) or 0.0

    return templates.TemplateResponse(
        "admin/api_usage.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "total_cost": total_cost,
            # ponytail: legacy rows lack attempt metadata; A10 wires ledger counts.
            "unpriced_generation_attempts": None,
            "model_list_calls": None,
        },
    )
