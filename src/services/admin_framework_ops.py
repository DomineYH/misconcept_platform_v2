"""Shared admin framework operations."""

import json

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.schemas import FrameworkUpdateWeb
from src.models.analysis_framework import AnalysisFramework
from src.models.scenario import Scenario
from src.models.session import Session


async def update_framework_record(
    db: AsyncSession,
    framework_id: int,
    framework_data: FrameworkUpdateWeb,
) -> AnalysisFramework:
    """Update a framework."""
    framework = await db.get(AnalysisFramework, framework_id)
    if not framework:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="프레임워크를 찾을 수 없습니다",
        )

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
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
                detail=[
                    {
                        "loc": ["body", "labels"],
                        "msg": str(exc),
                        "type": "value_error",
                    }
                ],
            )

    await db.flush()
    await db.refresh(framework)
    return framework


async def delete_framework_record(
    db: AsyncSession,
    framework_id: int,
) -> None:
    """Delete a framework when it is not in use."""
    framework = await db.get(AnalysisFramework, framework_id)
    if not framework:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="프레임워크를 찾을 수 없습니다",
        )

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
