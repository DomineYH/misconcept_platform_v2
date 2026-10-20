"""Admin user management routes."""

import logging

from fastapi import (
    APIRouter,
    Depends,
    File,
    HTTPException,
    Query,
    Request,
    UploadFile,
)
from fastapi.responses import HTMLResponse, Response
from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import joinedload

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.routes.admin_session_actions import demo_delete
from src.api.schemas import (
    AdminUserResponse,
    BulkPreviewResponse,
    BulkRegisterRequest,
    BulkRegisterResponse,
    UserCreate,
    UserUpdate,
)
from src.config import config
from src.demo.database import DemoError
from src.demo.lifecycle import NORMAL_SCOPE, lifecycle
from src.i18n import display_language, translate
from src.models.generation_run import GenerationRun
from src.models.model_probe import ModelProbe
from src.models.session import Session
from src.models.user import User
from src.models.user_group import UserGroup
from src.services.admin_user_bulk import demo_quota

logger = logging.getLogger(__name__)

router = APIRouter(tags=["Admin Users"])
UPLOAD_CHUNK = 64 * 1024


@router.get("/admin/users/bulk/template")
async def download_bulk_template(
    user: User = Depends(get_admin_user),
):
    """GET /admin/users/bulk/template — CSV template."""
    csv_content = "\ufeffusername,nickname,role,group\n"
    return Response(
        content=csv_content.encode("utf-8"),
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": (
                "attachment; " "filename=bulk_users_template.csv"
            )
        },
    )


