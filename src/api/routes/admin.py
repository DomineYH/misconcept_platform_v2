"""Admin dashboard and router aggregation (T076)."""

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse

from src.api.dependencies import get_admin_user, get_current_user, templates
from src.api.routes.admin_about import router as about_router
from src.api.routes.admin_ai_settings import router as ai_settings_router
from src.api.routes.admin_catalog import router as catalog_router
from src.api.routes.admin_groups import router as groups_router
from src.api.routes.admin_models import router as models_router
from src.api.routes.admin_probes import router as probes_router
from src.api.routes.admin_providers import router as providers_router
from src.api.routes.admin_scenarios import router as scenarios_router
from src.api.routes.admin_sessions import router as sessions_router
from src.api.routes.admin_users import router as users_router
from src.models.user import User

router = APIRouter(tags=["Admin"])

router.include_router(scenarios_router)
router.include_router(sessions_router)
router.include_router(users_router)
router.include_router(groups_router)
router.include_router(about_router)
router.include_router(providers_router)
router.include_router(models_router)
router.include_router(ai_settings_router)
router.include_router(catalog_router)
router.include_router(probes_router)


@router.get("/admin/ai", response_class=HTMLResponse)
async def ai_connections_page(
    request: Request, user: User = Depends(get_admin_user)
):
    """Render AI management; data APIs are supplied by subsequent S1 tickets."""
    return templates.TemplateResponse(
        "admin/ai.html", {"request": request, "user": user}
    )


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
