"""Health check and metrics endpoints for monitoring."""

import hashlib
import logging
import sqlite3
import time
from contextlib import closing

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session
from src.config import config
from src.demo.database import DemoError
from src.models import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Health"])

# Track application start time for uptime calculation
START_TIME = time.time()


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


async def demo_health_check(request: Request) -> JSONResponse:
    """§4: visit-free readiness; no cookie, visitor DB or normal DB."""
    ready = False
    try:
        visitors = request.app.state.demo_request_db.visitors
        seed = request.app.state.demo_seed
        # Revalidates the root identity, marker and exclusive lock file.
        visitors.root.bytes_used()
        if (
            hashlib.sha256(seed.manifest_path.read_bytes()).hexdigest()
            != visitors.root.seed_manifest_sha256
        ):
            raise DemoError("VERSION_MISMATCH")
        with closing(
            sqlite3.connect(
                seed.path.as_uri() + "?mode=ro", uri=True, timeout=1
            )
        ) as db:
            db.execute("SELECT 1").fetchone()
        # The visitor cap alone keeps readiness; the cleanup hard cap does not.
        ready = (
            not request.app.state.demo_cleanup.done()
            and visitors.pending_cleanup_count
            < visitors.settings.max_retired_generations
        )
    except Exception as error:
        logger.error(
            "Demo health check failure",
            extra={"code": getattr(error, "code", type(error).__name__)},
        )
    return JSONResponse(
        {
            "status": "healthy" if ready else "degraded",
            "database": "healthy" if ready else "unhealthy",
            "uptime_seconds": round(time.time() - START_TIME, 2),
        },
        200 if ready else 503,
        {"Cache-Control": "no-store"},
    )


router.get("/health")(
    demo_health_check if config.APP_MODE == "demo" else health_check
)


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
            "uptime_seconds": round(time.time() - START_TIME, 2),
        }
