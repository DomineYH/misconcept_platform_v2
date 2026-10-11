"""Provider-neutral S4 structure and exact evidence validation."""

import pytest
from s4_analysis_fixtures import analysis_reply

from src.services.analysis_output_contract import UnifiedAnalysisOutput
from src.services.invocation_types import CallEvent, StructuredRequest
from src.services.structured_output import strict_schema, structured_event


def test_analysis_probe_uses_unified_and_merge_contracts_only():
    from src.services.role_probe_contract import probe_steps

    steps = probe_steps("openai", "gpt-5-mini", "analysis", {}, "request")
    assert [step for step, _, _ in steps] == ["unified", "merge"]
    assert [request.output_schema.__name__ for _, _, request in steps] == [
        "UnifiedAnalysisOutput",
        "MergeAnalysisOutput",
    ]


def test_unified_contract_accepts_complete_teacher_only_feedback():
    import json

    messages = [dict(id=100, role="teacher", content="Why?")]
    request = StructuredRequest(
        "openai",
        "gpt-5-mini",
        "analysis",
        "",
        [],
        {},
        "request",
        UnifiedAnalysisOutput,
        dict(
            messages=messages, labels={"A": "high"}, classification_enabled=True
        ),
    )
    result = structured_event(
        request,
        CallEvent("completed", text=json.dumps(analysis_reply(messages))),
    )
    assert result.type == "completed"
    assert result.structured["message_classifications"][0]["rubric_id"] == "A"
    assert strict_schema(UnifiedAnalysisOutput)["additionalProperties"] is False


def test_invalid_evidence_is_rejected_but_usable_feedback_survives():
    import json

    messages = [dict(id=100, role="teacher", content="Why?")]
    value = analysis_reply(messages)
    value["strengths"] = [dict(message_id=100, quote="Invented", reason="bad")]
    context = dict(
        messages=messages, labels={"A": "high"}, classification_enabled=True
    )
    request = StructuredRequest(
        "openai",
        "gpt-5-mini",
        "analysis",
        "",
        [],
        {},
        "request",
        UnifiedAnalysisOutput,
        context,
    )
    result = structured_event(
        request, CallEvent("completed", text=json.dumps(value))
    )
    assert result.type == "error" and result.error_code == "invalid_reference"
    assert context["validated_analysis"]["strengths"] == []
    assert context["validated_analysis"]["brief_feedback"] == ["Good question"]


def contract_event(value, *, schema=UnifiedAnalysisOutput, **settings):
    import json

    context = dict(
        messages=[
            dict(id=100, role="teacher", content="Why?  학생"),
            dict(id=101, role="student", content="그냥 덧셈이니까요."),
            dict(id=102, role="student", content="통분이 필요해요."),
            dict(id=103, role="teacher", content="다시 설명할래?"),
        ],
        labels={"A": "high"},
        classification_enabled=True,
    )
    context.update(settings)
    request = StructuredRequest(
        "openai",
        "gpt-5-mini",
        "analysis",
        "",
        [],
        {},
        "request",
        schema,
        context,
    )
    return (
        structured_event(
            request, CallEvent("completed", text=json.dumps(value))
        ),
        context,
    )


def contract_value():
    return analysis_reply(
        [
            dict(id=100, role="teacher", content="Why?  학생"),
            dict(id=103, role="teacher", content="다시 설명할래?"),
        ]
    )


@pytest.mark.parametrize(
    "change,code",
    [
        ({"schema_version": True}, "invalid_output"),
        ({"schema_version": 2.0}, "invalid_output"),
        ({"schema_version": "2"}, "invalid_output"),
        ({"schema_version": 1}, "invalid_output"),
        ({"confidence": 0.9}, "invalid_output"),
        ({"strengths": None}, "invalid_output"),
        ({"brief_feedback": []}, "empty_response"),
        ({"brief_feedback": [" "]}, "empty_response"),
        ({"brief_feedback": ["good"] * 4}, "invalid_output"),
        ({"brief_feedback": ["가" * 301]}, "invalid_output"),
    ],
)
def test_damaged_envelope_has_no_usable_report(change, code):
    value = {**contract_value(), **change}
    event, context = contract_event(value)
    assert event.type == "error" and event.error_code == code
    assert "validated_analysis" not in context


@pytest.mark.parametrize(
    "change,code",
    [
        ({"message_id": True}, "invalid_output"),
        ({"message_id": "100"}, "invalid_output"),
        ({"message_id": 100.0}, "invalid_output"),
        ({"message_id": 999}, "invalid_reference"),
        (
            {"message_id": 101, "quote": "그냥 덧셈이니까요."},
            "invalid_reference",
        ),
        ({"rubric_id": "invented"}, "invalid_reference"),
        ({"rubric_id": None}, "invalid_reference"),
        ({"disposition": "non_analyzable"}, "invalid_reference"),
        ({"disposition": "unknown"}, "invalid_output"),
        ({"quote": "Why? 학생"}, "invalid_reference"),
        ({"quote": "invented"}, "invalid_reference"),
        ({"quote": " "}, "invalid_output"),
        ({"quote": "가" * 201}, "invalid_output"),
        ({"reason": " "}, "invalid_output"),
        ({"reason": "가" * 301}, "invalid_output"),
        ({"confidence": 0.9}, "invalid_output"),
    ],
)
def test_invalid_classification_never_becomes_a_normal_result(change, code):
    value = contract_value()
    value["message_classifications"][0].update(change)
    event, context = contract_event(value)
    assert event.type == "error" and event.error_code == code
    assert [
        item["message_id"]
        for item in context["validated_analysis"]["message_classifications"]
    ] == [103]


