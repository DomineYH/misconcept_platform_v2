"""Admin About page routes — contributor CRUD."""

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.schemas.contributor import (
    ContributorCreate,
    ContributorResponse,
    ContributorUpdate,
)
from src.models.contributor import Contributor
from src.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin About"])


@router.get("/admin/about", response_class=HTMLResponse)
async def admin_about_page(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """GET /admin/about - Manage contributor info."""
    result = await db.execute(
        select(Contributor).order_by(Contributor.sort_order, Contributor.id)
    )
    contributors = result.scalars().all()

    return templates.TemplateResponse(
        "admin/about.html",
        {
            "request": request,
            "user": user,
            "contributors": contributors,
        },
    )


@router.post("/admin/about", status_code=201)
async def create_contributor(
    data: ContributorCreate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/about - Create a new contributor entry."""
    contributor = Contributor(
        name=data.name,
        affiliation=data.affiliation,
        bio=data.bio,
        phone=data.phone,
        email=data.email,
        sort_order=data.sort_order,
    )
    db.add(contributor)
    await db.flush()
    await db.refresh(contributor)

    return ContributorResponse.model_validate(contributor)


@router.post("/admin/about/{contributor_id}/update")
async def update_contributor(
    contributor_id: int,
    data: ContributorUpdate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/about/{id}/update - Update contributor entry."""
    target = await db.get(Contributor, contributor_id)
    if not target:
        raise HTTPException(
            status_code=404,
            detail="등록자를 찾을 수 없습니다.",
        )

    if data.name is not None:
        target.name = data.name
    if data.affiliation is not None:
        target.affiliation = data.affiliation
    if data.bio is not None:
        target.bio = data.bio
    if data.phone is not None:
        target.phone = data.phone or None
    if data.email is not None:
        target.email = data.email or None
    if data.sort_order is not None:
        target.sort_order = data.sort_order

    target.updated_at = datetime.now(timezone.utc)

    await db.flush()
    await db.refresh(target)

    return ContributorResponse.model_validate(target)


@router.post("/admin/about/{contributor_id}/delete")
async def delete_contributor(
    contributor_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """POST /admin/about/{id}/delete - Delete contributor entry."""
    target = await db.get(Contributor, contributor_id)
    if not target:
        raise HTTPException(
            status_code=404,
            detail="등록자를 찾을 수 없습니다.",
        )

    await db.delete(target)
    await db.flush()

    return {"status": "deleted", "contributor_id": contributor_id}
