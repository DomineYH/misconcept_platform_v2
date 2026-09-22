"""Admin 프롬프트 템플릿 관리 API (Task 3.2.4).

관리자가 StudentBot과 TutorBot의 시스템 프롬프트를
조회, 생성, 수정, 삭제할 수 있는 API를 제공합니다.
"""

import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from src.api.dependencies import get_admin_user, get_db_session, templates
from src.api.schemas.prompt import PromptUpdateRequest
from src.models.user import User
from src.services.prompt_manager import PromptManager

logger = logging.getLogger(__name__)

_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"

router = APIRouter(prefix="/admin", tags=["admin", "prompts"])


# Pydantic 모델
class PromptCreateRequest(BaseModel):
    """프롬프트 생성 요청 모델."""

    bot_type: str = Field(..., pattern="^(student|tutor)$")
    template_name: str = Field(..., min_length=3, max_length=100)
    template_text: str = Field(..., min_length=10, max_length=10000)


# 페이지 라우트
@router.get("/prompts-page", response_class=HTMLResponse)
async def prompts_page(
    request: Request,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """프롬프트 관리 페이지.

    관리자 전용 페이지로 모든 프롬프트 템플릿을 조회하고
    새 템플릿을 생성하거나 활성화할 수 있습니다.
    """

    # 모든 프롬프트 조회 (최신순)
    prompts = await PromptManager.list_prompts(db)

    # 기본 프롬프트 텍스트 로드
    default_student_text = (_PROMPTS_DIR / "student_system.txt").read_text(
        encoding="utf-8"
    )
    default_tutor_text = (_PROMPTS_DIR / "tutor_system.txt").read_text(
        encoding="utf-8"
    )

    return templates.TemplateResponse(
        "admin/prompts.html",
        {
            "request": request,
            "user": user,
            "prompts": prompts,
            "default_student_text": default_student_text,
            "default_tutor_text": default_tutor_text,
        },
    )


# API 라우트
@router.get("/prompts")
async def list_prompts(
    bot_type: str | None = None,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """프롬프트 목록 조회 API.

    Args:
        bot_type: 필터링할 봇 타입 ('student' 또는 'tutor')

    Returns:
        프롬프트 목록 (생성일 역순)
    """

    prompts = await PromptManager.list_prompts(db, bot_type)
    return {"prompts": [p.to_dict() for p in prompts]}


@router.post("/prompts")
async def create_prompt(
    req: PromptCreateRequest,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """새 프롬프트 템플릿 생성.

    Args:
        req: 프롬프트 생성 요청 데이터

    Returns:
        생성된 프롬프트 정보
    """

    try:
        prompt = await PromptManager.create_prompt(
            db,
            bot_type=req.bot_type,
            template_name=req.template_name,
            template_text=req.template_text,
            updated_by=user.id,
        )
        await db.flush()

        logger.info(
            f"Admin {user.nickname} created {req.bot_type} prompt "
            f"v{prompt.version}"
        )

        return {"prompt": prompt.to_dict()}

    except Exception as e:
        logger.error(f"Failed to create prompt: {e}")
        raise HTTPException(status_code=500, detail="Failed to create prompt")


@router.post("/prompts/{prompt_id}/update")
async def update_prompt(
    prompt_id: int,
    req: PromptUpdateRequest,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """프롬프트 내용 수정.

    Args:
        prompt_id: 수정할 프롬프트 ID
        req: 수정할 내용 (이름, 텍스트)

    Returns:
        수정된 프롬프트 정보
    """

    try:
        prompt = await PromptManager.update_prompt(
            db,
            prompt_id,
            template_name=req.template_name,
            template_text=req.template_text,
            updated_by=user.id,
        )
        await db.flush()

        logger.info(
            f"Admin {user.nickname} updated prompt {prompt_id} "
            f"(v{prompt.version})"
        )
        return {"prompt": prompt.to_dict()}

    except Exception as e:
        logger.error(f"Failed to update prompt {prompt_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to update prompt")


@router.post("/prompts/{prompt_id}/delete")
async def delete_prompt(
    prompt_id: int,
    user: User = Depends(get_admin_user),
    db: AsyncSession = Depends(get_db_session),
):
    """프롬프트 삭제.

    Args:
        prompt_id: 삭제할 프롬프트 ID
    """

    try:
        await PromptManager.delete_prompt(db, prompt_id)
        await db.flush()

        logger.info(f"Admin {user.nickname} deleted prompt {prompt_id}")
        return {"status": "success", "id": prompt_id}

    except Exception as e:
        logger.error(f"Failed to delete prompt {prompt_id}: {e}")
        raise HTTPException(status_code=500, detail="Failed to delete prompt")