def test_all_duplicate_classifications_are_invalidated_without_losing_other_ids():
    value = contract_value()
    value["message_classifications"].append(
        dict(value["message_classifications"][0])
    )
    event, context = contract_event(value)
    assert event.error_code == "invalid_reference"
    assert [
        item["message_id"]
        for item in context["validated_analysis"]["message_classifications"]
    ] == [103]
    assert set(context["invalid_message_ids"]) == {100}


@pytest.mark.parametrize(
    "evidence",
    [
        [dict(message_id=101, quote="그냥 덧셈이니까요.")],
        [
            dict(message_id=102, quote="통분이 필요해요."),
            dict(message_id=101, quote="그냥 덧셈이니까요."),
        ],
        [dict(message_id=101, quote="그냥 덧셈이니까요.")] * 2,
        [
            dict(message_id=100, quote="Why?"),
            dict(message_id=102, quote="통분이 필요해요."),
        ],
    ],
)
def test_changed_finding_requires_distinct_ordered_student_evidence(evidence):
    value = contract_value()
    value["misconception_findings"] = [
        dict(kind="changed", claim="관찰된 변화", evidence=evidence)
    ]
    event, context = contract_event(value)
    assert event.error_code == "invalid_reference"
    assert context["validated_analysis"]["misconception_findings"] == []


@pytest.mark.parametrize(
    "section,limit,item",
    [
        ("strengths", 5, dict(message_id=100, quote="Why?", reason="good")),
        (
            "improvements",
            5,
            dict(
                message_id=101,
                quote="그냥 덧셈이니까요.",
                missed_reason="missed",
                alternative_question="Why?",
                alternative_reason="explore",
            ),
        ),
        (
            "dialogue_coaching",
            10,
            dict(
                message_id=101,
                role="student",
                marker="key_clue",
                quote="그냥 덧셈이니까요.",
                note="clue",
            ),
        ),
        (
            "misconception_findings",
            10,
            dict(
                kind="maintained",
                claim="관찰",
                evidence=[dict(message_id=101, quote="그냥 덧셈이니까요.")],
            ),
        ),
    ],
)
def test_section_limit_invalidates_whole_section_without_truncation(
    section, limit, item
):
    value = contract_value()
    value[section] = [item] * limit
    event, _ = contract_event(value)
    assert event.type == "completed"
    value[section].append(item)
    event, context = contract_event(value)
    assert event.error_code == "invalid_output"
    assert context["validated_analysis"][section] == []


def test_merge_cannot_create_new_quotes_even_when_they_are_verbatim():
    from src.services.analysis_output_contract import MergeAnalysisOutput

    value = contract_value()
    del value["message_classifications"]
    value["strengths"] = [dict(message_id=100, quote="Why?", reason="good")]
    event, _ = contract_event(
        value,
        schema=MergeAnalysisOutput,
        allowed_evidence=[dict(message_id=100, quote="Why?  학생")],
    )
    assert event.type == "error" and event.error_code == "invalid_reference"
    value["strengths"][0]["quote"] = "Why?  학생"
    event, _ = contract_event(
        value,
        schema=MergeAnalysisOutput,
        allowed_evidence=[dict(message_id=100, quote="Why?  학생")],
    )
    assert event.type == "completed"


def test_unicode_text_limits_preserve_exact_quotes_and_do_not_clamp():
    value = analysis_reply([dict(id=100, role="teacher", content="가" * 200)])
    value["message_classifications"][0]["reason"] = "나" * 300
    value["brief_feedback"] = ["다" * 300] * 3
    value["improvements"] = [
        dict(
            message_id=100,
            quote="가" * 200,
            missed_reason="라" * 300,
            alternative_question="마" * 200,
            alternative_reason="바" * 300,
        )
    ]
    request = StructuredRequest(
        "openai",
        "gpt-5-mini",
        "analysis",
        "",
        [],
        {},
        "request",
        UnifiedAnalysisOutput,
        dict(
            messages=[dict(id=100, role="teacher", content="가" * 200)],
            labels={"A": "high"},
            classification_enabled=True,
        ),
    )
    import json

    event = structured_event(
        request, CallEvent("completed", text=json.dumps(value))
    )
    assert event.type == "completed" and event.structured == value


def test_classification_off_discards_unrequested_rubric_output_as_a_violation():
    event, context = contract_event(
        contract_value(), classification_enabled=False
    )
    assert event.type == "error" and event.error_code == "invalid_output"
    assert context["validated_analysis"]["message_classifications"] == []


def test_invalid_feedback_item_does_not_discard_other_valid_feedback():
    value = contract_value()
    value["brief_feedback"] = ["검증된 총평", None, " "]
    event, context = contract_event(value)
    assert event.type == "error" and event.error_code == "invalid_output"
    assert context["validated_analysis"]["brief_feedback"] == ["검증된 총평"]
