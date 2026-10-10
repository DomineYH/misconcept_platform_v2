"""Fixed synthetic role inputs, independent of classroom data and legacy calls."""

import json

from src.services.invocation_types import (
    InvocationError,
    StructuredRequest,
    TextRequest,
)
from src.services.role_output_contracts import (
    MentorOutput,
    QuestionClassification,
    SessionSynthesis,
)
from src.services.student_probe_contract import (
    MESSAGES,
    OUTPUT_BUDGET,
    SYSTEM_INSTRUCTION,
)
from src.services.student_probe_contract import probe_options as capped_options

OUTPUT_BUDGETS = {"student": OUTPUT_BUDGET, "mentor": 1500, "analysis": 2500}
# Same synthetic IDs/quote as the existing analysis pipeline fixture.
ANALYSIS_MESSAGES = [
    {"id": 100, "role": "teacher", "content": "Why?"},
    {"id": 101, "role": "student", "content": "그냥 덧셈이니까요."},
]
LABELS = {"A": "high", "B": "low"}
DIALOGUE = (
    "교사: 왜 분자끼리 더해도 된다고 생각했어?\n"
    "학생: 그냥 덧셈이니까요.\n교사: 왜 분자끼리 더해도 된다고 생각했어?\n"
    "학생: 그냥 덧셈이니까요."
)


def probe_options(provider, model, role):
    return capped_options(provider, model, output_budget=OUTPUT_BUDGETS[role])


def probe_steps(provider, model_id, role, options, request_id):
    if role not in OUTPUT_BUDGETS:
        raise InvocationError("configuration_unavailable")

    def request(instruction, content, schema=None, context=None):
        values = (
            provider,
            model_id,
            role,
            instruction,
            [{"role": "user", "content": content}],
            options,
            request_id,
        )
        return (
            StructuredRequest(*values, schema, context or {})
            if schema
            else TextRequest(*values)
        )

    if role == "student":
        value = TextRequest(
            provider,
            model_id,
            role,
            SYSTEM_INSTRUCTION,
            MESSAGES,
            options,
            request_id,
        )
        return [("text", "text", value), ("stream", "stream", value)]
    if role == "analysis":
        return [
            (
                "classification",
                "structured",
                request(
                    "교사 질문을 분류하세요. A(high)=생각 탐색, B(low)=정답 유도. "
                    "confidence는 0~1, reasoning.summary는 근거, improved_sentence는 low일 때만 개선 질문입니다.",
                    "교사 질문: Why? 학생: 그냥 덧셈이니까요.",
                    QuestionClassification,
                    {"labels": LABELS},
                ),
            ),
            (
                "synthesis",
                "structured",
                request(
                    "대화를 종합해 교사에게 한국어 코칭을 제공하세요. brief_feedback은 문장당 70자, "
                    "alternative_question은 60자 이내입니다. message_id와 role은 대화와 일치해야 하고 "
                    "quote는 해당 발화의 원문이어야 합니다. 최소 하나의 구체적 강점 또는 개선점을 포함하세요.",
                    json.dumps(ANALYSIS_MESSAGES, ensure_ascii=False),
                    SessionSynthesis,
                    {"messages": ANALYSIS_MESSAGES},
                ),
            ),
        ]
    return [
        (
            "manual_positive",
            "structured",
            request(
                "교사를 위한 수동 도움입니다. should_intervene=true와 공백이 아닌 feedback 코칭을 "
                "반환하세요. reason_summary에는 짧은 판단 설명을 쓰고 원문 추론은 쓰지 마세요.",
                DIALOGUE,
                MentorOutput,
                {"trigger": "manual", "expected_should_intervene": True},
            ),
        ),
        (
            "auto_negative",
            "structured",
            request(
                "자동 멘토 검사입니다. 개입 조건은 교사가 도움을 명시적으로 요청한 경우입니다. "
                "이 합성 대화는 조건을 충족하지 않으므로 should_intervene=false, feedback은 정확히 "
                "빈 문자열로 반환하세요. reason_summary에는 짧은 판단 설명을 쓰세요.",
                "교사: 어떤 방법으로 풀었니?\n학생: 분모를 같게 만들었어요.",
                MentorOutput,
                {"trigger": "auto", "expected_should_intervene": False},
            ),
        ),
    ]
