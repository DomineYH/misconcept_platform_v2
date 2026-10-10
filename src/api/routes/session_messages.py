"""Session message handling routes."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    Query,
    Request,
)
from fastapi.responses import JSONResponse, Response
from fastapi.templating import Jinja2Templates
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_current_user, get_db_session, templates
from src.api.routes.session_helpers import load_session, require_native_session
from src.config import config
from src.models import Message, User
from src.services.session_history import session_display

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Sessions"])
limiter = Limiter(key_func=get_remote_address, enabled=not config.TESTING)


def _validate_and_render_message(
    message,
    request: Request,
    templates: Jinja2Templates,
    student_name: str | None = None,
) -> str | None:
    """Validate message attributes and render to HTML.

    Returns:
        Rendered HTML string or None if validation/rendering failed.
    """
    try:
        if not hasattr(message, "id") or message.id is None:
            logger.error(
                f"Message missing 'id' attribute. "
                f"role={getattr(message, 'role', None)}"
            )
            return None

        if not hasattr(message, "role") or not message.role:
            logger.error(f"Message {message.id} missing 'role' attribute")
            return None

        if not hasattr(message, "content"):
            logger.error(f"Message {message.id} missing 'content' attribute")
            return None

        html = templates.get_template("partials/message.html").render(
            message=message,
            request=request,
            student_name=student_name,
        )

        if not html or 'class="message' not in html:
            logger.error(
                f"Rendered HTML invalid or empty for message {message.id}. "
                f"HTML length: {len(html) if html else 0}"
            )
            return None

        logger.debug(
            f"Successfully rendered message {message.id} ({message.role})"
        )
        return html

    except Exception as e:
        logger.error(
            f"Failed to render message {getattr(message, 'id', 'unknown')}: "
            f"{e}",
            exc_info=True,
        )
        return None


@router.post("/sessions/{session_id}/messages")
@limiter.limit("30/minute")
async def send_message(
    request: Request,
    session_id: int,
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Old tabs must reload; all new writes use durable execution rights."""
    session = await load_session(session_id, user, db)
    require_native_session(session)
    return JSONResponse(
        {"code": "reload_required", "detail": "새로고침 후 다시 전송해주세요."},
        status_code=410,
    )


@router.get("/sessions/{session_id}/messages/updates")
@limiter.limit("60/minute")
async def get_message_updates(
    request: Request,
    session_id: int,
    since: int | None = Query(None, description="Last message ID"),
    user: User = Depends(get_current_user),
    db: AsyncSession = Depends(get_db_session),
) -> Response:
    """Get new messages since last message ID for HTMX polling."""
    session = await load_session(session_id, user, db)

    student_name = session_display(session)["student_name"]

    query = (
        select(Message)
        .where(Message.session_id == session_id)
        .order_by(Message.id)
        .limit(50)
    )

    if since is not None:
        query = query.where(Message.id > since)

    messages_result = await db.execute(query)
    messages = messages_result.scalars().all()

    if not messages:
        return Response(status_code=204)

    rendered_messages = []
    for message in messages:
        html = _validate_and_render_message(
            message, request, templates, student_name
        )
        if html:
            rendered_messages.append(html)

    combined_html = "".join(rendered_messages)

    if not combined_html:
        logger.warning(
            f"Polling: No messages rendered from {len(messages)} messages "
            f"for session {session_id}"
        )
        return Response(status_code=204)

    logger.debug(
        f"Polling: Rendered {len(rendered_messages)}/{len(messages)} "
        f"messages for session {session_id}"
    )

    last_id = max(m.id for m in messages)
    oob_input = (
        f'<input type="hidden" name="since" '
        f'value="{last_id}" id="last-message-id" '
        f'hx-swap-oob="true">'
    )
    combined_html += oob_input

    return Response(
        content=combined_html,
        media_type="text/html",
        status_code=200,
    )
