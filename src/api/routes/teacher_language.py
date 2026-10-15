"""Localize teacher HTTP error text without changing status, codes or payloads."""

from fastapi import HTTPException, Request
from fastapi.routing import APIRoute

from src.i18n import display_language, translate

# These legacy literals identify messages for translation, never error behavior.
ERROR_KEYS = {
    "User not found": "user_not_found",
    "Session not found": "session_not_found",
    "Scenario not found": "scenario_not_found",
    "Turn not found": "turn_not_found",
    "Run not found": "run_not_found",
    "Analysis not found": "analysis_not_found",
    "Forbidden": "forbidden",
    "Session must be ended before viewing analysis": "end_before_analysis",
    "Session must be ended before analysis": "end_before_analysis",
    "대화가 종료되었습니다.": "conversation_ended",
    "그룹이 배정되지 않았습니다.": "no_group",
    "이 시나리오에 대한 접근 권한이 없습니다.": "scenario_forbidden",
    "대화 세션을 시작할 수 없습니다. 잠시 후 다시 시도해주세요.": "start_failed",
    "과거 세션은 읽기 전용입니다. 게시된 시나리오로 새 연습을 시작해주세요.": "legacy_read_only_error",
    "관리자에게 AI 연결과 학생 모델 검증을 요청해주세요.": "student_configuration_error",
    "질문을 짧게 수정하거나 관리자에게 AI 설정을 확인해주세요.": "shorten_question",
    "관리자에게 AI 설정을 확인하거나 잠시 후 다시 시도해주세요.": "check_ai_settings",
    "관리자에게 AI 연결과 멘토 모델 검증을 요청하거나 잠시 후 다시 시도해주세요.": "check_mentor_settings",
}


class TeacherLanguageRoute(APIRoute):
    def get_route_handler(self):
        handler = super().get_route_handler()

        async def localized(request: Request):
            try:
                return await handler(request)
            except HTTPException as error:
                detail = error.detail
                text = (
                    detail.get("message")
                    if isinstance(detail, dict)
                    else detail
                )
                key = ERROR_KEYS.get(text) if isinstance(text, str) else None
                if key:
                    translated = translate(
                        f"teacher.{key}", display_language(request)
                    )
                    error.detail = (
                        {**detail, "message": translated}
                        if isinstance(detail, dict)
                        else translated
                    )
                raise

        return localized
