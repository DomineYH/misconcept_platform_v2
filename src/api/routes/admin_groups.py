"""Admin group management routes."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    HTTPException,
    Request,
)
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.schemas import (
    AdminGroupResponse,
    GroupCreate,
    GroupUpdate,
)
from src.models.scenario_group import ScenarioGroup
from src.models.user import User
from src.models.user_group import UserGroup

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Groups"])


@router.get("/admin/groups", response_class=HTMLResponse)
async def list_groups(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """GET /admin/groups - Group management page."""

    result = await db.execute(select(UserGroup).order_by(UserGroup.name))
    groups = result.scalars().all()

    # Get counts for each group
    groups_data = []
    for group in groups:
        member_count = await db.scalar(
            select(func.count(User.id)).where(User.group_id == group.id)
        )
        scenario_count = await db.scalar(
            select(func.count(ScenarioGroup.id)).where(
                ScenarioGroup.group_id == group.id
            )
        )
        groups_data.append(
            {
                "group": group,
                "member_count": member_count or 0,
                "scenario_count": scenario_count or 0,
            }
        )

    return templates.TemplateResponse(
        "admin/groups.html",
        {
            "request": request,
            "user": user,
            "groups_data": groups_data,
        },
    )


@router.post("/admin/groups", status_code=201)
async def create_group(
    data: GroupCreate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/groups - Create new group."""

    # Check unique name
    existing = await db.execute(
        select(UserGroup).where(UserGroup.name == data.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail="이미 존재하는 그룹 이름입니다.",
        )

    group = UserGroup(
        name=data.name,
        description=data.description,
    )
    db.add(group)
    await db.flush()
    await db.refresh(group)

    return AdminGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        member_count=0,
        scenario_count=0,
    )


@router.post("/admin/groups/{group_id}/update")
async def update_group(
    group_id: int,
    data: GroupUpdate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/groups/{id}/update - Update group."""

    group = await db.get(UserGroup, group_id)
    if not group:
        raise HTTPException(
            status_code=404,
            detail="그룹을 찾을 수 없습니다.",
        )

    if data.name is not None:
        # Check unique name (excluding self)
        existing = await db.execute(
            select(UserGroup).where(
                UserGroup.name == data.name,
                UserGroup.id != group_id,
            )
        )
        if existing.scalar_one_or_none():
            raise HTTPException(
                status_code=400,
                detail="이미 존재하는 그룹 이름입니다.",
            )
        group.name = data.name

    if data.description is not None:
        group.description = data.description

    await db.flush()
    await db.refresh(group)

    member_count = await db.scalar(
        select(func.count(User.id)).where(User.group_id == group.id)
    )
    scenario_count = await db.scalar(
        select(func.count(ScenarioGroup.id)).where(
            ScenarioGroup.group_id == group.id
        )
    )

    return AdminGroupResponse(
        id=group.id,
        name=group.name,
        description=group.description,
        created_at=group.created_at,
        member_count=member_count or 0,
        scenario_count=scenario_count or 0,
    )


@router.post("/admin/groups/{group_id}/delete")
async def delete_group(
    group_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/groups/{id}/delete - Delete group."""

    group = await db.get(UserGroup, group_id)
    if not group:
        raise HTTPException(
            status_code=404,
            detail="그룹을 찾을 수 없습니다.",
        )

    # Block if has members
    member_count = await db.scalar(
        select(func.count(User.id)).where(User.group_id == group.id)
    )
    if member_count and member_count > 0:
        raise HTTPException(
            status_code=400,
            detail=f"이 그룹에 {member_count}명의 "
            "멤버가 있어 삭제할 수 없습니다. "
            "먼저 멤버를 다른 그룹으로 이동하세요.",
        )

    await db.delete(group)
    await db.flush()

    return {
        "status": "deleted",
        "group_id": group_id,
    }
