"""Fixed synthetic role inputs, independent of classroom data and legacy calls."""

import json

from src.services.analysis_output_contract import (
    MergeAnalysisOutput,
    UnifiedAnalysisOutput,
)
from src.services.invocation_types import (
    InvocationError,
    StructuredRequest,
    TextRequest,
)
from src.services.role_output_contracts import (
    MentorOutput,
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
        context = dict(
            messages=ANALYSIS_MESSAGES,
            labels=LABELS,
            classification_enabled=True,
        )
        evidence = [
            dict(message_id=m["id"], quote=m["content"])
            for m in ANALYSIS_MESSAGES
        ]
        return [
            (
                "unified",
                "structured",
                request(
                    "단일 v2 사후 분석입니다. schema_version=2. 모든 교사 메시지를 분류하세요. "
                    "A(high)=생각 탐색, B(low)=정답 유도. confidence는 금지합니다. 인용은 정확한 원문, "
                    "quote/alternative_question은 200자, 이유·관찰·총평은 300자 이내입니다. "
                    "총평은 1–3개, 강점/개선점 각 최대 5개, 관찰/coaching 각 최대 10개입니다. "
                    "관찰은 학생 근거를 사용하며 changed는 서로 다른 학생 메시지 2개 이상입니다.",
                    json.dumps(ANALYSIS_MESSAGES, ensure_ascii=False),
                    UnifiedAnalysisOutput,
                    context,
                ),
            ),
            (
                "merge",
                "structured",
                request(
                    "v2 종합 계약 시험입니다. schema_version=2, 분류 배열은 반환하지 마세요. "
                    "입력의 검증된 근거 부분집합만 참조하세요. 새 인용/ID를 만들지 마세요. "
                    "quote/alternative_question은 200자, 이유·관찰·총평은 300자 이내입니다. "
                    "총평 1–3개, 강점/개선점 각 최대 5개, 관찰/coaching 각 최대 10개입니다.",
                    json.dumps(
                        dict(validated_evidence=evidence), ensure_ascii=False
                    ),
                    MergeAnalysisOutput,
                    {**context, "allowed_evidence": evidence},
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
