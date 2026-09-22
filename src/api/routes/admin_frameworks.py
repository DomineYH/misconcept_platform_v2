"""Admin framework management routes with web UI."""

import json
import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
    status,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import (
    get_admin_user,
    get_db_session,
    templates,
)
from src.api.schemas import (
    AdminFrameworkResponse,
    FrameworkCreateWeb,
    FrameworkUpdateWeb,
)
from src.models.analysis_framework import AnalysisFramework
from src.models.scenario import Scenario
from src.models.user import User
from src.services.admin_framework_ops import update_framework_record, delete_framework_record

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Frameworks"])


@router.get("/admin/frameworks", response_class=HTMLResponse)
async def list_all_frameworks_web(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """GET /admin/frameworks - Framework management page."""

    query = select(AnalysisFramework).order_by(AnalysisFramework.id.desc())
    result = await db.execute(query)
    frameworks = result.scalars().all()

    framework_usage = {}
    for fw in frameworks:
        usage_query = (
            select(func.count(Scenario.id))
            .where(Scenario.framework_id == fw.id)
            .where(Scenario.deleted_at.is_(None))
        )
        count = await db.scalar(usage_query)
        framework_usage[fw.id] = count or 0

    return templates.TemplateResponse(
        "admin/frameworks.html",
        {
            "request": request,
            "user": user,
            "frameworks": frameworks,
            "framework_usage": framework_usage,
        },
    )


@router.post(
    "/admin/frameworks",
    response_model=AdminFrameworkResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_framework_web(
    framework_data: FrameworkCreateWeb,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/frameworks - Create new framework."""

    # Check for duplicate name
    existing_query = select(AnalysisFramework).where(
        AnalysisFramework.name == framework_data.name
    )
    existing = await db.scalar(existing_query)
    if existing:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"프레임워크 '{framework_data.name}'" "이(가) 이미 존재합니다"
            ),
        )

    # Create framework with try/except for ORM validation
    try:
        new_framework = AnalysisFramework(
            name=framework_data.name,
            description=framework_data.description,
            category_name=framework_data.category_name,
            labels_json=json.dumps(
                [
                    {
                        "name": item.name,
                        "criteria": item.criteria,
                        "level": item.level,
                    }
                    for item in framework_data.labels
                ],
                ensure_ascii=False,
            ),
        )
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=[
                {
                    "loc": ["body", "labels"],
                    "msg": str(e),
                    "type": "value_error",
                }
            ],
        )

    db.add(new_framework)
    await db.flush()
    await db.refresh(new_framework)

    logger.info(
        f"Framework created: id={new_framework.id}, "
        f"name={new_framework.name}"
    )

    return new_framework


@router.post(
    "/admin/frameworks/{framework_id}/update",
    response_model=AdminFrameworkResponse,
)
async def update_framework_web(
    framework_id: int,
    framework_data: FrameworkUpdateWeb,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/frameworks/{id}/update - Update framework."""

    framework = await update_framework_record(db, framework_id, framework_data)
    logger.info("Framework updated: id=%d", framework_id)
    return framework


@router.post(
    "/admin/frameworks/{framework_id}/delete",
)
async def delete_framework_web(
    framework_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/frameworks/{id}/delete - Delete framework."""

    await delete_framework_record(db, framework_id)
    logger.info("Framework deleted: id=%d", framework_id)
    return {"status": "deleted", "framework_id": framework_id}
