"""Admin API usage routes."""

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import case, desc, func, select
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
    generation = ApiUsageLog.operation.is_(None) | (
        ApiUsageLog.operation != "model_list"
    )
    summary = (
        await db.execute(
            select(
                func.sum(case((generation, ApiUsageLog.estimated_cost_usd))),
                func.count(
                    case(
                        (
                            generation
                            & ApiUsageLog.invocation_id.is_not(None)
                            & ApiUsageLog.estimated_cost_usd.is_(None),
                            1,
                        )
                    )
                ),
                func.count(case((ApiUsageLog.operation == "model_list", 1))),
                func.count(case((ApiUsageLog.invocation_id.is_(None), 1))),
            )
        )
    ).one()

    return templates.TemplateResponse(
        "admin/api_usage.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "total_cost": summary[0] or 0.0,
            "unpriced_generation_attempts": summary[1],
            "model_list_calls": summary[2],
            "has_legacy_records": summary[3] > 0,
        },
    )
