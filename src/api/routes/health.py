"""Health check and metrics endpoints for monitoring."""

import logging
import time

from fastapi import APIRouter, Depends
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session
from src.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])

# Track application start time for uptime calculation
START_TIME = time.time()


@router.get("/health")
async def health_check(db: AsyncSession = Depends(get_db_session)) -> dict:
    """
    Health check endpoint for monitoring.

    Returns:
        dict: Status of application and database connectivity
    """
    try:
        # Check database connectivity
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception as e:
        logger.error("Health check DB failure: %s", e)
        db_status = "unhealthy"

    return {
        "status": "healthy" if db_status == "healthy" else "degraded",
        "database": db_status,
        "uptime_seconds": round(time.time() - START_TIME, 2),
    }


@router.get("/metrics")
async def get_metrics(
    admin: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
) -> dict:
    """
    Metrics endpoint for observability.

    Returns:
        dict: Application metrics including counts and performance data
    """
    try:
        # Get total counts from database
        user_count_result = await db.execute(text("SELECT COUNT(*) FROM user"))
        user_count = user_count_result.scalar()

        session_count_result = await db.execute(
            text("SELECT COUNT(*) FROM session")
        )
        session_count = session_count_result.scalar()

        message_count_result = await db.execute(
            text("SELECT COUNT(*) FROM message")
        )
        message_count = message_count_result.scalar()

        # Get active sessions (not ended)
        active_session_result = await db.execute(
            text("SELECT COUNT(*) FROM session WHERE ended_at IS NULL")
        )
        active_sessions = active_session_result.scalar()

        return {
            "uptime_seconds": round(time.time() - START_TIME, 2),
            "database": {
                "users": user_count,
                "sessions": {
                    "total": session_count,
                    "active": active_sessions,
                    "completed": session_count - active_sessions,
                },
                "messages": message_count,
            },
        }

    except Exception as e:
        logger.error("Metrics retrieval failure: %s", e)
        return {
            "error": "Failed to retrieve metrics",
            "uptime_seconds": round(
                time.time() - START_TIME, 2
            ),
        }
