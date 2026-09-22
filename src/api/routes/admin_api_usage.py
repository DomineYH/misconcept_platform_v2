"""Admin API usage routes."""
from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import HTMLResponse
from sqlalchemy import select, desc, func
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.models.user import User
from src.models.api_usage import ApiUsageLog

router = APIRouter()


@router.get("/admin/api-usage", response_class=HTMLResponse)
async def api_usage_dashboard(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Admin dashboard for API usage stats."""

    # Get recent logs
    query = (
        select(ApiUsageLog).order_by(desc(ApiUsageLog.timestamp)).limit(100)
    )
    result = await db.execute(query)
    logs = result.scalars().all()

    # Calculate total cost
    total_cost_query = select(func.sum(ApiUsageLog.estimated_cost_usd))
    total_cost = await db.scalar(total_cost_query) or 0.0

    return templates.TemplateResponse(
        "admin/api_usage.html",
        {
            "request": request,
            "user": user,
            "logs": logs,
            "total_cost": round(total_cost, 4),
        },
    )

