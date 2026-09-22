"""프롬프트 템플릿 관리 서비스 (Task 3.2.2).

동적 프롬프트 로딩과 버전 관리를 제공합니다.
시나리오별 템플릿 선택을 통해 StudentBot과 TutorBot이
DB 또는 파일 시스템에서 프롬프트를 로드합니다.
"""

import logging
from datetime import datetime, timezone
from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from src.models.prompt_template import PromptTemplate

logger = logging.getLogger(__name__)


class PromptManager:
    """프롬프트 템플릿 관리 서비스.

    Features:
    - 시나리오별 템플릿 선택 (template_id 기준)
    - Fallback: DB → 파일 시스템 → 하드코딩
    - 버전 관리
    """

    @classmethod
    async def get_template_by_id(
        cls, db: AsyncSession, template_id: int
    ) -> Optional[PromptTemplate]:
        """ID로 프롬프트 템플릿 조회.

        Args:
            db: Database session
            template_id: 조회할 템플릿 ID

        Returns:
            PromptTemplate 객체 또는 None (존재하지 않을 경우)
        """
        try:
            result = await db.execute(
                select(PromptTemplate).where(
                    PromptTemplate.id == template_id
                )
            )
            template = result.scalar_one_or_none()

            if template:
                logger.debug(
                    f"Template loaded by ID: {template_id} "
                    f"({template.bot_type} v{template.version})"
                )
            else:
                logger.warning(f"Template not found: ID={template_id}")

            return template

        except Exception as e:
            logger.error(f"Failed to load template by ID {template_id}: {e}")
            return None

    @classmethod
    async def get_template_text_by_id(
        cls, db: AsyncSession, template_id: int
    ) -> str:
        """ID로 프롬프트 템플릿 텍스트 조회.

        Args:
            db: Database session
            template_id: 조회할 템플릿 ID

        Returns:
            프롬프트 템플릿 문자열

        Raises:
            ValueError: 템플릿을 찾을 수 없는 경우
        """
        template = await cls.get_template_by_id(db, template_id)

        if template is None:
            error_msg = f"Prompt template not found: ID={template_id}"
            logger.error(error_msg)
            raise ValueError(error_msg)

        logger.info(
            f"Template text loaded: {template.bot_type} "
            f"v{template.version} (ID={template_id})"
        )
        return template.template_text

    @classmethod
    def _load_from_file(cls, bot_type: str) -> str:
        """파일 시스템에서 기본 프롬프트 로드.

        Args:
            bot_type: 'student' or 'tutor'

        Returns:
            프롬프트 문자열

        Fallback:
            파일이 없으면 하드코딩된 기본 프롬프트 반환
        """
        import os

        filename = f"src/prompts/{bot_type}_system.txt"

        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    content = f.read()
                logger.info(f"File load: {bot_type} prompt from {filename}")
                return content
            except Exception as e:
                logger.error(f"Failed to read {filename}: {e}")

        # 4. 하드코딩된 fallback
        logger.warning(f"File not found: {filename}. Using hardcoded default.")
        return f"You are a {bot_type} bot."

    @classmethod
    async def list_prompts(
        cls, db: AsyncSession, bot_type: Optional[str] = None
    ) -> list[PromptTemplate]:
        """프롬프트 목록 조회 (버전 히스토리).

        Args:
            db: Database session
            bot_type: 필터링할 봇 타입 (None이면 전체)

        Returns:
            PromptTemplate 객체 리스트 (생성일 역순)
        """
        query = select(PromptTemplate)
        if bot_type:
            query = query.where(PromptTemplate.bot_type == bot_type)
        query = query.order_by(PromptTemplate.created_at.desc())

        result = await db.execute(query)
        return list(result.scalars().all())

    @classmethod
    async def create_prompt(
        cls,
        db: AsyncSession,
        bot_type: str,
        template_name: str,
        template_text: str,
        updated_by: Optional[int] = None,
    ) -> PromptTemplate:
        """새 프롬프트 생성.

        Args:
            db: Database session
            bot_type: 'student' or 'tutor'
            template_name: 템플릿 이름
            template_text: 프롬프트 전문 (10-10,000자)
            updated_by: 생성자 User ID

        Returns:
            생성된 PromptTemplate 객체
        """
        # 버전 번호 계산 (동일 bot_type의 최대 버전 + 1)
        result = await db.execute(
            select(PromptTemplate)
            .where(PromptTemplate.bot_type == bot_type)
            .order_by(PromptTemplate.version.desc())
            .limit(1)
        )
        latest = result.scalar_one_or_none()
        version = (latest.version + 1) if latest else 1

        # 새 템플릿 생성
        template = PromptTemplate(
            bot_type=bot_type,
            template_name=template_name,
            template_text=template_text,
            version=version,
            updated_by=updated_by,
        )
        db.add(template)
        await db.flush()

        logger.info(f"Created prompt: {bot_type} v{version}")

        return template

    @classmethod
    async def update_prompt(
        cls,
        db: AsyncSession,
        prompt_id: int,
        template_name: Optional[str] = None,
        template_text: Optional[str] = None,
        updated_by: Optional[int] = None,
    ) -> PromptTemplate:
        """프롬프트 내용 수정 (버전 유지).

        Note: 데이터 정합성을 위해 중요 변경은 create_prompt(새 버전) 권장.
        오타 수정 등을 위해 제한적으로 사용.

        Args:
            db: Database session
            prompt_id: 수정할 프롬프트 ID
            template_name: 새 이름 (Optional)
            template_text: 새 텍스트 (Optional)
            updated_by: 수정한 User ID

        Returns:
            수정된 PromptTemplate 객체
        """
        result = await db.execute(
            select(PromptTemplate).where(PromptTemplate.id == prompt_id)
        )
        template = result.scalar_one()

        if template_name:
            template.template_name = template_name
        if template_text:
            template.template_text = template_text
        
        template.updated_at = datetime.now(timezone.utc)
        if updated_by:
            template.updated_by = updated_by
            
        await db.flush()

        logger.info(
            f"Updated prompt: {template.bot_type} "
            f"v{template.version}"
        )

        return template

    @classmethod
    async def delete_prompt(cls, db: AsyncSession, prompt_id: int) -> None:
        """프롬프트 삭제.

        Args:
            db: Database session
            prompt_id: 삭제할 프롬프트 ID
        """
        result = await db.execute(
            select(PromptTemplate).where(
                PromptTemplate.id == prompt_id
            )
        )
        template = result.scalar_one()

        bot_type = template.bot_type
        version = template.version

        await db.delete(template)
        await db.flush()

        logger.info(
            f"Deleted prompt: {bot_type} v{version}"
        )

