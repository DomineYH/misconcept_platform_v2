"""Admin analysis management routes."""

import logging
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import desc, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.routes.admin_sessions import safe_int
from src.models.message import Message
from src.models.question_analysis import QuestionAnalysis
from src.models.scenario import Scenario
from src.models.session import Session
from src.models.user import User
from src.utils.analysis_helpers import parse_reasoning

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Analysis"])


@router.get("/admin/analysis-page", response_class=HTMLResponse)
async def analysis_page(
    request: Request,
    scenario_id: Optional[str] = None,
    label: Optional[str] = None,
    date_from: Optional[str] = None,
    date_to: Optional[str] = None,
    page: int = Query(1, ge=1),
    per_page: int = Query(20, ge=1, le=100),
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Admin analysis management page with filtering."""
    scenario_id_val = safe_int(scenario_id)

    # Build base query
    query = (
        select(QuestionAnalysis, Message, Session, Scenario)
        .join(Message, QuestionAnalysis.message_id == Message.id)
        .join(Session, Message.session_id == Session.id)
        .join(Scenario, Session.scenario_id == Scenario.id)
        .order_by(desc(QuestionAnalysis.id))
    )

    # Apply filters
    if scenario_id_val:
        query = query.where(Session.scenario_id == scenario_id_val)
    if label:
        query = query.where(QuestionAnalysis.label == label)
    if date_from:
        try:
            dt = datetime.fromisoformat(date_from)
            query = query.where(Message.created_at >= dt)
        except ValueError:
            pass
    if date_to:
        try:
            dt = datetime.fromisoformat(date_to)
            query = query.where(Message.created_at <= dt)
        except ValueError:
            pass

    # Count total
    count_query = select(func.count()).select_from(query.subquery())
    total = await db.scalar(count_query) or 0

    # Paginate
    query = query.offset((page - 1) * per_page).limit(per_page)
    result = await db.execute(query)
    rows = result.all()

    # Format analyses
    analyses = []
    for analysis, message, session, scenario in rows:
        analyses.append(
            {
                "id": analysis.id,
                "content": message.content,
                "label": analysis.label,
                "confidence": analysis.confidence or 0,
                "reasoning": parse_reasoning(analysis.meta_json),
                "session_id": session.id,
                "scenario_title": scenario.title,
                "created_at": message.created_at,
            }
        )

    # Get scenarios for filter dropdown
    scenarios_result = await db.execute(
        select(Scenario).order_by(Scenario.title)
    )
    scenarios = scenarios_result.scalars().all()

    # Get available labels for filter
    labels_result = await db.execute(select(QuestionAnalysis.label).distinct())
    available_labels = [r[0] for r in labels_result.all()]

    # Calculate stats
    avg_conf_result = await db.scalar(
        select(func.avg(QuestionAnalysis.confidence))
    )

    # Get most common label
    most_common_result = await db.execute(
        select(QuestionAnalysis.label, func.count(QuestionAnalysis.id))
        .group_by(QuestionAnalysis.label)
        .order_by(desc(func.count(QuestionAnalysis.id)))
        .limit(1)
    )
    most_common_row = most_common_result.first()
    most_common_label = most_common_row[0] if most_common_row else "N/A"

    stats = {
        "total_analyses": total,
        "avg_confidence": avg_conf_result or 0,
        "most_common_label": most_common_label,
    }

    # Pagination info
    total_pages = (total + per_page - 1) // per_page if total > 0 else 1
    pagination = {
        "page": page,
        "per_page": per_page,
        "total": total,
        "total_pages": total_pages,
        "has_prev": page > 1,
        "has_next": page < total_pages,
    }

    return templates.TemplateResponse(
        "admin/analysis.html",
        {
            "request": request,
            "user": user,
            "analyses": analyses,
            "scenarios": scenarios,
            "available_labels": available_labels,
            "stats": stats,
            "pagination": pagination,
            "current_filters": {
                "scenario_id": scenario_id_val,
                "label": label,
                "date_from": date_from,
                "date_to": date_to,
            },
        },
    )


@router.get("/admin/analysis/{analysis_id}/detail", response_class=HTMLResponse)
async def analysis_detail_modal(
    request: Request,
    analysis_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """Get detailed analysis modal content."""

    # Load analysis with related data
    query = (
        select(QuestionAnalysis, Message, Session, Scenario)
        .join(Message, QuestionAnalysis.message_id == Message.id)
        .join(Session, Message.session_id == Session.id)
        .join(Scenario, Session.scenario_id == Scenario.id)
        .where(QuestionAnalysis.id == analysis_id)
    )

    result = await db.execute(query)
    row = result.first()

    if not row:
        raise HTTPException(status_code=404, detail="분석을 찾을 수 없습니다")

    analysis, message, session, scenario = row

    return templates.TemplateResponse(
        "partials/analysis_detail_modal.html",
        {
            "request": request,
            "analysis": {
                "id": analysis.id,
                "content": message.content,
                "label": analysis.label,
                "confidence": analysis.confidence,
                "reasoning": parse_reasoning(analysis.meta_json),
                "session_id": session.id,
                "scenario_title": scenario.title,
                "created_at": message.created_at,
            },
        },
    )