@router.post(
    "/admin/users/bulk/preview",
    response_model=BulkPreviewResponse,
)
async def preview_bulk_upload(
    file: UploadFile = File(...),
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """POST /admin/users/bulk/preview — Parse and validate CSV."""
    from src.services.admin_user_bulk import (
        parse_csv,
        validate_bulk_users,
    )

    content = b""
    while chunk := await file.read(UPLOAD_CHUNK):
        content += chunk
        if (
            config.APP_MODE == "demo"
            and len(content) > config.DEMO_MAX_UPLOAD_BYTES
        ):
            # §8: stop reading at the visitor upload limit.
            raise DemoError("UPLOAD_TOO_LARGE")

    try:
        rows = parse_csv(content, display_language(request))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    result = await validate_bulk_users(rows, db, display_language(request))
    return result


@router.post(
    "/admin/users/bulk/register",
    response_model=BulkRegisterResponse,
)
async def register_bulk_users_endpoint(
    data: BulkRegisterRequest,
    response: Response,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """POST /admin/users/bulk/register — Create users."""
    from src.services.admin_user_bulk import (
        register_bulk_users,
    )

    result = await register_bulk_users(
        data.users, db, display_language(request)
    )
    response.headers["Cache-Control"] = "no-store"
    return result


@router.get("/admin/users", response_class=HTMLResponse)
async def list_users(
    request: Request,
    page: int = Query(1, ge=1),
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """GET /admin/users - User management page."""

    total_count = await db.scalar(select(func.count(User.id))) or 0
    result = await db.execute(
        select(User)
        .options(joinedload(User.group))
        .order_by(User.id.desc())
        .limit(20)
        .offset(min((page - 1) * 20, total_count))
    )
    users = result.scalars().all()

    groups_result = await db.execute(select(UserGroup).order_by(UserGroup.name))
    groups = groups_result.scalars().all()

    return templates.TemplateResponse(
        "admin/users.html",
        {
            "request": request,
            "user": user,
            "users": users,
            "current_page": page,
            "total_count": total_count,
            "total_pages": max(1, (total_count + 19) // 20),
            "groups": groups,
        },
    )


@router.post("/admin/users", status_code=201)
async def create_user(
    data: UserCreate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """POST /admin/users - Create new user."""
    await demo_quota(db, User, config.DEMO_MAX_USERS)

    # Validate username format
    if not data.username.replace("_", "").isalnum():
        raise HTTPException(
            status_code=400,
            detail=translate(
                "admin.user_ids_may_contain_only_letters_numbers_and_underscores",
                display_language(request),
            ),
        )

    # Check unique username
    existing = await db.execute(
        select(User).where(User.username == data.username)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(
            status_code=400,
            detail=translate(
                "admin.user_id_already_exists", display_language(request)
            ),
        )

    # Verify group exists if provided
    if data.group_id is not None:
        group = await db.get(UserGroup, data.group_id)
        if not group:
            raise HTTPException(
                status_code=400,
                detail=translate(
                    "admin.group_not_found", display_language(request)
                ),
            )

    # Validate role
    if data.role not in ("teacher", "admin"):
        raise HTTPException(
            status_code=400,
            detail=translate("admin.invalid_role", display_language(request)),
        )

    new_user = User(
        username=data.username,
        nickname=data.nickname,
        role=data.role,
        group_id=data.group_id,
    )
    new_user.set_password(data.password)

    db.add(new_user)
    await db.flush()
    await db.refresh(new_user)

    group_name = None
    if new_user.group_id:
        grp = await db.get(UserGroup, new_user.group_id)
        group_name = grp.name if grp else None

    return AdminUserResponse(
        id=new_user.id,
        username=new_user.username,
        nickname=new_user.nickname,
        role=new_user.role,
        group_id=new_user.group_id,
        group_name=group_name,
        created_at=new_user.created_at,
    )


@router.post("/admin/users/{user_id}/update")
async def update_user(
    user_id: int,
    data: UserUpdate,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """POST /admin/users/{id}/update - Update user."""

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(
            status_code=404,
            detail=translate("admin.user_not_found", display_language(request)),
        )

    if data.nickname is not None:
        target.nickname = data.nickname
    if data.role is not None:
        if data.role not in ("teacher", "admin"):
            raise HTTPException(
                status_code=400,
                detail=translate(
                    "admin.invalid_role", display_language(request)
                ),
            )
        target.role = data.role
    if data.group_id is not None:
        if data.group_id == 0:
            target.group_id = None
        else:
            group = await db.get(UserGroup, data.group_id)
            if not group:
                raise HTTPException(
                    status_code=400,
                    detail=translate(
                        "admin.group_not_found", display_language(request)
                    ),
                )
            target.group_id = data.group_id
    if data.password is not None:
        target.set_password(data.password)

    await db.flush()
    await db.refresh(target)

    group_name = None
    if target.group_id:
        grp = await db.get(UserGroup, target.group_id)
        group_name = grp.name if grp else None

    return AdminUserResponse(
        id=target.id,
        username=target.username,
        nickname=target.nickname,
        role=target.role,
        group_id=target.group_id,
        group_name=group_name,
        created_at=target.created_at,
    )


@router.post("/admin/users/{user_id}/delete")
async def delete_user(
    user_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
    request: Request = None,
):
    """POST /admin/users/{id}/delete - Delete user."""
    if config.APP_MODE == "demo":
        comparison_generations = await demo_delete(
            request,
            lambda demo_db: remove_user(user_id, user, demo_db, request),
            teacher_id=user_id,
        )
        scope = request.state.demo_context.scope
    else:
        comparison_generations = await remove_user(user_id, user, db, request)
        scope = NORMAL_SCOPE
    for run_id in comparison_generations:
        lifecycle.cancel(scope, "run", run_id)

    return {
        "status": "deleted",
        "user_id": user_id,
    }


async def remove_user(user_id, user, db, request):
    """Apply the existing deletion limits; return running comparison runs."""
    if user_id == user.id:
        raise HTTPException(
            status_code=400,
            detail=translate(
                "admin.you_cannot_delete_yourself", display_language(request)
            ),
        )

    target = await db.get(User, user_id)
    if not target:
        raise HTTPException(
            status_code=404,
            detail=translate("admin.user_not_found", display_language(request)),
        )

    # Check for active sessions
    active_count = await db.scalar(
        select(func.count(Session.id)).where(
            Session.teacher_id == user_id,
            Session.ended_at.is_(None),
            Session.deleted_at.is_(None),
        )
    )

    if active_count and active_count > 0:
        raise HTTPException(
            status_code=400,
            detail=translate(
                "admin.users_with_active_sessions_cannot_be_deleted_end_their_sessions_first",
                display_language(request),
            ),
        )

    if (
        await db.scalar(
            select(ModelProbe.id).where(ModelProbe.owner_id == user_id).limit(1)
        )
        is not None
    ):
        raise HTTPException(
            status_code=400,
            detail=translate(
                "admin.users_with_model_test_records_cannot_be_deleted",
                display_language(request),
            ),
        )

    # Nullify teacher_id on ended sessions
    await db.execute(
        update(Session)
        .where(Session.teacher_id == user_id)
        .values(teacher_id=None)
    )

    comparison_generations = (
        await db.scalars(
            select(GenerationRun.id).where(
                GenerationRun.owner_id == user_id,
                GenerationRun.comparison_run_id.is_not(None),
                GenerationRun.status == "running",
            )
        )
    ).all()
    await db.delete(target)
    await db.flush()
    return comparison_generations
