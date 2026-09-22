"""Authentication routes for login/logout."""
from fastapi import (
    APIRouter,
    Depends,
    Form,
    Request,
    HTTPException,
)
from fastapi.responses import HTMLResponse, RedirectResponse
from slowapi import Limiter
from slowapi.util import get_remote_address
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_db_session, templates
from src.models import User
from src.config import config

router = APIRouter(tags=["Authentication"])
limiter = Limiter(
    key_func=get_remote_address, enabled=not config.TESTING
)


@router.get("/login", response_class=HTMLResponse)
async def get_login(request: Request):
    """Display login form."""
    return templates.TemplateResponse(
        "login.html", {"request": request}
    )


@router.post("/login")
@limiter.limit("5/minute")
async def post_login(
    request: Request,
    username: str = Form(
        ..., min_length=3, max_length=50
    ),
    password: str = Form(
        ..., min_length=8, max_length=128
    ),
    db: AsyncSession = Depends(get_db_session),
):
    """Authenticate user with username/password."""
    # Validate username format
    if not username.replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail="사용자 ID는 영문, 숫자, 언더스코어만 "
            "사용 가능합니다.",
        )

    # Find user by username
    result = await db.execute(
        select(User).where(User.username == username)
    )
    user = result.scalar_one_or_none()

    if not user or not user.verify_password(password):
        return templates.TemplateResponse(
            "login.html",
            {
                "request": request,
                "error": "사용자 ID 또는 비밀번호가 "
                "올바르지 않습니다.",
            },
            status_code=401,
        )

    # Create session cookie
    response = RedirectResponse(
        url="/scenarios", status_code=303
    )
    request.session["user_id"] = user.id
    request.session["username"] = user.username
    request.session["nickname"] = user.nickname
    request.session["role"] = user.role
    request.session["group_id"] = user.group_id

    return response


@router.post("/logout")
async def post_logout(request: Request):
    """Clear session and redirect to login."""
    request.session.clear()
    return RedirectResponse(url="/login", status_code=303)
