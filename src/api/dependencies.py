"""FastAPI dependency injection for database and auth."""

import time
from typing import AsyncGenerator

import markdown as md_lib
from fastapi import Depends, HTTPException, Request, status
from fastapi.templating import Jinja2Templates
from markupsafe import Markup, escape
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.db.connection import AsyncSessionLocal

# Shared Jinja2Templates instance
templates = Jinja2Templates(directory="src/templates")


def md_filter(text: str | None) -> Markup:
    """Convert markdown text to safe HTML.

    Escapes raw HTML first to prevent XSS, then converts
    markdown syntax to HTML tags.
    """
    if not text:
        return Markup("")
    escaped = str(escape(text))
    html = md_lib.markdown(escaped, extensions=["nl2br"])
    return Markup(html)


templates.env.filters["md"] = md_filter


class AuthenticationRequired(Exception):  # noqa: N818
    """Exception raised when authentication is required."""

    def __init__(self, redirect_url: str = "/login"):
        self.redirect_url = redirect_url
        super().__init__("Authentication required")


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """
    Provide database session for request scope.

    Yields:
        AsyncSession: Database session with transaction handling.
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def get_current_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get current user from session.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        User: User model instance

    Raises:
        HTTPException: If not authenticated
    """
    from src.models.user import User

    # Get user_id from session
    user_id = request.session.get("user_id")

    # Defensive: touch session to force cookie refresh
    # Ensures itsdangerous timestamp is updated on every request
    if user_id:
        request.session["_refreshed_at"] = int(time.time())

    if not user_id:
        raise AuthenticationRequired(redirect_url="/login")

    # Query user from database
    result = await db.execute(select(User).where(User.id == user_id))
    user = result.scalar_one_or_none()

    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
        )

    return user


async def get_admin_user(
    request: Request,
    db: AsyncSession = Depends(get_db_session),
):
    """
    Get current user and verify admin role.

    Args:
        request: FastAPI Request object
        db: Database session

    Returns:
        User: User model instance with admin role

    Raises:
        HTTPException: If not authenticated or not admin
    """
    user = await get_current_user(request, db)
    if not user.is_admin:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Admin access required",
        )
    return user
