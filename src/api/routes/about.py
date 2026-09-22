"""Public About page route."""

import logging

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db_session, templates
from src.models.contributor import Contributor

logger = logging.getLogger(__name__)

router = APIRouter(tags=["About"])


@router.get("/about/content", response_class=HTMLResponse)
async def about_content(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """GET /about/content - HTML fragment for about modal."""
    result = await db.execute(
        select(Contributor).order_by(
            Contributor.sort_order, Contributor.id
        )
    )
    contributors = result.scalars().all()

    return templates.TemplateResponse(
        "about_fragment.html",
        {
            "request": request,
            "contributors": contributors,
        },
    )
