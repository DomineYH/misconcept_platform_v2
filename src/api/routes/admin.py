"""Admin dashboard and router aggregation (T076)."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse

from src.api.dependencies import get_current_user, templates
from src.api.routes.admin_about import router as about_router
from src.api.routes.admin_frameworks import router as frameworks_router
from src.api.routes.admin_groups import router as groups_router
from src.api.routes.admin_scenarios import router as scenarios_router
from src.api.routes.admin_sessions import router as sessions_router
from src.api.routes.admin_users import router as users_router
from src.models.user import User

router = APIRouter(tags=["Admin"])

router.include_router(scenarios_router)
router.include_router(frameworks_router)
router.include_router(sessions_router)
router.include_router(users_router)
router.include_router(groups_router)
router.include_router(about_router)


@router.get("/admin", response_class=HTMLResponse)
async def admin_dashboard(
    request: Request,
    user: User = Depends(get_current_user),
):
    """GET /admin - Admin dashboard with quick actions only."""
    if user.role != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin role required",
        )

    return templates.TemplateResponse(
        "admin/dashboard.html",
        {"request": request, "user": user},
    )
