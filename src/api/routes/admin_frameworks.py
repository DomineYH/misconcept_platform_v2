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
from src.models.session import Session
from src.models.user import User

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

    framework = await db.get(AnalysisFramework, framework_id)
    if not framework:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="프레임워크를 찾을 수 없습니다",
        )

    # Check for duplicate name if updating name
    if framework_data.name and framework_data.name != framework.name:
        existing_query = select(AnalysisFramework).where(
            AnalysisFramework.name == framework_data.name
        )
        existing = await db.scalar(existing_query)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f"프레임워크 '{framework_data.name}'"
                    "이(가) 이미 존재합니다"
                ),
            )

    # Update fields
    if framework_data.name:
        framework.name = framework_data.name
    if framework_data.description:
        framework.description = framework_data.description
    # category_name: only set if explicitly present in the payload.
    # Distinguishes "omitted" (preserve) from "explicit null" (clear).
    if "category_name" in framework_data.model_fields_set:
        framework.category_name = framework_data.category_name
    if framework_data.labels:
        try:
            framework.labels_json = json.dumps(
                [
                    {
                        "name": item.name,
                        "criteria": item.criteria,
                        "level": item.level,
                    }
                    for item in framework_data.labels
                ],
                ensure_ascii=False,
            )
        except ValueError as e:
            raise HTTPException(
                status_code=(status.HTTP_422_UNPROCESSABLE_ENTITY),
                detail=[
                    {
                        "loc": ["body", "labels"],
                        "msg": str(e),
                        "type": "value_error",
                    }
                ],
            )

    await db.flush()
    await db.refresh(framework)

    logger.info(f"Framework updated: id={framework_id}")

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

    framework = await db.get(AnalysisFramework, framework_id)
    if not framework:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="프레임워크를 찾을 수 없습니다",
        )

    # Check if framework is in use
    usage_query = (
        select(func.count(Scenario.id))
        .where(Scenario.framework_id == framework_id)
        .where(Scenario.deleted_at.is_(None))
    )
    usage_count = await db.scalar(usage_query)

    if usage_count and usage_count > 0:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                f"삭제할 수 없습니다: {usage_count}개의 "
                "시나리오가 이 프레임워크를 사용 중입니다"
            ),
        )

    # Hard-delete soft-deleted scenarios
    soft_deleted_query = select(Scenario).where(
        Scenario.framework_id == framework_id,
        Scenario.deleted_at.is_not(None),
    )
    soft_deleted_result = await db.execute(soft_deleted_query)
    soft_deleted_scenarios = soft_deleted_result.scalars().all()

    for scenario in soft_deleted_scenarios:
        sessions_query = select(Session).where(
            Session.scenario_id == scenario.id
        )
        sessions_result = await db.execute(sessions_query)
        sessions = sessions_result.scalars().all()
        for session in sessions:
            await db.delete(session)

    await db.flush()

    for scenario in soft_deleted_scenarios:
        await db.delete(scenario)

    await db.flush()

    await db.delete(framework)
    await db.flush()

    logger.info(
        f"Framework deleted: id={framework_id}, " f"name={framework.name}"
    )

    return {
        "status": "deleted",
        "framework_id": framework_id,
    }
